"""Errors with safe, stable diagnostic boundaries."""

from dataclasses import dataclass


class IntegrationError(Exception):
    """A user-facing error whose message must not contain response payloads."""


class ValidationError(IntegrationError):
    """Invalid input, detected before execution."""


class SelectionError(IntegrationError):
    """An invalid selector or transformation."""


class AssertionFailure(IntegrationError):
    """A comparison failed."""


class OwnershipError(IntegrationError):
    """Ownership could not be established; no deletion is authorized."""


class DeadlineExceeded(IntegrationError):
    """A bounded operation exhausted its budget."""


@dataclass
class OperationError(IntegrationError):
    provider: str
    code: str
    status: int | None = None
    retryable: bool = False
    absent: bool = False
    retry_after: float | None = None

    def __str__(self) -> str:
        return f"{self.provider}: operation failed ({self.code}, status={self.status})"
