"""Bounded jsonpath-ng selectors and explicit, pure transformations."""

from __future__ import annotations

import base64
import binascii
import json
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

from jsonpath_ng.ext import parse

from .contracts import thaw
from .errors import SelectionError
from .schema import Transform


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
_NODES = {"Root", "This", "Child", "Fields", "Index", "Slice", "Filter", "Expression"}


@lru_cache(maxsize=1024)
def compile_path(path: str) -> Any:
    if len(path) > 1024 or not path.startswith("$"):
        raise SelectionError("JSONPath must start with $ and contain at most 1024 characters")
    try:
        tree = parse(path)

        def inspect(node: Any) -> None:
            if type(node).__name__ not in _NODES:
                raise SelectionError("Unsupported JSONPath construct")
            for key in ("left", "right", "target"):
                child = getattr(node, key, None)
                if child is not None:
                    inspect(child)
            for child in getattr(node, "expressions", ()):
                inspect(child)
            if getattr(node, "op", None) == "=~":
                raise SelectionError("Regex filters are unsupported; use matchRegex")

        inspect(tree)
        return tree
    except SelectionError:
        raise
    except Exception:
        raise SelectionError("Invalid JSONPath expression") from None


def select(data: Any, path: str) -> list[Any]:
    try:
        return [m.value for m in compile_path(path).find(thaw(data))]
    except SelectionError:
        raise
    except Exception:
        raise SelectionError("JSONPath cannot be applied to this response") from None


def scalar(data: Any, path: str) -> Any:
    values = select(data, path)
    if len(values) > 1:
        raise SelectionError("Scalar selection matched multiple values; specify any/all")
    return values[0] if values else MISSING


def transform(value: Any, transforms: list[Transform]) -> Any:
    for operation in transforms:
        try:
            match operation.name:
                case "decodeBase64":
                    value = base64.b64decode(value, validate=True).decode("utf-8")
                case "decodeJson":
                    value = json.loads(value)
                case "sort":
                    if not isinstance(value, list):
                        raise TypeError
                    value = sorted(value)
                case "lower":
                    value = value.lower()
                case "stripTrailingDot":
                    value = value.removesuffix(".")
                case "decimal":
                    if isinstance(value, bool | float):
                        raise TypeError
                    value = Decimal(value)
                    if not value.is_finite():
                        raise ValueError
        except (ValueError, TypeError, AttributeError, binascii.Error, InvalidOperation):
            raise SelectionError(f"Transformation {operation.name} failed") from None
    return value
