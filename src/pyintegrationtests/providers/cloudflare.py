"""Official SDK transport with origin confinement and explicit complete pagination."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import Field

from ..config import CloudflareConfig
from ..context import Context
from ..contracts import Action, ActionResult, Effect
from ..errors import OperationError, OwnershipError, ValidationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class CloudflareParams(Model):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    pagination: Literal["page", "all"] = "page"
    max_pages: int = Field(default=100, ge=1, le=10000)


def safe_path(path: str) -> None:
    decoded = unquote(path)
    parsed = urlsplit(decoded)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in decoded
        or ".." in decoded.split("/")
        or any(ord(c) < 32 for c in decoded)
    ):
        raise ValidationError(
            "Cloudflare path must be a relative API path without traversal, query or origin"
        )


def effect(params: dict[str, Any], config: Any) -> Effect:
    return "read" if params.get("method", "GET") == "GET" else "mutate"


def validate(params: dict[str, Any], config: Any) -> None:
    if isinstance(params.get("path"), str):
        safe_path(params["path"])
    if params.get("pagination") == "all" and params.get("method", "GET") != "GET":
        raise ValidationError("Cloudflare mutations cannot be paginated")


def client(context: Context, provider: str, timeout: float) -> Any:
    from cloudflare import Cloudflare

    settings = context.config.providers[provider]
    assert isinstance(settings, CloudflareConfig)
    if provider not in context.clients:
        token = context.environment.get(settings.api_token_env)
        if not token:
            raise ValidationError("Cloudflare token environment variable is missing")
        context.redactor.register(token)
        context.clients[provider] = Cloudflare(
            api_token=token,
            base_url=settings.base_url,
            timeout=timeout,
            max_retries=0,
            http_client=httpx.Client(follow_redirects=False),
        )
    return context.clients[provider]


def raw_request(
    sdk: Any, method: str, path: str, query: dict[str, Any], body: Any, timeout: float
) -> dict[str, Any]:
    from cloudflare import APIConnectionError, APIStatusError

    safe_path(path)
    options: dict[str, Any] = {"params": query, "timeout": timeout}
    kwargs: dict[str, Any] = {"cast_to": httpx.Response, "options": options}
    if body is not None:
        kwargs["body"] = body
    try:
        response = getattr(sdk, method.lower())(path, **kwargs)
        try:
            result = response.json()
        finally:
            response.close()
        if not isinstance(result, dict):
            raise ValidationError("Cloudflare response must be a JSON object")
        if result.get("success") is False:
            errors = result.get("errors", [])
            code = str(errors[0].get("code", "Unknown")) if errors else "Unknown"
            raise OperationError("cloudflare", code, response.status_code)
        return result
    except APIStatusError as exc:
        status = exc.status_code
        code = str(status)
        try:
            errors = exc.response.json().get("errors", [])
            if errors:
                code = str(errors[0]["code"])
        except (ValueError, KeyError, TypeError):
            pass
        retry = exc.response.headers.get("retry-after", "")
        raise OperationError(
            "cloudflare",
            code,
            status,
            retryable=status in {429, 500, 502, 503, 504},
            absent=status == 404,
            retry_after=float(retry) if retry.isdigit() else None,
        ) from None
    except APIConnectionError:
        raise OperationError("cloudflare", "ConnectionError", retryable=True) from None


def identity(context: Context, provider: str) -> dict[str, Any]:
    config = context.config.providers[provider]
    assert isinstance(config, CloudflareConfig)
    response = raw_request(
        client(context, provider, min(context.remaining(), 15)),
        "GET",
        f"/zones/{config.zone_id}",
        {},
        None,
        min(context.remaining(), 15),
    )
    zone = response.get("result", {})
    if zone.get("id") != config.zone_id:
        raise OwnershipError("Cloudflare zone identity mismatch")
    return {
        "kind": "cloudflare",
        "baseUrl": config.base_url,
        "zoneId": config.zone_id,
        "accountId": zone.get("account", {}).get("id"),
    }


def request(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    assert provider is not None
    config = context.config.providers[provider]
    assert isinstance(config, CloudflareConfig)
    safe_path(params["path"])
    if params["method"] != "GET":
        prefix = f"/zones/{config.zone_id}/"
        if not params["path"].startswith(prefix):
            raise OwnershipError(
                "Cloudflare mutation must target a child of the configured external zone"
            )
    sdk = client(context, provider, timeout)
    if provider not in context.bindings:
        context.bindings[provider] = identity(context, provider)
    query = dict(params["query"])
    pages = []
    cursor_seen: set[str] = set()
    for _ in range(params["max_pages"]):
        response = raw_request(
            sdk,
            params["method"],
            params["path"],
            query,
            params["body"],
            min(timeout, context.remaining()),
        )
        if params["pagination"] == "page":
            return ActionResult(response, {"pagination": "page", "provider": provider})
        pages.append(response)
        info = response.get("result_info", {})
        cursor = info.get("cursors", {}).get("after")
        if cursor:
            if cursor in cursor_seen:
                raise ValidationError("Cloudflare pagination repeated a cursor")
            cursor_seen.add(cursor)
            query["cursor"] = cursor
        elif info.get("page", 1) < info.get("total_pages", 1):
            query["page"] = info.get("page", 1) + 1
        else:
            if "result_info" not in response:
                raise ValidationError(
                    "Cloudflare endpoint lacks supported pagination metadata; use page mode"
                )
            return ActionResult(
                {"pages": pages}, {"complete": True, "pages": len(pages), "provider": provider}
            )
    raise ValidationError("Cloudflare pagination exceeded maxPages; result is incomplete")


def register(registry: Registry) -> None:
    registry.register(
        Action(
            "cloudflare.request", CloudflareParams, request, effect, "cloudflare", validate=validate
        )
    )
