"""Ordered case steps; pytest owns setup/call/teardown and final verdicts."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError as PydanticError

from .assertions import check, matches
from .cleanup import owned, prepare
from .context import Context, reference_source
from .contracts import ActionResult, freeze, thaw
from .deadlines import time_limit
from .errors import AssertionFailure, IntegrationError, OperationError, ValidationError
from .journal import Journal
from .schema import ActionStep, AssertStep, Case, Invocation, Observation, Predicate
from .selection import MISSING, scalar, select, transform
from .waiting import wait_for


def invoke(
    context: Context, invocation: Invocation, timeout: float, *, observation: bool = False
) -> ActionResult:
    action = context.registry.get(invocation.action)
    context.registry.validate_call(invocation, context.config, read=observation)
    try:
        params = action.parameters.model_validate(context.resolve(invocation.params)).model_dump()
    except PydanticError:
        raise ValidationError(f"{action.name}: resolved parameters failed validation") from None
    try:
        budget = min(timeout, context.remaining())
        with time_limit(budget):
            result = action.execute(context, invocation.provider, params, budget)
        context.remaining()
        if (result.sensitive or action.sensitive) and action.provider_kind not in {"aws", "helm"}:
            context.redactor.register(thaw(result.data))
        else:
            context.redactor.scan(thaw(result.data))
        return result
    except OperationError as exc:
        if isinstance(invocation, Observation) and invocation.not_found == "null" and exc.absent:
            return ActionResult(None, {"absent": True, "provider": exc.provider, "code": exc.code})
        raise
    except IntegrationError:
        raise
    except Exception as exc:
        # Third-party exceptions may contain request bodies, tokens or decoded secrets.
        raise IntegrationError(f"{action.name}: adapter failed ({type(exc).__name__})") from None


def _predicates(context: Context, values: list[Predicate]) -> list[Predicate]:
    return [
        Predicate.model_validate(context.resolve(p.model_dump(by_alias=True, exclude_unset=True)))
        for p in values
    ]


def diagnostic(
    context: Context,
    step: ActionStep | AssertStep,
    result: ActionResult,
    predicates: list[Predicate],
) -> None:
    context.diagnostic = {
        "step": step.id,
        "meta": context.redactor.redact(thaw(result.meta)),
        "assertions": [
            {
                "path": predicate.path,
                "op": predicate.op,
                "expected": context.redactor.redact(
                    predicate.value,
                    sensitive=result.sensitive or step.sensitive or predicate.sensitive,
                ),
                "selected": context.redactor.redact(
                    select(result.data, predicate.path),
                    sensitive=result.sensitive or step.sensitive or predicate.sensitive,
                ),
            }
            for predicate in predicates
        ],
    }


def execute_step(context: Context, journal: Journal, step: ActionStep | AssertStep) -> ActionResult:
    if isinstance(step, ActionStep):
        entry = prepare(context, journal, step.creates) if step.creates else None
        tracked = [prepare(context, journal, spec) for spec in step.tracks]
        for resource_id in step.uses:
            recorded = next(e for e in journal.data.entries if e.resource.id == resource_id)
            owned(context, recorded)
        try:
            result = invoke(context, step, context.remaining())
        except OperationError as exc:
            expected = step.expected_error
            if (
                expected is None
                or expected.provider != exc.provider
                or (expected.code is not None and expected.code != exc.code)
                or (expected.status is not None and expected.status != exc.status)
            ):
                raise
            result = ActionResult(
                {"error": {"provider": exc.provider, "code": exc.code, "status": exc.status}}
            )
        else:
            if entry or tracked:
                if entry:
                    entry.state = "created"
                for tracked_entry in tracked:
                    tracked_entry.state = "created"
                journal.save()
            if step.expected_error is not None:
                raise AssertionFailure("Expected operation error was not raised")
        predicates = _predicates(context, step.assertions)
        diagnostic(context, step, result, predicates)
        check(result.data, predicates, context.registry.assertions)
    else:
        predicates = _predicates(context, step.assertions)
        fail_when = _predicates(context, step.fail_when)

        def observe(timeout: float) -> ActionResult:
            if step.observe:
                return invoke(context, step.observe, timeout, observation=True)
            return ActionResult(reference_source(step.source or "", context.sources()))

        def verify(result: ActionResult) -> None:
            diagnostic(context, step, result, predicates)
            if any(
                [
                    matches(result.data, predicate, context.registry.assertions)
                    for predicate in fail_when
                ]
            ):
                raise IntegrationError("Terminal observation condition matched")
            check(result.data, predicates, context.registry.assertions)

        result = wait_for(
            observe,
            verify,
            mode=step.mode,
            timeout=step.timeout,
            interval=step.interval,
            deadline=context.deadline,
            observation_timeout=context.config.timeouts.observation_seconds,
            clock=context.clock,
        )
    captures = {}
    for name, capture in step.captures.items():
        value = scalar(result.data, capture.path)
        if value is MISSING:
            raise ValidationError("Capture selected a missing value")
        value = transform(value, capture.transforms)
        captures[name] = freeze(value)
        if capture.sensitive or step.sensitive:
            context.redactor.register(value)
    context.captures[step.id] = captures
    context.results[step.id] = result
    if step.sensitive:
        context.redactor.register(thaw(result.data))
    return result


def execute_case(context: Context, journal: Journal, case: Case) -> None:
    for step in case.steps:
        record = {"id": step.id, "startedAt": datetime.now(UTC).isoformat(), "status": "running"}
        context.trace.append(record)
        try:
            context.remaining()
            execute_step(context, journal, step)
            record["status"] = "passed"
        except BaseException:
            record["status"] = "failed"
            raise
