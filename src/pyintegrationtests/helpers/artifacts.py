"""Private fixtures, deterministic ZIPs, explicit binary loading and run names."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ..context import Context
from ..contracts import Action, ActionResult
from ..errors import ValidationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class ValueParams(Model):
    value: Any


class NameParams(Model):
    prefix: str = "pit"
    max_length: int = Field(default=63, ge=16, le=253)


class ZIPParams(Model):
    files: dict[str, str]
    name: str = Field(default="fixture.zip", pattern=r"^[a-zA-Z0-9_.-]+\.zip$")


class FileParams(Model):
    path: str
    encoding: Literal["text", "base64", "json"] = "text"


class HashParams(Model):
    value: Any
    algorithm: Literal["sha256", "sha512"] = "sha256"


def resource_name(prefix: str, run_id: str, case_id: str, max_length: int = 63) -> str:
    suffix = hashlib.sha256(f"{run_id}:{case_id}:{prefix}".encode()).hexdigest()[:12]
    clean = re.sub(r"[^a-z0-9-]+", "-", prefix.lower()).strip("-") or "pit"
    return f"{clean[: max_length - 13].rstrip('-')}-{suffix}"


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValidationError("File exceeds configured artifact byte limit")
    return value


def binary(value: Any, context: Context) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$bytes"}:
            try:
                result = base64.b64decode(value["$bytes"], validate=True)
            except (ValueError, TypeError):
                raise ValidationError("Invalid base64 binary input") from None
            if len(result) > context.config.artifacts.max_bytes:
                raise ValidationError("Binary input exceeds artifact byte limit")
            return result
        if set(value) == {"$file"}:
            return read_bounded(
                context.base_dir / value["$file"], context.config.artifacts.max_bytes
            )
        return {k: binary(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [binary(v, context) for v in value]
    return value


def value_action(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    return ActionResult(params["value"])


def name_action(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    return ActionResult(
        {
            "name": resource_name(
                params["prefix"], context.run_id, context.case_id, params["max_length"]
            )
        }
    )


def zip_action(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    context.prepare_directory()
    path = context.directory / params["name"]
    size = 0
    with path.open("wb") as handle:
        path.chmod(0o600)
        with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(params["files"].items()):
                member = PurePosixPath(name)
                if member.is_absolute() or ".." in member.parts or "\\" in name:
                    raise ValidationError("ZIP member must be a relative path without traversal")
                raw = content.encode("utf-8")
                size += len(raw)
                if size > context.config.artifacts.max_bytes:
                    raise ValidationError("ZIP input exceeds artifact byte limit")
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, raw)
    data = read_bounded(path, context.config.artifacts.max_bytes)
    digest = hashlib.sha256(data).digest()
    return ActionResult(
        {
            "path": str(path),
            "sha256": digest.hex(),
            "sha256Base64": base64.b64encode(digest).decode(),
            "size": len(data),
        }
    )


def file_action(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    data = read_bounded(context.base_dir / params["path"], context.config.artifacts.max_bytes)
    match params["encoding"]:
        case "base64":
            value = base64.b64encode(data).decode()
        case "json":
            value = json.loads(data)
        case _:
            value = data.decode()
    return ActionResult(value, sensitive=True)


def hash_action(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    value = binary(params["value"], context)
    if isinstance(value, str):
        value = value.encode()
    if not isinstance(value, bytes):
        raise ValidationError("Hash input must be text or explicit binary")
    digest = hashlib.new(params["algorithm"], value).digest()
    return ActionResult({"hex": digest.hex(), "base64": base64.b64encode(digest).decode()})


def register(registry: Registry) -> None:
    for name, model, handler in (
        ("data.value", ValueParams, value_action),
        ("name.generate", NameParams, name_action),
        ("artifact.zip", ZIPParams, zip_action),
        ("artifact.read", FileParams, file_action),
        ("artifact.hash", HashParams, hash_action),
    ):
        registry.register(Action(name, model, handler, "local"))
