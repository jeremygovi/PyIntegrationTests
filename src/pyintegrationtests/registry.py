"""Explicit extension registration and offline validation, without SDK initialization."""

from __future__ import annotations

import importlib.metadata
import re
import string
from typing import Any

from pydantic import ValidationError as PydanticError

from .config import Config, HelmConfig
from .contracts import Action, Assertion, Effect
from .errors import IntegrationError, ValidationError
from .schema import ActionStep, AssertStep, Document, Invocation, Predicate, Resource, Suite
from .selection import compile_path


class Registry:
    def __init__(self) -> None:
        self.actions: dict[str, Action] = {}
        self.assertions: dict[str, Assertion] = {}

    def register_assertion(self, name: str, comparator: Assertion) -> None:
        if name in self.assertions:
            raise ValidationError(f"Duplicate assertion registration: {name}")
        self.assertions[name] = comparator

    def register(self, action: Action) -> None:
        if action.name in self.actions:
            raise ValidationError(f"Duplicate action registration: {action.name}")
        self.actions[action.name] = action

    def get(self, name: str) -> Action:
        if name not in self.actions:
            raise ValidationError(f"Unknown action: {name}")
        return self.actions[name]

    def effect(self, invocation: Invocation, config: Config) -> Effect:
        action = self.get(invocation.action)
        provider = config.providers.get(invocation.provider or "")
        if action.provider_kind is not None:
            if provider is None or provider.kind != action.provider_kind:
                raise ValidationError(
                    f"{action.name} requires a named {action.provider_kind} provider"
                )
        elif invocation.provider is not None:
            raise ValidationError(f"{action.name} does not accept a provider")
        return (
            action.effect(invocation.params, provider) if callable(action.effect) else action.effect
        )

    def validate_call(self, invocation: Invocation, config: Config, *, read: bool = False) -> None:
        action = self.get(invocation.action)
        effect = self.effect(invocation, config)
        if read and effect != "read":
            raise ValidationError(f"{action.name} is not an eligible observation")
        unresolved: list[tuple[str | int, ...]] = []

        def scan(value: Any, path: tuple[str | int, ...] = ()) -> None:
            if isinstance(value, dict):
                if any(k in value for k in ("$ref", "$format")):
                    unresolved.append(path)
                else:
                    for key, child in value.items():
                        scan(child, (*path, key))
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    scan(child, (*path, i))

        scan(invocation.params)
        try:
            action.parameters.model_validate(invocation.params)
        except PydanticError as exc:
            errors = [
                e for e in exc.errors() if not any(e["loc"][: len(p)] == p for p in unresolved)
            ]
            if errors:
                raise ValidationError(
                    f"{action.name}: invalid parameters at {errors[0]['loc']}"
                ) from None
        action.validate(invocation.params, config.providers.get(invocation.provider or ""))

    def load_plugins(self, names: list[str]) -> None:
        entries = importlib.metadata.entry_points(group="pyintegrationtests.plugins")
        for name in names:
            matches = [entry for entry in entries if entry.name == name]
            if len(matches) != 1:
                raise ValidationError(
                    f"Plugin must resolve to exactly one installed entry point: {name}"
                )
            try:
                matches[0].load()(self)
            except Exception:
                raise ValidationError(f"Plugin registration failed: {name}") from None


def default_registry(config: Config) -> Registry:
    from .providers import register_builtins

    registry = Registry()
    register_builtins(registry)
    registry.load_plugins(config.plugins)
    return registry


def _references(value: Any, sources: set[str], *, resource: bool = False) -> None:
    if isinstance(value, list):
        for v in value:
            _references(v, sources, resource=resource)
    elif isinstance(value, dict):
        if "$literal" in value:
            if set(value) != {"$literal"}:
                raise ValidationError("$literal must be the only key")
            return
        if "$ref" in value:
            ref = value["$ref"]
            if set(value) != {"$ref"} or not isinstance(ref, dict) or set(ref) - {"source", "path"}:
                raise ValidationError("Invalid structured reference")
            source = ref.get("source")
            if not isinstance(source, str) or (
                source not in sources and not (resource and source == "resource.data")
            ):
                raise ValidationError(f"Unknown or future reference: {source}")
            compile_path(ref.get("path", "$"))
            return
        if "$format" in value:
            spec = value["$format"]
            if (
                set(value) != {"$format"}
                or not isinstance(spec, dict)
                or set(spec) != {"template", "values"}
            ):
                raise ValidationError("Invalid $format expression")
            try:
                if not isinstance(spec["values"], dict):
                    raise ValueError
                for _, key, fmt, conversion in string.Formatter().parse(spec["template"]):
                    if key is not None and (
                        not key.isidentifier() or key not in spec["values"] or fmt or conversion
                    ):
                        raise ValueError
            except (ValueError, TypeError):
                raise ValidationError("Invalid limited format template") from None
        for v in value.values():
            _references(v, sources, resource=resource)


def _predicates(predicates: list[Predicate], registry: Registry) -> None:
    for predicate in predicates:
        if predicate.op == "custom" and predicate.assertion not in registry.assertions:
            raise ValidationError("Unknown custom assertion")
        compile_path(predicate.path)
        if predicate.op == "matchRegex" and isinstance(predicate.value, str):
            if len(predicate.value) > 512:
                raise ValidationError("Regular expression exceeds 512 characters")
            try:
                re.compile(predicate.value)
            except re.error:
                raise ValidationError("Invalid regular expression") from None
        _predicates(predicate.checks, registry)


def validate_resource(spec: Resource, registry: Registry, config: Config) -> None:
    registry.validate_call(spec.probe, config, read=True)
    if spec.cleanup:
        registry.validate_call(spec.cleanup, config)
        if registry.effect(spec.cleanup, config) != "mutate":
            raise ValidationError("Cleanup must be a mutation")
        if spec.cleanup.provider != spec.probe.provider:
            raise ValidationError("Cleanup and ownership probe must use the same provider")
    elif not spec.after or not spec.cleaned:
        raise ValidationError(
            "Verification-only resources require after dependencies and cleaned assertions"
        )
    if spec.verify:
        registry.validate_call(spec.verify, config, read=True)
        if spec.verify.provider != spec.probe.provider:
            raise ValidationError("Cleanup verification must use the ownership provider")
    _predicates(spec.absent + spec.owned + spec.cleaned, registry)


def validate_suite(suite: Suite, config: Config, registry: Registry, document: Document) -> None:
    for name, provider in config.providers.items():
        if isinstance(provider, HelmConfig):
            cluster = config.providers.get(provider.kubernetes_provider)
            if cluster is None or cluster.kind != "kubernetes":
                raise ValidationError(f"{name}: kubernetesProvider must name a Kubernetes provider")
    if len({case.id for case in suite.tests}) != len(suite.tests):
        raise ValidationError(f"{document.path}: duplicate test ID")
    for case_index, case in enumerate(suite.tests):
        sources = {"inputs", "run", "config"}
        steps: set[str] = set()
        resources: dict[str, Resource] = {}
        for index, step in enumerate(case.steps):
            try:
                if step.id in steps:
                    raise ValidationError("Duplicate step ID")
                payload = step.model_dump(by_alias=True, exclude_unset=True)
                creates = payload.pop("creates", None)
                tracked = payload.pop("tracks", [])
                _references(payload, sources)
                for ownership in ([creates] if creates else []) + tracked:
                    compensation = ownership.pop("cleanup", None)
                    verification = ownership.pop("verify", None)
                    _references(ownership, sources)
                    _references(compensation, sources, resource=True)
                    _references(verification, sources, resource=True)
                if isinstance(step, ActionStep):
                    registry.validate_call(step, config)
                    effect = registry.effect(step, config)
                    if effect == "mutate" and not (step.creates or step.uses or step.tracks):
                        raise ValidationError("Mutation requires creates or uses")
                    if effect != "mutate" and (step.creates or step.uses or step.tracks):
                        raise ValidationError("Only mutations may declare ownership")
                    if step.creates:
                        if step.creates.id in resources:
                            raise ValidationError("Resource ID reused; use a new ID for reinstall")
                        if step.creates.probe.provider != step.provider:
                            raise ValidationError(
                                "Creation and ownership probe must use the same provider"
                            )
                        validate_resource(step.creates, registry, config)
                        resources[step.creates.id] = step.creates
                    for resource in step.tracks:
                        if resource.id in resources:
                            raise ValidationError("Tracked resource ID reused")
                        validate_resource(resource, registry, config)
                        resources[resource.id] = resource
                    if any(name not in resources for name in step.uses):
                        raise ValidationError("uses references an unknown resource")
                elif isinstance(step, AssertStep):
                    if step.observe:
                        registry.validate_call(step.observe, config, read=True)
                    elif step.source not in sources:
                        raise ValidationError("Assertion source is unknown or future")
                    _predicates(step.fail_when, registry)
                _predicates(step.assertions, registry)
                for capture in step.captures.values():
                    compile_path(capture.path)
                steps.add(step.id)
                sources.update({f"steps.{step.id}.data", f"steps.{step.id}.meta"})
                sources.update(f"steps.{step.id}.captures.{name}" for name in step.captures)
            except IntegrationError as exc:
                location = document.location(("tests", case_index, "steps", index))
                raise ValidationError(f"{location}: test={case.id} step={step.id}: {exc}") from None
        validate_dependencies(resources)


def validate_dependencies(resources: dict[str, Resource], *, partial: bool = False) -> None:
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str) -> None:
        if name not in resources:
            if partial:
                return
            raise ValidationError(f"Unknown cleanup dependency: {name}")
        if name in visiting:
            raise ValidationError("Cleanup dependency cycle")
        if name in done:
            return
        visiting.add(name)
        for dependency in resources[name].after:
            visit(dependency)
        visiting.remove(name)
        done.add(name)

    for name in resources:
        visit(name)
