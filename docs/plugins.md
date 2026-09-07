# Trusted Python extensions

Extensions are installed Python distributions and explicitly allowlisted in `plugins`.
PyIntegrationTests loads only matching entry points from the
`pyintegrationtests.plugins` group. YAML cannot name an import path, module, file or Python
expression. Plugins and scenarios are trusted code; this mechanism is not a multi-tenant
sandbox.

```toml
[project.entry-points."pyintegrationtests.plugins"]
company = "company_pit:register"
```

```python
from typing import Any
from pydantic import BaseModel, ConfigDict
from pyintegrationtests.contracts import Action, ActionResult


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=lambda name: name)
    resource: str


def read(context, provider: str | None, params: dict[str, Any], timeout: float) -> ActionResult:
    # Respect timeout and context.remaining(); return complete provider data.
    return ActionResult({"resource": params["resource"], "newField": True})


def register(registry) -> None:
    registry.register(Action("company.read", Params, read, "read"))
    registry.register_assertion(
        "company.same-prefix", lambda actual, expected: actual.startswith(expected)
    )
```

An `Action` declares a stable name, Pydantic parameter model, handler, effect (`read`,
`mutate`, or `local`), optional provider kind, sensitivity and an offline validator.
The effect can be a pure function of validated raw parameters and provider configuration.
Only `read` actions may appear in observations. Every mutation still needs DSL ownership.
Return `ActionResult` with complete normalized `data`, useful non-sensitive `meta`, and
`sensitive=True` when the full result must be masked. Do not print credentials or payloads.

Handlers receive the case-local context, provider name, validated parameters and remaining
timeout. They must avoid global mutable clients, close streams, honor size/deadline limits,
and translate provider failures to `OperationError`. Mark only true transient failures
`retryable`; mark only an authoritative not-found response `absent`. Do not retry mutations.

Custom assertions are pure callables `(actual, expected) -> bool`. Use them in YAML via
`op: custom` and `assertion: company.same-prefix`. They must do no I/O and must not raise
or disclose their operands. Field equality, presence, list filtering and temporal polling
already belong to the generic DSL and should not become custom assertions.

Duplicate registrations, missing entry points and plugin exceptions fail validation using
redacted messages. Add offline tests for registration, parameter validation, complete
responses, error translation, redaction, effects and cleanup. Keep optional SDK imports
inside handlers so core validation works without that extra installed.

## ACK test-infra decision

`acktest` is not a dependency in V1. At inspected upstream revision
`16ffb8919c36a25fc4492624f779be81e1a13cce`, its setup declares version `0.0.1`, Python
3.8 classifiers, exact pins for boto3 1.43.56, Kubernetes 28.1.0, PyYAML 6.0.1 and
pytest-xdist 3.5.0, and its package import installs process-wide credential behavior.
No `acktest` distribution was available on PyPI when checked. Those constraints conflict
with isolated named clients and the current dependency set. Generic Kubernetes condition
helpers cover the required ACK checks without copying the library. Re-evaluate a later
published release in isolation before adding an optional adapter.
