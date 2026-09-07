"""Provider configuration; loading never constructs a client or mutates os.environ."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import dotenv_values
from pydantic import Field

from .schema import Model, load_document, parse_model


class KubernetesConfig(Model):
    kind: Literal["kubernetes"]
    context: str
    namespace: str
    kubeconfig: str | None = None
    allow_cluster_scoped: bool = False


class HelmConfig(Model):
    kind: Literal["helm"]
    kubernetes_provider: str
    binary: str = "helm"


class AWSConfig(Model):
    kind: Literal["aws"]
    profile: str | None = None
    region: str
    account_id: str = Field(pattern=r"^\d{12}$")
    endpoint_url: str | None = None
    verify: bool | str = True
    # Explicit classifications, never inferred from SDK operation names.
    operations: dict[str, Literal["read", "mutate"]] = Field(default_factory=dict)


class CloudflareConfig(Model):
    kind: Literal["cloudflare"]
    api_token_env: str = "CLOUDFLARE_API_TOKEN"
    base_url: str = "https://api.cloudflare.com/client/v4"
    zone_id: str


class HTTPConfig(Model):
    kind: Literal["http"]
    base_url: str | None = None
    verify: bool | str = True
    proxy: str | None = None
    trust_env: bool = False


class DNSConfig(Model):
    kind: Literal["dns"]
    nameservers: list[str] = Field(default_factory=list)


type ProviderConfig = Annotated[
    KubernetesConfig | HelmConfig | AWSConfig | CloudflareConfig | HTTPConfig | DNSConfig,
    Field(discriminator="kind"),
]


class Timeouts(Model):
    case_seconds: float = Field(default=900, gt=0, le=86400)
    observation_seconds: float = Field(default=15, gt=0, le=3600)
    cleanup_seconds: float = Field(default=300, gt=0, le=3600)


class CleanupConfig(Model):
    policy: Literal["strict"] = "strict"


class ArtifactsConfig(Model):
    directory: str = ".pyintegrationtests/runs"
    max_bytes: int = Field(default=8 * 1024 * 1024, gt=0, le=100 * 1024 * 1024)


class Config(Model):
    api_version: Literal["pyintegrationtests/v1alpha1"] = "pyintegrationtests/v1alpha1"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    plugins: list[str] = Field(default_factory=list)


ENV_OPTIONS: dict[str, tuple[str, str]] = {
    "PYINTEGRATIONTESTS_CASE_SECONDS": ("timeouts", "caseSeconds"),
    "PYINTEGRATIONTESTS_OBSERVATION_SECONDS": ("timeouts", "observationSeconds"),
    "PYINTEGRATIONTESTS_CLEANUP_SECONDS": ("timeouts", "cleanupSeconds"),
    "PYINTEGRATIONTESTS_ARTIFACTS_DIRECTORY": ("artifacts", "directory"),
}


def load_config(
    path: Path | None = None,
    env_file: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[Config, dict[str, str]]:
    from .schema import Document

    environment = dict(os.environ)
    if env_file is not None:
        if not env_file.is_file():
            from .errors import ValidationError

            raise ValidationError(f"{env_file}: env file does not exist")
        environment = {
            **{
                k: v for k, v in dotenv_values(env_file, interpolate=False).items() if v is not None
            },
            **environment,
        }
    doc = load_document(path) if path else Document(Path("<config>"), {}, {})
    data = dict(doc.data)
    for key, (section, field) in ENV_OPTIONS.items():
        if key in environment:
            data[section] = {**data.get(section, {}), field: environment[key]}
    for section, values in (overrides or {}).items():
        data[section] = {**data.get(section, {}), **values}
    return parse_model(Document(doc.path, data, doc.locations), Config), environment
