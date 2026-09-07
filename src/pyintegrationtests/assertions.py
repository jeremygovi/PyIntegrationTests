"""Provider-independent comparisons with explicit presence and collection semantics."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .contracts import Assertion
from .errors import AssertionFailure, SelectionError
from .schema import Predicate
from .selection import MISSING, select, transform


def equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equal(v, right[k]) for k, v in left.items())
    return bool(left == right)


def subset(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return all(k in right and subset(v, right[k]) for k, v in left.items())
    if isinstance(left, list) and isinstance(right, list):
        available = list(right)
        for item in left:
            match = next((i for i, v in enumerate(available) if subset(item, v)), None)
            if match is None:
                return False
            available.pop(match)
        return True
    return equal(left, right)


def _collection_equal(actual: Any, expected: Any, order: str) -> bool:
    if order == "ordered":
        return equal(actual, expected)
    if not isinstance(actual, list) or not isinstance(expected, list):
        raise SelectionError("Unordered equality requires two lists")
    a = [json.dumps(v, sort_keys=True, separators=(",", ":")) for v in actual]
    b = [json.dumps(v, sort_keys=True, separators=(",", ":")) for v in expected]
    return set(a) == set(b) if order == "set" else Counter(a) == Counter(b)


def compare(
    actual: Any, predicate: Predicate, custom: Mapping[str, Assertion] | None = None
) -> bool:
    op, expected = predicate.op, predicate.value
    if op == "exists":
        return actual is not MISSING
    if op == "notExists":
        return actual is MISSING
    if actual is MISSING:
        return False
    actual = transform(actual, predicate.transforms)
    if op == "custom":
        comparator = (custom or {}).get(predicate.assertion or "")
        if comparator is None:
            raise SelectionError("Unknown custom assertion")
        try:
            result = comparator(actual, expected)
        except Exception:
            raise SelectionError("Custom assertion raised an exception") from None
        if type(result) is not bool:
            raise SelectionError("Custom assertion must return bool")
        return result
    if op == "allOf":
        # Evaluate all branches so a bad selector cannot hide behind short circuiting.
        return all([matches(actual, p, custom) for p in predicate.checks])
    if op == "anyOf":
        return any([matches(actual, p, custom) for p in predicate.checks])
    if op == "not":
        return not matches(actual, predicate.checks[0], custom)
    if op == "equal":
        return _collection_equal(actual, expected, predicate.order)
    if op == "notEqual":
        return not _collection_equal(actual, expected, predicate.order)
    if op == "isNull":
        return actual is None
    if op == "isNotNull":
        return actual is not None
    if op in {"isEmpty", "isNotEmpty"}:
        return isinstance(actual, str | list | dict) and ((len(actual) == 0) == (op == "isEmpty"))
    if op == "isType":
        types: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float, Decimal),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        if expected not in types:
            raise SelectionError("Unknown JSON type")
        return isinstance(actual, types[expected]) and not (
            expected in {"integer", "number"} and isinstance(actual, bool)
        )
    if op == "lengthEqual":
        return (
            isinstance(actual, str | list | dict)
            and type(expected) is int
            and len(actual) == expected
        )
    if op in {"contains", "notContains"}:
        if isinstance(actual, str) and isinstance(expected, str):
            contained = expected in actual
        elif isinstance(actual, list):
            contained = any(equal(expected, item) for item in actual)
        elif isinstance(actual, dict) and isinstance(expected, str):
            contained = expected in actual
        else:
            raise SelectionError("contains requires a compatible container and operand")
        return contained if op == "contains" else not contained
    if op in {"isSubset", "isNotSubset"}:
        result = subset(actual, expected)
        return result if op == "isSubset" else not result
    if op in {"greater", "greaterOrEqual", "less", "lessOrEqual"}:
        if any(
            isinstance(x, bool) or not isinstance(x, int | float | Decimal)
            for x in (actual, expected)
        ):
            raise SelectionError("Numeric comparison requires numeric operands")
        return bool(
            {
                "greater": actual > expected,
                "greaterOrEqual": actual >= expected,
                "less": actual < expected,
                "lessOrEqual": actual <= expected,
            }[op]
        )
    if op == "matchRegex":
        if not isinstance(actual, str) or not isinstance(expected, str) or len(expected) > 512:
            raise SelectionError("matchRegex requires text and a pattern of at most 512 characters")
        try:
            return re.search(expected, actual[:65536]) is not None
        except re.error:
            raise SelectionError("Invalid regular expression") from None
    raise SelectionError("Unknown comparator")


def matches(data: Any, predicate: Predicate, custom: Mapping[str, Assertion] | None = None) -> bool:
    values = select(data, predicate.path)
    if predicate.quantifier == "scalar":
        if len(values) > 1:
            raise SelectionError("Multiple matches require an explicit quantifier")
        actual = (
            values[0]
            if values
            else (predicate.default if "default" in predicate.model_fields_set else MISSING)
        )
        return compare(actual, predicate, custom)
    if not values:
        return predicate.allow_empty
    results = [compare(value, predicate, custom) for value in values]
    return any(results) if predicate.quantifier == "any" else all(results)


def check(
    data: Any, predicates: list[Predicate], custom: Mapping[str, Assertion] | None = None
) -> None:
    results = [matches(data, p, custom) for p in predicates]
    for index, (predicate, passed) in enumerate(zip(predicates, results, strict=True)):
        if not passed:
            # Values deliberately stay in memory; diagnostics disclose paths, not payloads.
            raise AssertionFailure(f"Assertion {index + 1} failed: {predicate.path} {predicate.op}")
