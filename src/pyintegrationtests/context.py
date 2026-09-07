"""Per-case state and structured, type-preserving references."""

from __future__ import annotations

import string
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Config
from .contracts import ActionResult, thaw
from .errors import DeadlineExceeded, ValidationError
from .reporting import Redactor, private_directory
from .selection import MISSING, scalar
from .waiting import Clock

if TYPE_CHECKING:
    from .registry import Registry


def reference_source(source: str, sources: dict[str, Any]) -> Any:
    # Sources are namespaces, not Python attribute expressions.
    if source not in sources:
        raise ValidationError(f"Unknown reference source: {source}")
    return sources[source]


def resolve(value: Any, sources: dict[str, Any], *, defer_resource: bool = False) -> Any:
    if isinstance(value, list):
        return [resolve(v, sources, defer_resource=defer_resource) for v in value]
    if not isinstance(value, dict):
        return value
    if "$literal" in value:
        if set(value) != {"$literal"}:
            raise ValidationError("$literal must be the only key")
        return value["$literal"]
    if "$ref" in value:
        if set(value) != {"$ref"} or not isinstance(value["$ref"], dict):
            raise ValidationError("Invalid structured reference")
        ref = value["$ref"]
        if set(ref) - {"source", "path"} or not isinstance(ref.get("source"), str):
            raise ValidationError("Reference requires source and optional path")
        if defer_resource and ref["source"] == "resource.data":
            return value
        result = scalar(reference_source(ref["source"], sources), ref.get("path", "$"))
        if result is MISSING:
            raise ValidationError("Reference selected a missing value")
        return thaw(result)
    if "$format" in value:
        spec = value["$format"]
        if (
            set(value) != {"$format"}
            or not isinstance(spec, dict)
            or set(spec) != {"template", "values"}
        ):
            raise ValidationError("$format requires template and values")
        values = resolve(spec["values"], sources, defer_resource=defer_resource)
        if defer_resource and contains_resource_reference(values):
            return {"$format": {"template": spec["template"], "values": values}}
        try:
            for _, key, fmt, conversion in string.Formatter().parse(spec["template"]):
                if key is not None and (not key.isidentifier() or fmt or conversion):
                    raise ValueError
            if not isinstance(values, dict) or any(
                not isinstance(v, str | int | float | bool) for v in values.values()
            ):
                raise ValueError
            return spec["template"].format_map(values)
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValidationError("Invalid limited string format") from None
    return {k: resolve(v, sources, defer_resource=defer_resource) for k, v in value.items()}


def contains_resource_reference(value: Any) -> bool:
    if isinstance(value, dict):
        if "$ref" in value:
            return bool(value["$ref"].get("source") == "resource.data")
        return any(contains_resource_reference(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_resource_reference(child) for child in value)
    return False


@dataclass
class Context:
    config: Config
    environment: dict[str, str]
    registry: Registry
    suite_id: str
    case_id: str
    base_dir: Path
    inputs: dict[str, Any]
    clock: Clock = field(default_factory=Clock)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    redactor: Redactor = field(default_factory=Redactor)
    results: dict[str, ActionResult] = field(default_factory=dict)
    captures: dict[str, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, Any] = field(default_factory=dict)
    bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    deadline: float = 0
    resource_data: Any = None
    diagnostic: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.deadline = self.clock.now() + self.config.timeouts.case_seconds
        for key, value in self.environment.items():
            if any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY")):
                self.redactor.register(value)

    @property
    def directory(self) -> Path:
        return (
            Path(self.config.artifacts.directory).resolve()
            / self.run_id
            / self.suite_id
            / self.case_id
        )

    def prepare_directory(self) -> None:
        private_directory(self.directory)

    def sources(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "inputs": self.inputs,
            "run": {"id": self.run_id, "suiteId": self.suite_id, "caseId": self.case_id},
            "config": self.redactor.redact(self.config.model_dump(by_alias=True)),
            "resource.data": self.resource_data,
        }
        for step, value in self.results.items():
            result[f"steps.{step}.data"] = value.data
            result[f"steps.{step}.meta"] = value.meta
            for name, captured in self.captures.get(step, {}).items():
                result[f"steps.{step}.captures.{name}"] = captured
        return result

    def resolve(self, value: Any, *, defer_resource: bool = False) -> Any:
        return resolve(value, self.sources(), defer_resource=defer_resource)

    def remaining(self) -> float:
        budget = self.deadline - self.clock.now()
        if budget <= 0:
            raise DeadlineExceeded("Case deadline exceeded")
        return budget

    def close(self) -> None:
        for client in self.clients.values():
            close = getattr(client, "close", None)
            if callable(close):
                close()
