"""JWT fixture signing through PyJWT; keys never enter the DSL or journal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ..context import Context
from ..contracts import Action, ActionResult
from ..errors import ValidationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class JWTParams(Model):
    key_env: str
    algorithm: Literal["RS256", "RS384", "RS512", "ES256", "HS256"] = "RS256"
    claims: dict[str, Any]
    headers: dict[str, Any] = Field(default_factory=dict)


def sign(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    import jwt

    key = context.environment.get(params["key_env"])
    if not key:
        raise ValidationError("JWT fixture key environment variable is missing")
    context.redactor.register(key)
    token = jwt.encode(
        params["claims"], key, algorithm=params["algorithm"], headers=params["headers"]
    )
    return ActionResult({"token": token}, sensitive=True)


def register(registry: Registry) -> None:
    registry.register(Action("jwt.sign", JWTParams, sign, "local", sensitive=True))
