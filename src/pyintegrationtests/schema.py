"""Versioned DSL and bounded YAML loader with source locations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticError
from pydantic.alias_generators import to_camel
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .errors import ValidationError

API_VERSION = "pyintegrationtests/v1alpha1"
MAX_YAML_BYTES = 2 * 1024 * 1024


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=to_camel)


class Transform(Model):
    name: Literal["decodeBase64", "decodeJson", "sort", "lower", "stripTrailingDot", "decimal"]


class Predicate(Model):
    path: str = "$"
    op: Literal[
        "equal",
        "notEqual",
        "exists",
        "notExists",
        "isNull",
        "isNotNull",
        "contains",
        "notContains",
        "isSubset",
        "isNotSubset",
        "lengthEqual",
        "isType",
        "isEmpty",
        "isNotEmpty",
        "greater",
        "greaterOrEqual",
        "less",
        "lessOrEqual",
        "matchRegex",
        "allOf",
        "anyOf",
        "not",
        "custom",
    ]
    assertion: str | None = None
    value: Any = None
    checks: list[Predicate] = Field(default_factory=list)
    quantifier: Literal["scalar", "any", "all"] = "scalar"
    allow_empty: bool = False
    order: Literal["ordered", "set", "multiset"] = "ordered"
    transforms: list[Transform] = Field(default_factory=list)
    default: Any = None
    sensitive: bool = False

    @model_validator(mode="after")
    def check_operand(self) -> Self:
        if (self.op == "custom") != (self.assertion is not None):
            raise ValueError("Custom comparison requires a registered assertion name")
        unary = {"exists", "notExists", "isNull", "isNotNull", "isEmpty", "isNotEmpty"}
        if self.op in {"allOf", "anyOf", "not"}:
            if not self.checks or (self.op == "not" and len(self.checks) != 1):
                raise ValueError("Composition requires checks; not requires exactly one")
        elif self.checks:
            raise ValueError("checks only applies to composition")
        elif self.op not in unary and "value" not in self.model_fields_set:
            raise ValueError("Comparator requires value")
        return self


class Invocation(Model):
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    provider: str | None = None
    params: dict[str, Any] = Field(default_factory=dict, alias="with")


class Observation(Invocation):
    not_found: Literal["error", "null"] = "error"


class ExpectedError(Model):
    provider: str
    code: str | None = None
    status: int | None = None

    @model_validator(mode="after")
    def targeted(self) -> Self:
        if self.code is None and self.status is None:
            raise ValueError("expectedError requires code or status")
        return self


class Resource(Model):
    """Exact probe and run-specific ownership proof, persisted before creation."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    probe: Observation
    absent: list[Predicate] = Field(min_length=1)
    owned: list[Predicate] = Field(min_length=1)
    cleanup: Invocation | None = None
    verify: Observation | None = None
    cleaned: list[Predicate] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)


class Capture(Model):
    path: str = "$"
    transforms: list[Transform] = Field(default_factory=list)
    sensitive: bool = False


class ActionStep(Invocation):
    kind: Literal["action"] = "action"
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    creates: Resource | None = None
    tracks: list[Resource] = Field(default_factory=list)
    uses: list[str] = Field(default_factory=list)
    captures: dict[str, Capture] = Field(default_factory=dict)
    assertions: list[Predicate] = Field(default_factory=list)
    expected_error: ExpectedError | None = None
    sensitive: bool = False


class AssertStep(Model):
    kind: Literal["assert"]
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    source: str | None = None
    observe: Observation | None = None
    assertions: list[Predicate] = Field(min_length=1)
    mode: Literal["immediate", "eventually", "consistently"] = "immediate"
    timeout: float = Field(default=60, gt=0, le=86400)
    interval: float = Field(default=1, gt=0, le=300)
    fail_when: list[Predicate] = Field(default_factory=list)
    captures: dict[str, Capture] = Field(default_factory=dict)
    sensitive: bool = False

    @model_validator(mode="after")
    def fresh(self) -> Self:
        if (self.source is None) == (self.observe is None):
            raise ValueError("Specify exactly one of source and observe")
        if self.mode != "immediate" and self.observe is None:
            raise ValueError("Temporal assertions require a fresh observation")
        return self


type Step = Annotated[ActionStep | AssertStep, Field(discriminator="kind")]


class Case(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    timeout: float | None = Field(default=None, gt=0, le=86400)
    steps: list[Step] = Field(min_length=1)


class Defaults(Model):
    timeout: float | None = Field(default=None, gt=0, le=86400)


class Suite(Model):
    api_version: Literal["pyintegrationtests/v1alpha1"]
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str | None = None
    config: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    sensitive_inputs: list[str] = Field(default_factory=list)
    defaults: Defaults = Field(default_factory=Defaults)
    tests: list[Case] = Field(min_length=1)


@dataclass(frozen=True)
class Document:
    path: Path
    data: dict[str, Any]
    locations: dict[tuple[str | int, ...], int]

    def location(self, parts: tuple[str | int, ...]) -> str:
        while parts not in self.locations and parts:
            parts = parts[:-1]
        return f"{self.path}:{self.locations.get(parts, 1)}"


def load_document(path: Path) -> Document:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_YAML_BYTES + 1)
        if len(raw) > MAX_YAML_BYTES:
            raise ValidationError(f"{path}: YAML exceeds 2 MiB")
        # Reject aliases, including recursive and expansion bombs. Anchors alone are harmless.
        if any(isinstance(e, yaml.AliasEvent) for e in yaml.parse(raw, Loader=yaml.SafeLoader)):
            raise ValidationError(f"{path}: YAML aliases are not supported")
        root = yaml.compose(raw, Loader=yaml.SafeLoader)
        if not isinstance(root, MappingNode):
            raise ValidationError(f"{path}: expected one YAML mapping")
        locations: dict[tuple[str | int, ...], int] = {}
        loader = yaml.SafeLoader(raw)
        count = 0

        def convert(node: Node, parts: tuple[str | int, ...], depth: int = 0) -> Any:
            nonlocal count
            count += 1
            if node.tag not in {
                f"tag:yaml.org,2002:{tag}"
                for tag in ("map", "seq", "str", "int", "float", "bool", "null", "timestamp")
            }:
                raise ValidationError(f"{path}:{node.start_mark.line + 1}: unsupported YAML tag")
            if depth > 40 or count > 50000:
                raise ValidationError(f"{path}:{node.start_mark.line + 1}: YAML too complex")
            locations[parts] = node.start_mark.line + 1
            if isinstance(node, MappingNode):
                result: dict[str, Any] = {}
                for key_node, value_node in node.value:
                    if (
                        not isinstance(key_node, ScalarNode)
                        or key_node.tag != "tag:yaml.org,2002:str"
                    ):
                        raise ValidationError(
                            f"{path}:{key_node.start_mark.line + 1}: key must be a string"
                        )
                    key = key_node.value
                    if key in result:
                        raise ValidationError(
                            f"{path}:{key_node.start_mark.line + 1}: duplicate key {key}"
                        )
                    result[key] = convert(value_node, (*parts, key), depth + 1)
                return result
            if isinstance(node, SequenceNode):
                return [convert(v, (*parts, i), depth + 1) for i, v in enumerate(node.value)]
            return loader.construct_object(node)

        try:
            data = convert(root, ())
        finally:
            loader.dispose()
        return Document(path.resolve(), data, locations)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else 1
        raise ValidationError(f"{path}:{line}: cannot parse YAML ({type(exc).__name__})") from None


def parse_model[T: BaseModel](document: Document, model: type[T]) -> T:
    try:
        return model.model_validate(document.data)
    except PydanticError as exc:
        # Never render Pydantic's input_value; it can contain credentials.
        messages = [
            f"{document.location(e['loc'])}: {'.'.join(map(str, e['loc']))}: {e['type']}"
            for e in exc.errors(include_input=False, include_context=False)
        ]
        raise ValidationError("\n".join(messages)) from None


def suite_files(paths: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for path in paths:
        if not path.exists():
            raise ValidationError(f"{path}: path does not exist")
        candidates = path.rglob("*") if path.is_dir() else [path]
        found.update(p for p in candidates if p.name.endswith((".integ.yaml", ".integ.yml")))
    return sorted(found)
