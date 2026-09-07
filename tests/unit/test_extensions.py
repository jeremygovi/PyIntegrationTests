from types import SimpleNamespace

import pytest

from pyintegrationtests.assertions import matches
from pyintegrationtests.contracts import Action, ActionResult
from pyintegrationtests.errors import SelectionError, ValidationError
from pyintegrationtests.registry import Registry, validate_dependencies
from pyintegrationtests.schema import Model, Predicate, Resource


def test_extensions_require_unique_explicit_entry_points(monkeypatch):
    calls = []

    def register(registry):
        calls.append("loaded")
        registry.register(Action("fixture.read", Model, lambda *args: ActionResult({}), "read"))
        registry.register_assertion("fixture.same-length", lambda a, b: len(a) == len(b))

    entry = SimpleNamespace(name="fixture", load=lambda: register)
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kwargs: [entry])
    registry = Registry()
    registry.load_plugins([])
    assert calls == []
    registry.load_plugins(["fixture"])
    assert matches(
        "ab",
        Predicate(op="custom", assertion="fixture.same-length", value="xy"),
        registry.assertions,
    )
    with pytest.raises(ValidationError, match="Duplicate"):
        registry.register(registry.get("fixture.read"))
    with pytest.raises(ValidationError, match="Duplicate"):
        registry.register_assertion("fixture.same-length", lambda a, b: True)
    with pytest.raises(ValidationError, match="exactly one"):
        registry.load_plugins(["not-installed"])


def test_plugin_errors_and_custom_comparator_errors_do_not_disclose_payloads(monkeypatch):
    def error(*args):
        raise RuntimeError("SECRET")

    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda **kwargs: [SimpleNamespace(name="broken", load=lambda: error)],
    )
    with pytest.raises(ValidationError) as exc:
        Registry().load_plugins(["broken"])
    assert "SECRET" not in str(exc.value)
    with pytest.raises(SelectionError) as exc:
        matches("secret", Predicate(op="custom", assertion="broken", value="x"), {"broken": error})
    assert "SECRET" not in str(exc.value)


def test_cleanup_cycles_are_rejected_before_execution():
    def resource(name, after):
        return Resource.model_validate(
            {
                "id": name,
                "probe": {"action": "fixture.read"},
                "absent": [{"op": "isNull"}],
                "owned": [{"op": "exists"}],
                "cleanup": {"action": "fixture.delete"},
                "after": after,
            }
        )

    with pytest.raises(ValidationError, match="cycle"):
        validate_dependencies({"a": resource("a", ["b"]), "b": resource("b", ["a"])})
    with pytest.raises(ValidationError, match="Unknown"):
        validate_dependencies({"a": resource("a", ["missing"])})
    validate_dependencies({"a": resource("a", ["missing"])}, partial=True)
