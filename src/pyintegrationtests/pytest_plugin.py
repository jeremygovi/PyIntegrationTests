"""Public pytest collection and lifecycle hooks for explicitly identified YAML suites."""

from __future__ import annotations

import signal
from collections.abc import Iterator
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from .config import Config
    from .context import Context
    from .journal import Journal
    from .registry import Registry
    from .schema import Case, Document, Suite

_REPORTS = pytest.StashKey[list[dict[str, Any]]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pyintegrationtests")
    group.addoption("--pit-config", default=None, help="Explicit provider configuration")
    group.addoption("--pit-env-file", default=None, help="Explicit .env file (process env wins)")
    group.addoption("--pit-live", action="store_true", help="Allow real infrastructure execution")
    group.addoption("--pit-json", default=None, help="Private JSON report destination")
    group.addoption("--pit-case-seconds", type=float, default=None, help="Override case deadline")
    group.addoption(
        "--pit-label", action="append", default=[], help="Select label key=value; repeatable"
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: requires explicit real infrastructure opt-in")
    config.stash[_REPORTS] = []


def load_suite(
    path: Path,
    config_path: str | None = None,
    env_path: str | None = None,
    case_seconds: float | None = None,
) -> tuple[Document, Suite, Config, dict[str, str], Registry]:
    from .config import load_config
    from .registry import default_registry, validate_suite
    from .schema import Suite, load_document, parse_model

    document = load_document(path)
    suite = parse_model(document, Suite)
    path_config = (
        Path(config_path) if config_path else (path.parent / suite.config if suite.config else None)
    )
    overrides = {"timeouts": {"caseSeconds": case_seconds}} if case_seconds else None
    config, environment = load_config(path_config, Path(env_path) if env_path else None, overrides)
    registry = default_registry(config)
    validate_suite(suite, config, registry, document)
    return document, suite, config, environment, registry


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> SuiteFile | None:
    if file_path.name.endswith((".integ.yaml", ".integ.yml")):
        return SuiteFile.from_parent(parent, path=file_path)
    return None


class SuiteFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        from .errors import IntegrationError

        try:
            document, suite, settings, environment, registry = load_suite(
                self.path,
                self.config.getoption("pit_config"),
                self.config.getoption("pit_env_file"),
                self.config.getoption("pit_case_seconds"),
            )
        except IntegrationError as exc:
            raise self.CollectError(str(exc)) from None
        for index, case in enumerate(suite.tests):
            yield IntegrationItem.from_parent(
                self,
                name=case.id,
                case=case,
                suite=suite,
                settings=settings,
                environment=environment,
                registry=registry,
                line=document.locations.get(("tests", index), 1) - 1,
            )


class IntegrationItem(pytest.Item):
    def __init__(
        self,
        *,
        case: Case,
        suite: Suite,
        settings: Config,
        environment: dict[str, str],
        registry: Registry,
        line: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.case, self.suite, self.settings = case, suite, settings
        self.environment, self.registry, self.line = environment, registry, line
        self.context: Context | None = None
        self.journal: Journal | None = None
        self.labels = {**suite.labels, **case.labels}
        self.live = bool(settings.providers)
        if self.live:
            self.add_marker("live")

    def setup(self) -> None:
        from .context import Context
        from .errors import ValidationError
        from .journal import Journal

        if self.live and not self.config.getoption("pit_live"):
            pytest.skip("Real infrastructure requires --pit-live and explicit provider targets")
        if self.config.getoption("numprocesses", default=0):
            raise ValidationError("pytest-xdist is not supported for infrastructure suites")
        self.context = Context(
            self.settings,
            self.environment,
            self.registry,
            self.suite.id,
            self.case.id,
            self.path.parent,
            dict(self.suite.inputs),
        )
        self.context.deadline = self.context.clock.now() + (
            self.config.getoption("pit_case_seconds")
            or self.case.timeout
            or self.suite.defaults.timeout
            or self.settings.timeouts.case_seconds
        )
        for name in self.suite.sensitive_inputs:
            self.context.redactor.register(self.context.inputs.get(name))
        self.context.prepare_directory()
        self.journal = Journal.create(self.context)
        self.journal.save()

    def runtest(self) -> None:
        from .engine import execute_case
        from .reporting import atomic_json

        assert self.context is not None and self.journal is not None

        def interrupted(signum: int, frame: FrameType | None) -> None:
            raise KeyboardInterrupt("Termination requested; attempting cleanup")

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            execute_case(self.context, self.journal, self.case)
        except BaseException:
            atomic_json(
                self.context.directory / "diagnostic.json",
                self.context.redactor.redact(self.context.diagnostic),
            )
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)

    def teardown(self) -> None:
        from .cleanup import cleanup
        from .errors import IntegrationError
        from .reporting import atomic_json

        if self.context is None or self.journal is None:
            return
        errors = cleanup(self.context, self.journal)
        try:
            self.context.close()
        except Exception as exc:
            errors.append(f"Client close failed: {type(exc).__name__}")
        report = self.context.redactor.redact(
            {
                "nodeid": self.nodeid,
                "runId": self.context.run_id,
                "steps": self.context.trace,
                "cleanupErrors": errors,
                "journal": str(self.journal.path),
            }
        )
        atomic_json(self.context.directory / "report.json", report)
        self.config.stash[_REPORTS].append(report)
        if errors:
            raise IntegrationError("Cleanup failed: " + "; ".join(errors))

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None) -> str:
        from .errors import IntegrationError

        error = excinfo.value
        message = str(error) if isinstance(error, IntegrationError) else type(error).__name__
        if self.context:
            message = self.context.redactor.text(message)
        return f"{self.path}:{self.line + 1}: {self.suite.id}/{self.case.id}: {message}"

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, self.line, f"{self.suite.id}/{self.case.id}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    filters: list[str] = config.getoption("pit_label") or []
    for value in filters:
        if "=" not in value:
            raise pytest.UsageError("--pit-label requires key=value")
    selected, deselected = [], []
    for item in items:
        if isinstance(item, IntegrationItem) and not all(
            item.labels.get(k) == v for k, v in (f.split("=", 1) for f in filters)
        ):
            deselected.append(item)
        else:
            selected.append(item)
    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    outcome = yield
    report = outcome.get_result()
    if isinstance(item, IntegrationItem):
        if report.failed and call.excinfo:
            report.longrepr = item.repr_failure(call.excinfo)
        # Arbitrary SDK/plugin stdout may contain secrets not registered in inputs.
        report.sections = []


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    from .reporting import atomic_json

    destination = session.config.getoption("pit_json")
    if destination:
        atomic_json(
            Path(destination),
            {"version": 1, "exitCode": exitstatus, "cases": session.config.stash[_REPORTS]},
        )
