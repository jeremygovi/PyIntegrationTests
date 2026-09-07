import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import yaml
from cloudflare import Cloudflare
from kubernetes.client.exceptions import ApiException

from pyintegrationtests.cleanup import cleanup
from pyintegrationtests.context import Context
from pyintegrationtests.engine import execute_case
from pyintegrationtests.errors import OperationError
from pyintegrationtests.journal import Journal
from pyintegrationtests.providers import helm
from pyintegrationtests.pytest_plugin import load_suite

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_complete_cloudflare_example_with_official_sdk_and_exact_cleanup(tmp_path):
    _, suite, config, _, registry = load_suite(EXAMPLES / "cloudflare-record/record.integ.yaml")
    config.artifacts.directory = str(tmp_path / "runs")
    context = Context(
        config,
        {},
        registry,
        suite.id,
        suite.tests[0].id,
        EXAMPLES / "cloudflare-record",
        suite.inputs,
    )
    records, mutations = {}, []
    zone_id = config.providers["cloudflare"].zone_id

    def handler(request):
        path = request.url.path
        if path.endswith(f"/zones/{zone_id}"):
            assert request.method == "GET"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {"id": zone_id, "account": {"id": "sandbox-account"}},
                },
            )
        if request.method == "GET":
            if path.endswith("/dns_records"):
                result = [
                    record
                    for record in records.values()
                    if record["name"] == request.url.params["name"]
                ]
                return httpx.Response(200, json={"success": True, "result": result})
            if not records:
                return httpx.Response(404, json={"success": False, "errors": [{"code": 81044}]})
            return httpx.Response(200, json={"success": True, "result": records["record-id"]})
        mutations.append((request.method, path))
        if request.method == "POST":
            records["record-id"] = {
                **json.loads(request.content),
                "id": "record-id",
                "future": {"nullable": None},
            }
        elif request.method == "PATCH":
            records["record-id"].update(json.loads(request.content))
        else:
            del records["record-id"]
        return httpx.Response(
            200, json={"success": True, "result": records.get("record-id", {"id": "record-id"})}
        )

    context.clients["cloudflare"] = Cloudflare(
        api_token="test-token",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    context.prepare_directory()
    journal = Journal.create(context)
    execute_case(context, journal, suite.tests[0])
    assert cleanup(context, journal) == []
    assert cleanup(context, Journal.load(journal.path, config)) == []
    assert not records
    assert [method for method, _ in mutations] == ["POST", "PATCH", "DELETE"]
    assert "test-token" not in journal.path.read_text()
    assert context.results["create"].data["result"]["content"] == "192.0.2.10"
    assert context.results["read-updated"].data["result"]["content"] == "192.0.2.20"
    context.close()


def test_complete_helm_example_with_fresh_resources_and_absence(tmp_path, monkeypatch):
    _, suite, config, _, registry = load_suite(EXAMPLES / "helm-workload/workload.integ.yaml")
    config.artifacts.directory = str(tmp_path / "runs")
    context = Context(
        config, {}, registry, suite.id, suite.tests[0].id, EXAMPLES / "helm-workload", suite.inputs
    )
    state = {}

    def process(argv, timeout, **kwargs):
        if argv[1] == "status":
            if not state:
                raise OperationError("helm", "NotFound", absent=True)
            return json.dumps(state["release"])
        if argv[1] == "install":
            values = yaml.safe_load(Path(argv[argv.index("--description") - 1]).read_text())
            assert values["replicas"] == 1 and values["runId"] == context.run_id
            state["release"] = {"info": {"description": f"pyintegrationtests:{context.run_id}"}}
            return json.dumps(state["release"])
        assert argv[1] == "uninstall"
        state.clear()
        return "uninstalled"

    def get(**kwargs):
        if not state:
            raise ApiException(status=404)
        return SimpleNamespace(
            to_dict=lambda: {
                "metadata": {
                    "generation": 1,
                    "labels": {"app": "fixture"},
                    "annotations": {"example.com/purpose": "integration-test"},
                },
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {"containers": [{"livenessProbe": {"httpGet": {"path": "/"}}}]}
                    },
                },
                "status": {
                    "observedGeneration": 1,
                    "readyReplicas": 1,
                    "availableReplicas": 1,
                    "updatedReplicas": 1,
                },
            }
        )

    resource = SimpleNamespace(namespaced=True, get=get)
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kwargs: resource))
    context.clients["cluster:api"] = SimpleNamespace(
        configuration=SimpleNamespace(host="https://sandbox.invalid", ssl_ca_cert=None)
    )
    context.clients["cluster:dynamic"] = dynamic
    monkeypatch.setattr(helm, "run_process", process)
    context.prepare_directory()
    journal = Journal.create(context)
    execute_case(context, journal, suite.tests[0])
    assert cleanup(context, journal) == [] and not state
    assert context.results["deployment-gone"].data is None
    assert context.results["service-gone"].data is None
    context.close()


def test_example_chart_renders_with_real_helm():
    import subprocess

    chart = EXAMPLES / "helm-workload/chart"
    subprocess.run(["helm", "lint", str(chart), "--strict"], check=True, capture_output=True)
    result = subprocess.run(
        ["helm", "template", "fixture", str(chart), "--set-string", "runId=fixture-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    resources = list(yaml.safe_load_all(result.stdout))
    assert {resource["kind"] for resource in resources} == {"Deployment", "Service"}
    assert all(
        resource["metadata"]["labels"]["pyintegrationtests.io/run"] == "fixture-run"
        for resource in resources
    )
