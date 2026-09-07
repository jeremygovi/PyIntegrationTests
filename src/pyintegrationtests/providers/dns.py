"""DNS observations preserve NXDOMAIN, NODATA, TTLs and record types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import DNSConfig
from ..context import Context
from ..contracts import Action, ActionResult
from ..errors import OperationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class DNSParams(Model):
    name: str
    record_type: str = "A"


def query(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    import dns.exception
    import dns.resolver

    assert provider is not None
    config = context.config.providers[provider]
    assert isinstance(config, DNSConfig)
    resolver = context.clients.get(provider)
    if resolver is None:
        resolver = dns.resolver.Resolver(configure=not bool(config.nameservers))
        if config.nameservers:
            resolver.nameservers = config.nameservers
        context.clients[provider] = resolver
    try:
        answer = resolver.resolve(
            params["name"], params["record_type"], lifetime=timeout, raise_on_no_answer=False
        )
        return ActionResult(
            {
                "name": str(answer.qname),
                "canonicalName": str(answer.canonical_name),
                "type": params["record_type"],
                "status": "ANSWER" if answer.rrset else "NODATA",
                "ttl": answer.rrset.ttl if answer.rrset else None,
                "records": [record.to_text() for record in answer],
                "response": answer.response.to_text(),
            }
        )
    except dns.resolver.NXDOMAIN:
        return ActionResult(
            {
                "name": params["name"],
                "type": params["record_type"],
                "status": "NXDOMAIN",
                "records": [],
            }
        )
    except dns.exception.Timeout:
        raise OperationError("dns", "Timeout", retryable=True) from None
    except dns.resolver.NoNameservers:
        raise OperationError("dns", "NoNameservers", retryable=True) from None


def register(registry: Registry) -> None:
    registry.register(Action("dns.query", DNSParams, query, "read", "dns"))
