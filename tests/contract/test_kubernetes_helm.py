import copy
import json
import subprocess
from unittest.mock import MagicMock

import pytest
import yaml
from kubernetes.client.exceptions import ApiException

from pyintegrationtests.config import HelmConfig, KubernetesConfig
from pyintegrationtests.contracts import thaw
from pyintegrationtests.errors import (
    AssertionFailure,
    IntegrationError,
    OperationError,
    OwnershipError,
    ValidationError,
)
from pyintegrationtests.helpers import ack
from pyintegrationtests.providers import helm, kubernetes


def kube_context(context, data):
    context.config.providers["cluster"] = KubernetesConfig(
        kind="kubernetes", context="sandbox", namespace="fixtures"
    )
    resource = MagicMock()
    resource.namespaced = True
    resource.get.return_value.to_dict.side_effect = lambda: copy.deepcopy(data)
    dynamic = MagicMock()
    dynamic.resources.get.return_value = resource
    context.clients["cluster:api"] = MagicMock()
    context.clients["cluster:dynamic"] = dynamic
    return resource


def params(**kwargs):
    return kubernetes.ResourceParams(
        api_version="apps/v1", kind="Deployment", name="fixture", **kwargs
    ).model_dump()


def test_native_fields_extra_cr_fields_and_mutation_guard(context):
    data = {
        "metadata": {"uid": "uid", "labels": {"pyintegrationtests.io/run": context.run_id}},
        "spec": {
            "template": {"spec": {"containers": [{"livenessProbe": {"httpGet": {"path": "/"}}}]}}
        },
        "status": {"readyReplicas": 1, "futureField": "preserved"},
    }
    api = kube_context(context, data)
    result = kubernetes.resource(context, "cluster", params(), 1)
    assert thaw(result.data) == data
    api.delete.return_value.to_dict.return_value = {"status": "Success"}
    kubernetes.resource(context, "cluster", params(operation="delete"), 1)
    assert api.delete.call_args.kwargs["body"]["preconditions"] == {"uid": "uid"}
    data["metadata"]["labels"].clear()
    with pytest.raises(OwnershipError):
        kubernetes.resource(
            context, "cluster", params(operation="patch", body={"spec": {"replicas": 2}}), 1
        )
    api.patch.assert_not_called()


def test_cluster_scope_requires_explicit_allowance(context):
    api = kube_context(context, {})
    api.namespaced = False
    with pytest.raises(OwnershipError):
        kubernetes.resource(context, "cluster", params(), 1)


@pytest.mark.parametrize("status,absent", [(404, True), (403, False), (429, False)])
def test_kube_error_classification(context, status, absent):
    api = kube_context(context, {})
    api.get.side_effect = ApiException(status=status, reason="secret response")
    with pytest.raises(OperationError) as exc:
        kubernetes.resource(context, "cluster", params(), 1)
    assert exc.value.absent is absent and "secret" not in str(exc.value)


def test_kube_list_preserves_pages(context):
    api = kube_context(context, {})
    api.get.return_value.to_dict.side_effect = [
        {"items": [{"unknown": 1}], "metadata": {"continue": "next"}},
        {"items": [{"unknown": 2}], "metadata": {}},
    ]
    result = kubernetes.resource(context, "cluster", params(operation="list"), 1)
    assert thaw(result.data)["items"] == [{"unknown": 1}, {"unknown": 2}]
    assert api.get.call_args.kwargs["_continue"] == "next"


def test_create_stamps_owner_and_does_not_mutate_source(context):
    api = kube_context(context, {})
    body = {"metadata": {"name": "fixture"}, "spec": {"replicas": 1}}
    api.create.return_value.to_dict.return_value = body
    kubernetes.resource(context, "cluster", params(operation="create", body=body), 1)
    assert "labels" not in body["metadata"]
    assert (
        api.create.call_args.kwargs["body"]["metadata"]["labels"]["pyintegrationtests.io/run"]
        == context.run_id
    )


def test_fresh_deployment_and_terminal_ack_job():
    stale = {
        "metadata": {"generation": 2},
        "spec": {"replicas": 1},
        "status": {
            "observedGeneration": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "updatedReplicas": 1,
        },
    }
    with pytest.raises(AssertionFailure, match="stale"):
        ack.deployment(stale)
    stale["status"]["observedGeneration"] = 2
    ack.deployment(stale)
    conditions = [
        {"type": "ACK.ResourceSynced", "status": "True"},
        {"type": "ACK.Terminal", "status": "True"},
    ]
    with pytest.raises(IntegrationError, match="terminal"):
        ack.conditions({"status": {"conditions": conditions}})
    with pytest.raises(AssertionFailure, match="generation"):
        ack.conditions({"status": {"conditions": conditions[:1]}}, expected_generation=2)
    with pytest.raises(IntegrationError, match="Job failed"):
        ack.job(
            {
                "status": {
                    "conditions": [
                        {"type": "Complete", "status": "True"},
                        {"type": "Failed", "status": "True"},
                    ]
                }
            }
        )


def test_clone_job_and_cron_template_are_recreatable():
    original = {
        "metadata": {
            "uid": "old",
            "resourceVersion": "12",
            "name": "old",
            "ownerReferences": [{"uid": "x"}],
        },
        "spec": {
            "selector": {"generated": "old"},
            "manualSelector": False,
            "template": {
                "metadata": {"labels": {"job-name": "old", "controller-uid": "old", "app": "keep"}},
                "spec": {
                    "containers": [{"name": "fixture", "image": "busybox"}],
                    "restartPolicy": "Never",
                },
            },
        },
        "status": {"succeeded": 1},
    }
    result = kubernetes.job_body(original, "new", cron=False, labels={"owner": "run"})
    assert "uid" not in result["metadata"] and "status" not in result
    assert "selector" not in result["spec"]
    assert result["spec"]["template"]["metadata"]["labels"] == {"app": "keep"}
    assert "selector" in original["spec"]
    assert (
        kubernetes.job_body({"spec": {"jobTemplate": original}}, "cron-run", cron=True, labels={})[
            "metadata"
        ]["name"]
        == "cron-run"
    )


def test_exec_returns_separate_streams_and_exit_code(context, monkeypatch):
    kube_context(context, {})
    websocket = MagicMock()
    websocket.is_open.side_effect = [True, False]
    websocket.read_stdout.return_value = "stdout fixture"
    websocket.read_stderr.return_value = "permission denied"
    websocket.returncode = 13
    stream = MagicMock(return_value=websocket)
    monkeypatch.setattr("kubernetes.stream.stream", stream)
    result = kubernetes.execute(
        context,
        "cluster",
        kubernetes.ExecParams(
            name="pod", container="sidecar", argv=["sh", "-c", "echo fixture"]
        ).model_dump(),
        1,
    )
    assert result.data["exitCode"] == 13 and result.data["stderr"] == "permission denied"
    assert stream.call_args.kwargs["container"] == "sidecar"
    assert stream.call_args.kwargs["command"] == ["sh", "-c", "echo fixture"]
    websocket.close.assert_called_once()


def helm_context(context):
    context.config.providers["cluster"] = KubernetesConfig(
        kind="kubernetes", context="sandbox", namespace="fixtures"
    )
    context.config.providers["charts"] = HelmConfig(kind="helm", kubernetes_provider="cluster")


def test_helm_install_upgrade_uninstall_reinstall_and_typed_values(context, monkeypatch):
    helm_context(context)
    releases, calls = {}, []

    def process(argv, timeout, **kwargs):
        calls.append(argv)
        operation, release = argv[1:3]
        assert argv[argv.index("--kube-context") + 1] == "sandbox"
        if operation == "status":
            if release not in releases:
                raise OperationError("helm", "NotFound", absent=True)
            return json.dumps(releases[release])
        if operation == "uninstall":
            del releases[release]
            return "uninstalled"
        assert operation in {"install", "upgrade"}
        assert "--atomic" not in argv
        from pathlib import Path

        runtime_values = Path(argv[argv.index("--description") - 1])
        values = yaml.safe_load(runtime_values.read_text())
        assert values == {"replicas": 2, "enabled": False, "numericString": "123"}
        assert runtime_values.stat().st_mode & 0o777 == 0o600
        releases[release] = {
            "info": {"description": f"pyintegrationtests:{context.run_id}"},
            "version": len(calls),
        }
        return json.dumps(releases[release])

    monkeypatch.setattr(helm, "run_process", process)
    for operation in ("install", "upgrade", "uninstall", "install", "uninstall"):
        kwargs = {
            "chart": "oci://registry.invalid/chart",
            "values": {"replicas": 2, "enabled": False, "numericString": "123"},
        }
        helm.command(
            context,
            "charts",
            helm.HelmParams(operation=operation, release="fixture", **kwargs).model_dump(),
            1,
        )
    assert releases == {}


def test_helm_preexisting_release_is_rejected(context, monkeypatch):
    helm_context(context)
    monkeypatch.setattr(
        helm, "run_process", lambda *a, **kw: json.dumps({"info": {"description": "external"}})
    )
    with pytest.raises(OwnershipError):
        helm.command(
            context,
            "charts",
            helm.HelmParams(operation="upgrade", release="fixture", chart="chart").model_dump(),
            1,
        )


def test_helm_subprocess_uses_argv_and_kills_timed_out_group(monkeypatch):
    process = MagicMock(pid=123, returncode=0)
    process.communicate.side_effect = [subprocess.TimeoutExpired("helm", 1), ("", "")]
    popen, kill = MagicMock(return_value=process), MagicMock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(helm.os, "killpg", kill)
    with pytest.raises(OperationError, match="Timeout"):
        helm.run_process(["helm", "status", "fixture"], 1)
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["start_new_session"] is True
    kill.assert_called_once()


def test_helm_dependency_build_requires_lock_and_does_not_modify_source(context):
    root = context.base_dir / "charts"
    chart = root / "parent"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\n"
        "name: parent\n"
        "version: 0.1.0\n"
        "dependencies:\n"
        "  - name: child\n"
        "    version: 0.1.0\n"
        "    repository: file://../child\n"
    )
    with pytest.raises(ValidationError, match=r"Chart\.lock"):
        helm.dependency_build(
            context,
            helm.HelmParams(
                operation="dependency-build", chart="charts/parent", workspace_root="charts"
            ).model_dump(),
            "helm",
            1,
        )


def test_helm_binary_is_real_and_available():
    result = subprocess.run(
        ["helm", "version", "--short"], check=True, capture_output=True, text=True
    )
    assert result.stdout.startswith("v3.19.0")


def test_dependency_build_preserves_local_siblings_and_source_lock(context):
    from pathlib import Path

    root = context.base_dir / "charts"
    parent, child = root / "parent", root / "child"
    parent.mkdir(parents=True)
    child.mkdir()
    (child / "Chart.yaml").write_text("apiVersion: v2\nname: child\nversion: 0.1.0\n")
    (parent / "Chart.yaml").write_text(
        "apiVersion: v2\n"
        "name: parent\n"
        "version: 0.1.0\n"
        "dependencies:\n"
        "  - name: child\n"
        "    version: 0.1.0\n"
        "    repository: file://../child\n"
    )
    # Generate the test fixture's lock explicitly, not through the library under test.
    subprocess.run(["helm", "dependency", "update", str(parent)], check=True, capture_output=True)
    before = (parent / "Chart.lock").read_bytes()
    result = helm.dependency_build(
        context,
        helm.HelmParams(
            operation="dependency-build", chart="charts/parent", workspace_root="charts"
        ).model_dump(),
        "helm",
        5,
    )
    copied = Path(result.data["chart"])
    assert copied != parent and (copied / "charts/child-0.1.0.tgz").is_file()
    assert (parent / "Chart.lock").read_bytes() == before
    assert (copied / "Chart.lock").read_bytes() == before
