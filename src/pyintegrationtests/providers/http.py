"""HTTP requests with bounded bodies and explicit redirect/TLS settings."""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import Field

from ..config import HTTPConfig
from ..context import Context
from ..contracts import Action, ActionResult, Effect
from ..errors import OperationError, ValidationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class HTTPParams(Model):
    method: Literal["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: str | None = None
    json_body: Any = None
    follow_redirects: bool = False
    raise_for_status: bool = False


def effect(params: dict[str, Any], config: Any) -> Effect:
    return "read" if params.get("method", "GET") in {"GET", "HEAD", "OPTIONS"} else "mutate"


def request(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    assert provider is not None
    config = context.config.providers[provider]
    assert isinstance(config, HTTPConfig)
    client = context.clients.get(provider)
    if client is None:
        verify = (
            ssl.create_default_context(cafile=config.verify)
            if isinstance(config.verify, str)
            else config.verify
        )
        client = httpx.Client(
            base_url=config.base_url or "",
            verify=verify,
            proxy=config.proxy,
            trust_env=config.trust_env,
            timeout=timeout,
        )
        context.clients[provider] = client
    context.redactor.register(
        {k: v for k, v in params["headers"].items() if k.lower() in {"authorization", "cookie"}}
    )
    try:
        with client.stream(
            params["method"],
            params["url"],
            headers=params["headers"],
            params=params["query"],
            content=params["body"],
            json=params["json_body"],
            follow_redirects=params["follow_redirects"],
            timeout=timeout,
        ) as response:
            raw = bytearray()
            for chunk in response.iter_bytes():
                context.remaining()
                raw.extend(chunk)
                if len(raw) > context.config.artifacts.max_bytes:
                    raise ValidationError("HTTP body exceeds configured artifact byte limit")
            status = response.status_code
            if params["raise_for_status"] and status >= 400:
                raise OperationError(
                    "http",
                    str(status),
                    status,
                    retryable=status in {429, 502, 503, 504},
                    absent=status == 404,
                )
            body = bytes(raw).decode(response.encoding or "utf-8", errors="replace")
            import json

            try:
                decoded = json.loads(body)
            except ValueError:
                decoded = None
            return ActionResult(
                {
                    "status": status,
                    "headers": dict(response.headers),
                    "body": body,
                    "json": decoded,
                    "url": str(response.url),
                },
                sensitive=True,
            )
    except httpx.TimeoutException:
        raise OperationError("http", "Timeout", retryable=True) from None
    except httpx.RequestError:
        raise OperationError("http", "ConnectionError", retryable=True) from None


def register(registry: Registry) -> None:
    registry.register(Action("http.request", HTTPParams, request, effect, "http", sensitive=True))
