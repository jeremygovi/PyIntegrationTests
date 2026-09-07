"""Immutable results and contracts shared by adapters and trusted extensions."""

from __future__ import annotations

import base64
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel

from .errors import ValidationError

if TYPE_CHECKING:
    from .context import Context

type Effect = Literal["read", "mutate", "local"]


def normalize(value: Any) -> Any:
    """Convert SDK values without rounding decimals or implicitly consuming streams."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Non-finite response number")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(v) for v in value]
    if isinstance(value, BaseModel):
        return normalize(value.model_dump(mode="python", exclude_unset=True))
    raise ValidationError(f"Unsupported response type: {type(value).__name__}")


def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True, init=False)
class ActionResult:
    data: Any
    meta: Mapping[str, Any]
    sensitive: bool

    def __init__(
        self, data: Any, meta: Mapping[str, Any] | None = None, sensitive: bool = False
    ) -> None:
        object.__setattr__(self, "data", freeze(normalize(data)))
        object.__setattr__(self, "meta", freeze(normalize(dict(meta or {}))))
        object.__setattr__(self, "sensitive", sensitive)


class ActionHandler(Protocol):
    def __call__(
        self, context: Context, provider: str | None, params: dict[str, Any], timeout: float
    ) -> ActionResult: ...


@dataclass(frozen=True)
class Action:
    name: str
    parameters: type[BaseModel]
    execute: ActionHandler
    effect: Effect | Callable[[dict[str, Any], Any], Effect]
    provider_kind: str | None = None
    sensitive: bool = False
    validate: Callable[[dict[str, Any], Any], None] = field(default=lambda p, c: None)


class Assertion(Protocol):
    """Pure comparators return bool and perform no I/O."""

    def __call__(self, actual: Any, expected: Any) -> bool: ...
