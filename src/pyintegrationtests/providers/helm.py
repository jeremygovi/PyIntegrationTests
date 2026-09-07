"""Official Helm executable, typed values and isolated reproducible dependency builds."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import Field

from ..config import HelmConfig, KubernetesConfig
from ..context import Context
from ..contracts import Action, ActionResult, Effect
from ..errors import OperationError, OwnershipError, ValidationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class HelmParams(Model):
    operation: Literal[
        "install",
        "upgrade",
        "uninstall",
        "status",
        "get-values",
        "get-manifest",
        "dependency-build",
    ]
    release: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,51}[a-z0-9]$")
    chart: str | None = None
    version: str | None = None
    values_files: list[str] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False
    workspace_root: str | None = None


def effect(params: dict[str, Any], config: Any) -> Effect:
    if params.get("operation") == "dependency-build":
        return "local"
    return (
        "read" if params.get("operation") in {"status", "get-values", "get-manifest"} else "mutate"
    )


def validate(params: dict[str, Any], config: Any) -> None:
    if params.get("operation") != "dependency-build" and not params.get("release"):
        raise ValidationError("Helm operation requires an exact release name")
    if params.get("operation") in {"install", "upgrade", "dependency-build"} and not params.get(
        "chart"
    ):
        raise ValidationError("Helm operation requires a chart")
    chart = params.get("chart")
    if isinstance(chart, str) and chart.startswith("-"):
        raise ValidationError("Helm chart cannot be an option")


def cluster(context: Context, provider: str) -> tuple[HelmConfig, KubernetesConfig]:
    settings = context.config.providers[provider]
    assert isinstance(settings, HelmConfig)
    kube = context.config.providers[settings.kubernetes_provider]
    assert isinstance(kube, KubernetesConfig)
    return settings, kube


def identity(context: Context, provider: str) -> dict[str, Any]:
    from .kubernetes import identity as kube_identity

    settings, _ = cluster(context, provider)
    return {**kube_identity(context, settings.kubernetes_provider), "kind": "helm"}


def run_process(
    argv: list[str], timeout: float, *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> str:
    executable = shutil.which(argv[0], path=(env or {}).get("PATH"))
    if executable is None:
        raise OperationError("helm", "ExecutableUnavailable")
    try:
        process = subprocess.Popen(
            [executable, *argv[1:]],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
        )
    except OSError:
        raise OperationError("helm", "ExecutableUnavailable") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as exc:
        # Also reap children on SIGINT/SIGTERM and the engine's hard deadline.
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        except ProcessLookupError:
            process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise OperationError("helm", "Timeout") from None
        raise
    if process.returncode:
        absent = "release: not found" in stderr.lower() or "release not found" in stderr.lower()
        raise OperationError("helm", "NotFound" if absent else "CommandFailed", absent=absent)
    return stdout


def dependency_build(
    context: Context, params: dict[str, Any], binary: str, timeout: float
) -> ActionResult:
    source = (context.base_dir / params["chart"]).resolve()
    root = (context.base_dir / (params["workspace_root"] or ".")).resolve()
    if not source.is_dir() or not source.is_relative_to(root):
        raise ValidationError("Local chart must be within workspaceRoot")
    chart = yaml.safe_load((source / "Chart.yaml").read_text())
    if chart.get("dependencies") and not (source / "Chart.lock").is_file():
        raise ValidationError(
            "Chart.lock missing; generate and commit the lock before dependency-build"
        )
    for dependency in chart.get("dependencies", []):
        repository = dependency.get("repository", "")
        if repository.startswith("file://") and not (
            source / repository[7:]
        ).resolve().is_relative_to(root):
            raise ValidationError(
                "Local dependency escapes workspaceRoot; include its parent explicitly"
            )
    context.prepare_directory()
    destination = Path(tempfile.mkdtemp(prefix="chart-", dir=context.directory))
    excluded = [
        ".git",
        ".env",
        ".env.*",
        ".pyintegrationtests",
        ".venv",
        "*.local.yaml",
        "*.local.yml",
    ]
    if context.directory.is_relative_to(root):
        excluded.append(context.directory.relative_to(root).parts[0])
    # Preserve relative file:// siblings while excluding runtime artifacts and credentials.
    shutil.copytree(
        root,
        destination / "workspace",
        ignore=shutil.ignore_patterns(*excluded),
    )
    work_chart = destination / "workspace" / source.relative_to(root)
    run_process(
        [binary, "dependency", "build", str(work_chart), "--skip-refresh"],
        timeout,
        cwd=destination,
        env=context.environment,
    )
    return ActionResult({"chart": str(work_chart)})


def command(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    import json

    assert provider is not None
    settings, kube = cluster(context, provider)
    if params["operation"] == "dependency-build":
        return dependency_build(context, params, settings.binary, timeout)
    flags = ["--kube-context", kube.context, "--namespace", kube.namespace]
    kubeconfig = kube.kubeconfig or context.environment.get("KUBECONFIG")
    if kubeconfig:
        flags.extend(["--kubeconfig", kubeconfig])
    op = params["operation"]
    release = params["release"]
    if op in {"install", "upgrade", "uninstall"}:
        try:
            status = json.loads(
                run_process(
                    [settings.binary, "status", release, "-o", "json", *flags],
                    min(timeout, 15),
                    env=context.environment,
                )
            )
        except OperationError as exc:
            if not exc.absent:
                raise
            if op == "uninstall":
                return ActionResult({"absent": True})
            if op == "upgrade":
                raise
        else:
            if (
                op == "install"
                or status.get("info", {}).get("description")
                != f"pyintegrationtests:{context.run_id}"
            ):
                raise OwnershipError("Helm release already exists or is not owned by this run")
    argv = [settings.binary]
    if op.startswith("get-"):
        argv += ["get", op.removeprefix("get-"), release]
    else:
        argv += [op, release]
    with tempfile.TemporaryDirectory(prefix="pit-values-") as directory:
        if op in {"install", "upgrade"}:
            chart = params["chart"]
            local_chart = context.base_dir / chart
            argv.append(str(local_chart.resolve()) if local_chart.exists() else chart)
            if params["version"]:
                argv.extend(["--version", params["version"]])
            for path in params["values_files"]:
                argv.extend(["--values", str((context.base_dir / path).resolve())])
            values = Path(directory) / "values.yaml"
            values.write_text(yaml.safe_dump(params["values"]))
            values.chmod(0o600)
            argv.extend(
                ["--values", str(values), "--description", f"pyintegrationtests:{context.run_id}"]
            )
        if op in {"install", "upgrade", "uninstall"}:
            argv.extend(["--timeout", f"{max(1, int(timeout))}s"])
            if params["wait"]:
                argv.append("--wait")
        if op in {"status", "get-values", "install", "upgrade"}:
            argv.extend(["--output", "json"])
        output = run_process([*argv, *flags], timeout, env=context.environment)
    if len(output.encode()) > context.config.artifacts.max_bytes:
        raise ValidationError("Helm response exceeds artifact byte limit")
    data = (
        json.loads(output)
        if op in {"status", "get-values", "install", "upgrade"}
        else {"text": output}
    )
    return ActionResult(data, {"provider": provider, "operation": op}, sensitive=True)


def register(registry: Registry) -> None:
    registry.register(
        Action(
            "helm.command", HelmParams, command, effect, "helm", sensitive=True, validate=validate
        )
    )
