from decimal import Decimal

import pytest

from pyintegrationtests.assertions import matches
from pyintegrationtests.errors import SelectionError
from pyintegrationtests.schema import Predicate, Transform
from pyintegrationtests.selection import compile_path, transform


@pytest.mark.parametrize(
    "actual,op,expected,result",
    [
        (None, "isNull", None, True),
        (0, "isNotNull", None, True),
        (False, "isEmpty", None, False),
        (0, "isEmpty", None, False),
        (None, "isEmpty", None, False),
        ([], "isEmpty", None, True),
        ({}, "isEmpty", None, True),
        ("", "isEmpty", None, True),
        ([], "isNotEmpty", None, False),
        ("a", "isNotEmpty", None, True),
        (1, "notEqual", 2, True),
        (1, "notEqual", 1, False),
        ([1, 2], "lengthEqual", 2, True),
        ([1], "lengthEqual", True, False),
        ([True], "contains", 1, False),
        ([1, 2], "contains", 2, True),
        ("hello", "contains", "ell", True),
        ({"a": 1}, "contains", "a", True),
        ([1], "notContains", 2, True),
        ([1], "notContains", 1, False),
        ({"a": 1}, "isSubset", {"a": 1, "b": 2}, True),
        ([{"a": 1}], "isSubset", [{"a": 1, "b": 2}], True),
        ([1, 1], "isSubset", [1, 2], False),
        ([1, 1], "isNotSubset", [1, 2], True),
        (1, "isType", "integer", True),
        (True, "isType", "integer", False),
        (False, "isType", "boolean", True),
        (1.0, "isType", "number", True),
        (1, "greater", 0, True),
        (1, "greaterOrEqual", 1, True),
        (1, "less", 2, True),
        (1, "lessOrEqual", 1, True),
        ("abc123", "matchRegex", r"^abc\d+$", True),
        ("abc", "matchRegex", r"^\d+$", False),
    ],
)
def test_comparator_truth_table(actual, op, expected, result):
    assert matches(actual, Predicate(op=op, value=expected)) is result


def test_composition_default_and_multiset():
    assert matches(
        2,
        Predicate(
            op="allOf", checks=[Predicate(op="greater", value=1), Predicate(op="less", value=3)]
        ),
    )
    assert matches(2, Predicate(op="not", checks=[Predicate(op="equal", value=3)]))
    assert matches({}, Predicate(path="$.rcu", op="equal", value=0, default=0))
    assert matches([1, 1, 2], Predicate(op="equal", value=[2, 1, 1], order="multiset"))


@pytest.mark.parametrize(
    "actual,op,value,kwargs",
    [
        (True, "greater", 0, {}),
        (1, "contains", 1, {}),
        (1, "equal", 1, {"order": "set"}),
        ("a", "matchRegex", "[", {}),
        (3, "isType", "invented", {}),
    ],
)
def test_invalid_operations_are_errors_not_false(actual, op, value, kwargs):
    with pytest.raises(SelectionError):
        matches(actual, Predicate(op=op, value=value, **kwargs))


@pytest.mark.parametrize(
    "name,value,result",
    [
        ("sort", [2, 1], [1, 2]),
        ("lower", "A", "a"),
        ("stripTrailingDot", "dns.example.", "dns.example"),
        ("decimal", "0.10000000000000001", Decimal("0.10000000000000001")),
    ],
)
def test_explicit_transforms(name, value, result):
    assert transform(value, [Transform(name=name)]) == result


@pytest.mark.parametrize("path", ["no-root", "$[", "$..value", "$ + 1", "$[?(@.x =~ 'a')]"])
def test_invalid_or_unsupported_jsonpaths(path):
    with pytest.raises(SelectionError):
        compile_path(path)
