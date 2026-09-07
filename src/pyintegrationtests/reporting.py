"""Private atomic artifacts and conservative redaction at the output boundary."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .contracts import normalize
from .errors import ValidationError

_SECRET_KEY = re.compile(
    r"secret|token|password|authorization|cookie|private.?key|credential", re.I
)


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                self.register(child)
        elif isinstance(value, list | tuple):
            for child in value:
                self.register(child)
        elif isinstance(value, str) and value:
            self._secrets.add(value)

    def text(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value

    def scan(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _SECRET_KEY.search(key) or key in {"SecretString", "SecretBinary"}:
                    self.register(child)
                else:
                    self.scan(child)
        elif isinstance(value, list | tuple):
            for child in value:
                self.scan(child)

    def redact(self, value: Any, sensitive: bool = False) -> Any:
        if sensitive:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                k: "[REDACTED]" if _SECRET_KEY.search(k) else self.redact(v)
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.redact(v) for v in value]
        return self.text(value) if isinstance(value, str) else value

    def safe_for_journal(self, value: Any) -> bool:
        return bool(self.redact(value) == value)


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValidationError("Artifact directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic_json(path: Path, value: Any) -> None:
    private_directory(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalize(value), handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        Path(temp_name).unlink(missing_ok=True)
