"""Dynamic Kubernetes resources retain native JSON names and unknown CR fields."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ..config import KubernetesConfig
from ..context import Context
from ..contracts import Action, ActionResult, Effect
from ..errors import OperationError, OwnershipError, ValidationError
from ..helpers import ack
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class ResourceParams(Model):
    api_version: str
    kind: str
    namespace: str | None = None
    name: str | None = None
    label_selector: str | None = None
    field_selector: str | None = None
    body: dict[str, Any] | None = None
    operation: Literal["get", "list", "create", "apply", "patch", "delete"] = "get"
    patch_type: Literal["merge", "json", "strategic"] = "merge"
    json_patch: list[dict[str, Any]] | None = None
    readiness: Literal["deployment", "job", "ack"] | None = None
    expected_generation: int | None = None
    max_pages: int = Field(default=100, ge=1, le=10000)


class PodParams(Model):
    namespace: str | None = None
    name: str
    container: str
    argv: list[str] = Field(default_factory=list)
    tail_lines: int = Field(default=200, ge=1, le=10000)
    since_seconds: int | None = Field(default=None, ge=1)


class JobParams(Model):
    namespace: str | None = None
    source: str
    name: str
    from_cron_job: bool = False
    labels: dict[str, str] = Field(default_factory=dict)


def clients(context: Context, provider: str) -> tuple[Any, Any]:
    from kubernetes import config as kube_config
    from kubernetes.dynamic import DynamicClient

    settings = context.config.providers[provider]
    assert isinstance(settings, KubernetesConfig)
    key = f"{provider}:api"
    if key not in context.clients:
        if not settings.context or settings.context.startswith("REPLACE"):
            raise ValidationError("Explicit Kubernetes sandbox context is required")
        api = kube_config.new_client_from_config(
            config_file=settings.kubeconfig or context.environment.get("KUBECONFIG"),
            context=settings.context,
            persist_config=False,
        )
        context.clients[key] = api
        context.clients[f"{provider}:dynamic"] = DynamicClient(api)
    return context.clients[key], context.clients[f"{provider}:dynamic"]


def identity(context: Context, provider: str) -> dict[str, Any]:
    settings = context.config.providers[provider]
    assert isinstance(settings, KubernetesConfig)
    api, _ = clients(context, provider)
    certificate = api.configuration.ssl_ca_cert
    digest = hashlib.sha256(Path(certificate).read_bytes()).hexdigest() if certificate else None
    return {
        "kind": "kubernetes",
        "context": settings.context,
        "server": api.configuration.host,
        "namespace": settings.namespace,
        "caSha256": digest,
    }


def effect(params: dict[str, Any], config: Any) -> Effect:
    return "read" if params.get("operation", "get") in {"get", "list"} else "mutate"


def validate(params: dict[str, Any], config: Any) -> None:
    op = params.get("operation", "get")
    if op != "list" and not params.get("name"):
        raise ValidationError("Kubernetes operation requires an exact name")
    if op in {"create", "apply"} and not params.get("body"):
        raise ValidationError("Kubernetes mutation requires a body")
    if params.get("readiness") and op != "get":
        raise ValidationError("Readiness requires a fresh get")


def translate(exc: Exception) -> OperationError:
    status = getattr(exc, "status", None)
    return OperationError(
        "kubernetes",
        str(status or type(exc).__name__),
        status,
        retryable=status in {429, 500, 502, 503, 504},
        absent=status == 404,
    )


def resource(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from kubernetes.client.exceptions import ApiException
    from kubernetes.dynamic.exceptions import DynamicApiError

    assert provider is not None
    settings = context.config.providers[provider]
    assert isinstance(settings, KubernetesConfig)
    _, dynamic = clients(context, provider)
    api = dynamic.resources.get(api_version=params["api_version"], kind=params["kind"])
    if not api.namespaced and not settings.allow_cluster_scoped:
        raise OwnershipError("Cluster-scoped access requires allowClusterScoped")
    namespace = params["namespace"] or settings.namespace
    kwargs: dict[str, Any] = {"_request_timeout": timeout}
    if api.namespaced:
        kwargs["namespace"] = namespace
    if params["name"]:
        kwargs["name"] = params["name"]
    operation = params["operation"]
    body = copy.deepcopy(params["body"])
    if body is not None:
        if (
            body.get("apiVersion", params["api_version"]) != params["api_version"]
            or body.get("kind", params["kind"]) != params["kind"]
        ):
            raise ValidationError("Body apiVersion/kind differs from target")
        metadata = body.setdefault("metadata", {})
        if metadata.get("namespace", namespace) != namespace or (
            params["name"] and metadata.get("name", params["name"]) != params["name"]
        ):
            raise ValidationError("Body metadata differs from exact target")
        if operation in {"create", "apply"}:
            metadata["name"] = params["name"]
            metadata.setdefault("labels", {})["pyintegrationtests.io/run"] = context.run_id
    try:
        if operation == "list":
            kwargs.pop("name", None)
            kwargs.update(
                label_selector=params["label_selector"],
                field_selector=params["field_selector"],
                limit=500,
            )
            collected: list[Any] = []
            for _ in range(params["max_pages"]):
                context.remaining()
                response = api.get(**kwargs).to_dict()
                collected.extend(response.get("items", []))
                token = response.get("metadata", {}).get("continue")
                if not token:
                    response["items"] = collected
                    return ActionResult(response, {"complete": True})
                kwargs["_continue"] = token
            raise ValidationError("Kubernetes pagination exceeded maxPages")
        if operation == "get":
            response = api.get(**kwargs).to_dict()
        elif operation == "create":
            response = api.create(body=body, **kwargs).to_dict()
        else:
            try:
                current = api.get(**kwargs).to_dict()
            except (ApiException, DynamicApiError) as exc:
                if operation != "apply" or getattr(exc, "status", None) != 404:
                    raise
                current = None
            if current is None:
                # A create must conflict if another writer wins the race.
                response = api.create(body=body, **kwargs).to_dict()
            elif (
                current.get("metadata", {}).get("labels", {}).get("pyintegrationtests.io/run")
                != context.run_id
            ):
                raise OwnershipError("Kubernetes mutation target is not owned by this run")
            elif operation == "delete":
                uid = current["metadata"]["uid"]
                response = api.delete(
                    body={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "preconditions": {"uid": uid},
                    },
                    **kwargs,
                ).to_dict()
            else:
                patch_body = params["json_patch"] if params["patch_type"] == "json" else body
                if isinstance(patch_body, list):
                    patch_body = [
                        {
                            "op": "test",
                            "path": "/metadata/uid",
                            "value": current["metadata"]["uid"],
                        },
                        {
                            "op": "test",
                            "path": "/metadata/resourceVersion",
                            "value": current["metadata"]["resourceVersion"],
                        },
                        *patch_body,
                    ]
                elif isinstance(patch_body, dict):
                    patch_body.setdefault("metadata", {}).update(
                        uid=current["metadata"]["uid"],
                        resourceVersion=current["metadata"]["resourceVersion"],
                    )
                else:
                    raise ValidationError("Kubernetes patch requires a compatible patch body")
                content_type = {
                    "merge": "application/merge-patch+json",
                    "json": "application/json-patch+json",
                    "strategic": "application/strategic-merge-patch+json",
                }[params["patch_type"]]
                if operation == "apply":
                    content_type = "application/apply-patch+yaml"
                    kwargs.update(field_manager="pyintegrationtests", force=False)
                response = api.patch(body=patch_body, content_type=content_type, **kwargs).to_dict()
        if params["readiness"] == "deployment":
            ack.deployment(response)
        elif params["readiness"] == "job":
            ack.job(response)
        elif params["readiness"] == "ack":
            ack.conditions(response, expected_generation=params["expected_generation"])
        return ActionResult(
            response,
            {"provider": provider, "operation": operation},
            sensitive=params["kind"] == "Secret",
        )
    except (ApiException, DynamicApiError) as exc:
        raise translate(exc) from None


def logs(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from kubernetes.client import CoreV1Api
    from kubernetes.client.exceptions import ApiException

    assert provider is not None
    settings = context.config.providers[provider]
    assert isinstance(settings, KubernetesConfig)
    api, _ = clients(context, provider)
    try:
        text = CoreV1Api(api).read_namespaced_pod_log(
            params["name"],
            params["namespace"] or settings.namespace,
            container=params["container"],
            tail_lines=params["tail_lines"],
            since_seconds=params["since_seconds"],
            limit_bytes=context.config.artifacts.max_bytes,
            _request_timeout=timeout,
        )
        return ActionResult({"text": text}, sensitive=True)
    except ApiException as exc:
        raise translate(exc) from None


def execute(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from kubernetes.client import CoreV1Api
    from kubernetes.stream import stream

    assert provider is not None
    if not params["argv"]:
        raise ValidationError("Kubernetes exec requires explicit argv")
    settings = context.config.providers[provider]
    assert isinstance(settings, KubernetesConfig)
    api, _ = clients(context, provider)
    response = stream(
        CoreV1Api(api).connect_get_namespaced_pod_exec,
        params["name"],
        params["namespace"] or settings.namespace,
        container=params["container"],
        command=params["argv"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
        _request_timeout=timeout,
    )
    stdout, stderr = "", ""
    deadline = min(context.deadline, context.clock.now() + timeout)
    try:
        while response.is_open():
            if context.clock.now() >= deadline:
                raise OperationError("kubernetes", "ExecTimeout")
            response.update(timeout=min(1, deadline - context.clock.now()))
            stdout += response.read_stdout()
            stderr += response.read_stderr()
            if len(stdout.encode()) + len(stderr.encode()) > context.config.artifacts.max_bytes:
                raise ValidationError("Kubernetes exec output exceeds artifact byte limit")
        return ActionResult(
            {"stdout": stdout, "stderr": stderr, "exitCode": response.returncode}, sensitive=True
        )
    finally:
        response.close()


def job_body(
    source: dict[str, Any], name: str, *, cron: bool, labels: dict[str, str]
) -> dict[str, Any]:
    original = source["spec"]["jobTemplate"] if cron else source
    body = copy.deepcopy(original)
    body.pop("status", None)
    metadata = body.get("metadata", {})
    body["metadata"] = {
        "name": name,
        "labels": {**metadata.get("labels", {}), **labels},
        "annotations": metadata.get("annotations", {}),
    }
    body.update(apiVersion="batch/v1", kind="Job")
    spec = body["spec"]
    spec.pop("selector", None)
    spec.pop("manualSelector", None)
    template_labels = spec.get("template", {}).get("metadata", {}).get("labels", {})
    for key in (
        "controller-uid",
        "job-name",
        "batch.kubernetes.io/controller-uid",
        "batch.kubernetes.io/job-name",
    ):
        template_labels.pop(key, None)
    return body


def create_job(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    source_params = ResourceParams(
        api_version="batch/v1",
        kind="CronJob" if params["from_cron_job"] else "Job",
        namespace=params["namespace"],
        name=params["source"],
    )
    from ..contracts import thaw

    source = resource(context, provider, source_params.model_dump(), timeout)
    body = job_body(
        thaw(source.data), params["name"], cron=params["from_cron_job"], labels=params["labels"]
    )
    target = ResourceParams(
        api_version="batch/v1",
        kind="Job",
        namespace=params["namespace"],
        name=params["name"],
        operation="create",
        body=body,
    )
    return resource(context, provider, target.model_dump(), timeout)


def register(registry: Registry) -> None:
    registry.register(
        Action(
            "kubernetes.resource", ResourceParams, resource, effect, "kubernetes", validate=validate
        )
    )
    registry.register(
        Action("kubernetes.logs", PodParams, logs, "read", "kubernetes", sensitive=True)
    )
    registry.register(
        Action("kubernetes.exec", PodParams, execute, "mutate", "kubernetes", sensitive=True)
    )
    registry.register(Action("kubernetes.job", JobParams, create_job, "mutate", "kubernetes"))
