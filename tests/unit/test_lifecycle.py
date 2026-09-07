import json
import stat

import pytest

from pyintegrationtests.cleanup import cleanup, prepare
from pyintegrationtests.contracts import Action, ActionResult
from pyintegrationtests.engine import execute_case, invoke
from pyintegrationtests.errors import (
    AssertionFailure,
    OperationError,
    OwnershipError,
    ValidationError,
)
from pyintegrationtests.journal import Journal
from pyintegrationtests.schema import Case, Model, Observation, Resource


class Params(Model):
    name: str


@pytest.fixture
def lifecycle(context):
    state, calls = {}, []

    def read(ctx, provider, params, timeout):
        calls.append(("read", params["name"]))
        return ActionResult(state.get(params["name"]))

    def create(ctx, provider, params, timeout):
        persisted = json.loads((ctx.directory / "journal.json").read_text())
        assert persisted["entries"][-1]["state"] == "intent"
        state[params["name"]] = {"name": params["name"], "owner": ctx.run_id}
        calls.append(("create", params["name"]))
        return ActionResult(state[params["name"]])

    def delete(ctx, provider, params, timeout):
        calls.append(("delete", params["name"]))
        state.pop(params["name"], None)
        return ActionResult({})

    context.registry.register(Action("fake.read", Params, read, "read"))
    context.registry.register(Action("fake.create", Params, create, "mutate"))
    context.registry.register(Action("fake.delete", Params, delete, "mutate"))
    context.prepare_directory()
    return state, calls, Journal.create(context)


def resource(name="fixture", after=()):
    return Resource.model_validate(
        {
            "id": name,
            "probe": {"action": "fake.read", "with": {"name": name}},
            "absent": [{"op": "isNull"}],
            "owned": [
                {
                    "path": "$.owner",
                    "op": "equal",
                    "value": {"$ref": {"source": "run", "path": "$.id"}},
                }
            ],
            "cleanup": {
                "action": "fake.delete",
                "with": {"name": {"$ref": {"source": "resource.data", "path": "$.name"}}},
            },
            "after": list(after),
        }
    )


def case(spec=None, tail=()):
    spec = spec or resource()
    return Case.model_validate(
        {
            "id": "case",
            "steps": [
                {
                    "kind": "action",
                    "id": "create",
                    "action": "fake.create",
                    "with": {"name": spec.id},
                    "creates": spec.model_dump(by_alias=True),
                },
                *tail,
            ],
        }
    )


def test_intention_precedes_mutation_and_cleanup_is_replayable(context, lifecycle):
    state, calls, journal = lifecycle
    execute_case(context, journal, case())
    assert state
    assert cleanup(context, journal) == []
    assert not state
    replay = Journal.load(journal.path, context.config)
    assert cleanup(context, replay) == []
    assert calls.count(("delete", "fixture")) == 1
    assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(journal.path.parent.stat().st_mode) == 0o700


def test_preexisting_and_replaced_resources_are_protected(context, lifecycle):
    state, calls, journal = lifecycle
    state["fixture"] = {"owner": "external"}
    with pytest.raises(OwnershipError, match="already exists"):
        execute_case(context, journal, case())
    assert not journal.data.entries
    state.clear()
    execute_case(context, journal, case())
    state["fixture"]["owner"] = "someone-else"
    assert cleanup(context, journal)
    assert ("delete", "fixture") not in calls
    assert journal.data.entries[0].state == "unknown"


def test_timeout_after_creation_uses_exact_probe(context, lifecycle):
    state, _calls, journal = lifecycle
    original = context.registry.get("fake.create")

    def lost_response(*args):
        original.execute(*args)
        raise OperationError("fake", "Timeout")

    context.registry.actions["fake.create"] = Action("fake.create", Params, lost_response, "mutate")
    with pytest.raises(OperationError):
        execute_case(context, journal, case())
    assert journal.data.entries[0].state == "intent"
    assert cleanup(context, journal) == []
    assert not state


def test_tracks_only_mutation_marks_resource_created(context, lifecycle):
    state, calls, journal = lifecycle
    tracked_case = Case.model_validate(
        {
            "id": "tracked",
            "steps": [
                {
                    "kind": "action",
                    "id": "create",
                    "action": "fake.create",
                    "with": {"name": "fixture"},
                    "tracks": [resource().model_dump(by_alias=True)],
                }
            ],
        }
    )
    execute_case(context, journal, tracked_case)
    assert journal.data.entries[0].state == "created"
    assert cleanup(context, journal) == []
    assert not state
    assert ("delete", "fixture") in calls


def test_cleanup_continues_independently_and_honors_dependencies(context, lifecycle):
    state, calls, journal = lifecycle
    for name, after in (("a", ("b",)), ("b", ()), ("c", ())):
        execute_case(context, journal, case(resource(name, after)))
    state["c"]["owner"] = "wrong"
    assert len(cleanup(context, journal)) == 1
    deleted = [name for op, name in calls if op == "delete"]
    assert deleted == ["b", "a"]


def test_manual_delete_is_idempotent(context, lifecycle):
    state, _, journal = lifecycle
    execute_case(context, journal, case())
    state.clear()
    assert cleanup(context, journal) == []


def test_original_assertion_and_cleanup_failure_are_preserved(context, lifecycle):
    state, _, journal = lifecycle
    with pytest.raises(AssertionFailure):
        execute_case(
            context,
            journal,
            case(
                tail=[
                    {
                        "kind": "assert",
                        "id": "fail",
                        "source": "steps.create.data",
                        "assertions": [{"path": "$.name", "op": "equal", "value": "wrong"}],
                    }
                ]
            ),
        )
    state["fixture"]["owner"] = "wrong"
    assert cleanup(context, journal)
    assert context.trace[-1]["status"] == "failed"


def test_cleanup_rejects_changed_target_and_secrets(context, lifecycle):
    _, _, journal = lifecycle
    execute_case(context, journal, case())
    config = context.config.model_copy(update={"plugins": ["different"]})
    with pytest.raises(ValidationError, match="differs"):
        Journal.load(journal.path, config)
    spec = resource("secret-name")
    context.redactor.register("secret-name")
    with pytest.raises(ValidationError, match="sensitive"):
        prepare(context, journal, spec)


def test_ownership_requires_positive_run_proof(context, lifecycle):
    _, _, journal = lifecycle
    spec = resource().model_copy(update={"owned": resource().absent})
    with pytest.raises(OwnershipError, match="run ID"):
        prepare(context, journal, spec)


@pytest.mark.parametrize(
    "code,status,accepted",
    [("AccessDenied", 403, True), ("Timeout", None, False), ("NoSuchEntity", 404, False)],
)
def test_expected_error_is_precise(context, lifecycle, code, status, accepted):
    _, _, journal = lifecycle

    def failure(*args):
        raise OperationError("fake", code, status)

    context.registry.register(Action("fake.error", Params, failure, "read"))
    negative = Case.model_validate(
        {
            "id": "negative",
            "steps": [
                {
                    "kind": "action",
                    "id": "check",
                    "action": "fake.error",
                    "with": {"name": "x"},
                    "expectedError": {"provider": "fake", "code": "AccessDenied", "status": 403},
                }
            ],
        }
    )
    if accepted:
        execute_case(context, journal, negative)
    else:
        with pytest.raises(OperationError):
            execute_case(context, journal, negative)


def test_not_found_is_distinct_from_forbidden(context, lifecycle):
    def failure(*args):
        raise OperationError("fake", "403", 403)

    context.registry.register(Action("fake.forbidden", Params, failure, "read"))
    with pytest.raises(OperationError):
        invoke(
            context,
            Observation(action="fake.forbidden", params={"name": "x"}, not_found="null"),
            1,
            observation=True,
        )
