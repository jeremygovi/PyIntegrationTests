"""Adapters import optional SDKs only at their execution boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import Context
    from ..registry import Registry


def register_builtins(registry: Registry) -> None:
    from ..helpers import artifacts, jwt
    from . import aws, cloudflare, dns, helm, http, kubernetes

    for adapter in (artifacts, jwt, aws, cloudflare, dns, helm, http, kubernetes):
        adapter.register(registry)


def provider_identity(context: Context, name: str) -> dict[str, Any]:
    from . import aws, cloudflare, helm, kubernetes

    provider = context.config.providers[name]
    match provider.kind:
        case "aws":
            return aws.identity(context, name)
        case "kubernetes":
            return kubernetes.identity(context, name)
        case "helm":
            return helm.identity(context, name)
        case "cloudflare":
            return cloudflare.identity(context, name)
        case _:
            return provider.model_dump()
