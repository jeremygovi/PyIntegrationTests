"""Exact ownership guards and bounded, resumable compensation."""

from __future__ import annotations

from typing import Any

from .assertions import check
from .context import Context
from .contracts import ActionResult
from .errors import AssertionFailure, IntegrationError, OwnershipError
from .journal import Entry, Journal
from .schema import Resource
from .waiting import wait_for


def binding(context: Context, provider: str | None) -> dict[str, Any]:
    if provider is None:
        return {"kind": "local"}
    if provider not in context.bindings:
        from .providers import provider_identity

        context.bindings[provider] = provider_identity(context, provider)
    return context.bindings[provider]


def observe_resource(context: Context, resource: Resource, timeout: float) -> ActionResult:
    from .engine import invoke

    return invoke(context, resource.probe, timeout, observation=True)


def is_absent(result: ActionResult, resource: Resource, context: Context) -> bool:
    try:
        check(result.data, resource.absent, context.registry.assertions)
        return True
    except AssertionFailure:
        return False


def owned(context: Context, entry: Entry, *, allow_absent: bool = False) -> ActionResult:
    if binding(context, entry.resource.probe.provider) != entry.binding:
        raise OwnershipError("Provider identity differs from the journal")
    result = observe_resource(
        context,
        entry.resource,
        min(context.remaining(), context.config.timeouts.observation_seconds),
    )
    if is_absent(result, entry.resource, context):
        if allow_absent:
            return result
        raise OwnershipError("Owned resource is absent")
    try:
        check(result.data, entry.resource.owned, context.registry.assertions)
    except AssertionFailure:
        raise OwnershipError("Resource ownership guard failed") from None
    return result


def prepare(context: Context, journal: Journal, spec: Resource) -> Entry:
    resource = Resource.model_validate(
        context.resolve(
            spec.model_dump(by_alias=True, exclude_unset=True),
            defer_resource=True,
        )
    )
    if not any(
        predicate.op == "equal"
        and isinstance(predicate.value, str)
        and context.run_id in predicate.value
        for predicate in resource.owned
    ):
        raise OwnershipError("Ownership requires an equality guard containing this run ID")
    target = binding(context, resource.probe.provider)
    result = observe_resource(
        context, resource, min(context.remaining(), context.config.timeouts.observation_seconds)
    )
    if not is_absent(result, resource, context):
        raise OwnershipError(
            "Creation target already exists; refusing adoption of external resources"
        )
    return journal.intent(resource, target, context)


def cleanup(context: Context, journal: Journal) -> list[str]:
    from .engine import invoke
    from .registry import validate_dependencies

    context.deadline = context.clock.now() + context.config.timeouts.cleanup_seconds
    entries = {entry.resource.id: entry for entry in journal.data.entries}
    validate_dependencies({name: entry.resource for name, entry in entries.items()}, partial=True)
    errors: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def clean(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise OwnershipError("Cleanup dependency cycle")
        entry = entries[name]
        visiting.add(name)
        for dependency in entry.resource.after:
            # Later intentions may not exist after partial setup.
            if dependency in entries:
                clean(dependency)
        visiting.remove(name)
        visited.add(name)
        if entry.state == "done":
            return
        if any(entries[d].state != "done" for d in entry.resource.after if d in entries):
            entry.state = "failed"
            entry.error = "Cleanup dependency failed"
            errors.append(f"{name}: {entry.error}")
            journal.save()
            return
        try:
            result = owned(context, entry, allow_absent=True)
            if not is_absent(result, entry.resource, context):
                entry.state = "cleaning"
                journal.save()
                context.resource_data = result.data
                if entry.resource.cleanup:
                    invoke(context, entry.resource.cleanup, context.remaining())
                wait_for(
                    lambda timeout: invoke(
                        context,
                        entry.resource.verify or entry.resource.probe,
                        timeout,
                        observation=True,
                    ),
                    lambda observed: check(
                        observed.data,
                        entry.resource.cleaned or entry.resource.absent,
                        context.registry.assertions,
                    ),
                    mode="eventually",
                    timeout=context.remaining(),
                    interval=1,
                    deadline=context.deadline,
                    observation_timeout=context.config.timeouts.observation_seconds,
                    clock=context.clock,
                )
            entry.state = "done"
            entry.error = None
        except Exception as exc:
            entry.state = "unknown" if isinstance(exc, OwnershipError) else "failed"
            entry.error = (
                context.redactor.text(str(exc))
                if isinstance(exc, IntegrationError)
                else type(exc).__name__
            )
            errors.append(f"{name}: {entry.error}")
        finally:
            context.resource_data = None
            journal.save()

    for entry in reversed(journal.data.entries):
        clean(entry.resource.id)
    return errors
