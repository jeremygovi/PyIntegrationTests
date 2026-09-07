import base64
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pyintegrationtests.assertions import check, matches
from pyintegrationtests.config import load_config
from pyintegrationtests.context import resolve
from pyintegrationtests.contracts import ActionResult, normalize, thaw
from pyintegrationtests.errors import AssertionFailure, SelectionError, ValidationError
from pyintegrationtests.helpers.artifacts import resource_name
from pyintegrationtests.schema import Predicate, Transform, load_document
from pyintegrationtests.selection import MISSING, compile_path, scalar, transform


@pytest.mark.parametrize("actual", [None, False, 0, "", [], {}])
def test_presence_is_not_truthiness(actual):
    assert matches({"v": actual}, Predicate(path="$.v", op="exists"))
    assert not matches({"v": actual}, Predicate(path="$.v", op="notExists"))
    assert scalar({}, "$.v") is MISSING


@pytest.mark.parametrize(
    "op", ["isNull", "isNotNull", "equal", "notEqual", "isEmpty", "isNotEmpty"]
)
def test_missing_never_passes_value_comparisons(op):
    assert not matches({}, Predicate(path="$.v", op=op, value=None))


@pytest.mark.parametrize(
    "left,right", [(True, 1), (False, 0), (1, "1"), ([True], [1]), ({"x": True}, {"x": 1})]
)
def test_strict_equality(left, right):
    assert not matches(left, Predicate(op="equal", value=right))


def test_scalar_and_quantifiers():
    data = {"items": [{"v": 1}, {"v": 2}]}
    with pytest.raises(SelectionError, match="quantifier"):
        check(data, [Predicate(path="$.items[*].v", op="greater", value=0)])
    assert matches(data, Predicate(path="$.items[*].v", op="greater", value=0, quantifier="all"))
    assert matches(data, Predicate(path="$.items[*].v", op="equal", value=2, quantifier="any"))
    assert not matches({}, Predicate(path="$.missing[*]", op="equal", value=1, quantifier="all"))
    assert matches(
        {}, Predicate(path="$.missing[*]", op="equal", value=1, quantifier="all", allow_empty=True)
    )


@pytest.mark.parametrize("order,expected", [("ordered", False), ("set", True), ("multiset", False)])
def test_order_and_duplicates(order, expected):
    assert matches([1, 1, 2], Predicate(op="equal", value=[2, 1], order=order)) is expected


def test_paths_and_transforms():
    data = {
        "metadata": {"annotations": {"example.com/check": "yes"}},
        "items": [{"name": "a"}, {"name": "b"}],
    }
    assert scalar(data, "$.metadata.annotations['example.com/check']") == "yes"
    assert scalar(data, "$.items[?(@.name == 'b')].name") == "b"
    value = base64.b64encode(b'{"v": 3}').decode()
    assert transform(value, [Transform(name="decodeBase64"), Transform(name="decodeJson")]) == {
        "v": 3
    }
    with pytest.raises(SelectionError):
        transform("invalid", [Transform(name="decodeBase64")])
    with pytest.raises(SelectionError):
        compile_path("$..password")


def test_composition_does_not_hide_selection_errors():
    predicate = Predicate(
        op="anyOf",
        checks=[
            Predicate(op="equal", value="bad"),
            Predicate(op="equal", value=1, transforms=[Transform(name="decodeJson")]),
        ],
    )
    with pytest.raises(SelectionError):
        matches("bad", predicate)


def test_immutable_complete_result_and_exact_normalization():
    source = {
        "unknown": {"values": [1]},
        "date": datetime(2026, 1, 1, tzinfo=UTC),
        "decimal": Decimal("0.100000000000000001"),
    }
    result = ActionResult(source)
    source["unknown"]["values"].append(2)
    assert thaw(result.data)["unknown"] == {"values": [1]}
    with pytest.raises(TypeError):
        result.data["unknown"]["values"][0] = 3
    assert result.data["decimal"] == "0.100000000000000001"
    assert normalize(b"abc") == {"$bytes": "YWJj"}


@pytest.mark.parametrize(
    "content",
    ["a: 1\na: 2\n", "a: &a [*a]\n", "a: !!python/object:os.system {}\n", "1: value\n", "- a\n"],
)
def test_yaml_rejects_unsafe_or_ambiguous_input(tmp_path, content):
    path = tmp_path / "suite.yaml"
    path.write_text(content)
    with pytest.raises(ValidationError):
        load_document(path)


def test_yaml_source_locations(tmp_path):
    path = tmp_path / "suite.yaml"
    path.write_text("tests:\n  - id: example\n    steps: []\n")
    document = load_document(path)
    assert document.location(("tests", 0, "steps")).endswith(":3")


def test_config_precedence_does_not_modify_process(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("timeouts:\n  caseSeconds: 10\n")
    env_file = tmp_path / ".env"
    env_file.write_text("PYINTEGRATIONTESTS_CASE_SECONDS=20\nPRIVATE_KEY=fixture\n")
    monkeypatch.setenv("PYINTEGRATIONTESTS_CASE_SECONDS", "30")
    loaded, environment = load_config(config, env_file, {"timeouts": {"caseSeconds": 40}})
    assert loaded.timeouts.case_seconds == 40
    assert environment["PRIVATE_KEY"] == "fixture"
    import os

    assert "PRIVATE_KEY" not in os.environ
    assert load_config(config, env_file)[0].timeouts.case_seconds == 30


def test_references_preserve_types_and_literal_escape():
    source = {"inputs": {"value": [1, False, None]}}
    ref = {"$ref": {"source": "inputs", "path": "$.value"}}
    assert resolve(ref, source) == [1, False, None]
    assert resolve({"$literal": {"$ref": "api field"}}, {}) == {"$ref": "api field"}
    assert (
        resolve({"$format": {"template": "record-{name}", "values": {"name": "abc"}}}, {})
        == "record-abc"
    )
    with pytest.raises(ValidationError):
        resolve({"$format": {"template": "{name.__class__}", "values": {"name": "abc"}}}, {})


def test_names_are_bounded_and_collision_resistant():
    name = resource_name("My very long prefix!" * 20, "run-a", "case", 63)
    assert len(name) == 63 and name[0].isalnum() and name[-1].isalnum()
    assert name != resource_name("My very long prefix!" * 20, "run-b", "case", 63)


def test_failure_never_embeds_values():
    with pytest.raises(AssertionFailure) as exc:
        check("secret-observed", [Predicate(op="equal", value="secret-expected")])
    assert "secret" not in str(exc.value)
