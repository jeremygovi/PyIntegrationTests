"""Versioned, atomic cleanup intentions. Payloads and credentials never belong here."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .config import Config
from .context import Context
from .errors import ValidationError
from .reporting import atomic_json
from .schema import Model, Resource


class Entry(Model):
    resource: Resource
    binding: dict[str, Any]
    state: Literal["intent", "created", "cleaning", "done", "unknown", "failed"] = "intent"
    error: str | None = None


class JournalData(Model):
    version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    suite_id: str
    case_id: str
    config_hash: str
    entries: list[Entry] = Field(default_factory=list)


def config_hash(config: Config) -> str:
    # Store a digest only. Credentials are refreshed on resume from env/native SDK chains.
    raw = config.model_dump_json(exclude={"artifacts", "timeouts"})
    return hashlib.sha256(raw.encode()).hexdigest()


class Journal:
    def __init__(self, path: Path, data: JournalData) -> None:
        self.path = path
        self.data = data

    @classmethod
    def create(cls, context: Context) -> Journal:
        return cls(
            context.directory / "journal.json",
            JournalData(
                run_id=context.run_id,
                suite_id=context.suite_id,
                case_id=context.case_id,
                config_hash=config_hash(context.config),
            ),
        )

    @classmethod
    def load(cls, path: Path, config: Config) -> Journal:
        try:
            if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError
            data = JournalData.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            raise ValidationError("Invalid cleanup journal") from None
        if data.config_hash != config_hash(config):
            raise ValidationError("Cleanup configuration differs from the recorded target")
        ids = [entry.resource.id for entry in data.entries]
        if len(set(ids)) != len(ids):
            raise ValidationError("Duplicate cleanup resource ID")
        return cls(path, data)

    def save(self) -> None:
        atomic_json(self.path, self.data.model_dump(by_alias=True))

    def intent(self, resource: Resource, binding: dict[str, Any], context: Context) -> Entry:
        payload = resource.model_dump(by_alias=True, exclude_unset=True)
        if not context.redactor.safe_for_journal(payload):
            raise ValidationError(
                "Cleanup parameters contain sensitive data; use public resource identities"
            )
        # Ensure a plain JSON representation before the first possible side effect.
        try:
            json.dumps(payload, allow_nan=False)
        except (ValueError, TypeError):
            raise ValidationError("Cleanup parameters must be JSON-serializable") from None
        entry = Entry(resource=resource, binding=binding)
        self.data.entries.append(entry)
        self.save()
        return entry
