import base64
import io
import zipfile
from types import SimpleNamespace

import dns.exception
import dns.resolver
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pyintegrationtests.config import DNSConfig
from pyintegrationtests.errors import OperationError, ValidationError
from pyintegrationtests.helpers import artifacts
from pyintegrationtests.helpers.jwt import JWTParams, sign
from pyintegrationtests.providers import dns as provider_dns


def test_zip_is_reproducible_private_and_hashes_match(context):
    params = artifacts.ZIPParams(
        files={"index.html": "fixture", "nested/app.js": "console.log(1)"}
    ).model_dump()
    first = artifacts.zip_action(context, None, params, 1)
    second = artifacts.zip_action(context, None, params, 1)
    assert first.data["sha256"] == second.data["sha256"]
    from pathlib import Path

    path = Path(first.data["path"])
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        assert archive.read("index.html") == b"fixture"
    assert path.stat().st_mode & 0o777 == 0o600
    digest = artifacts.hash_action(
        context, None, artifacts.HashParams(value={"$file": str(path)}).model_dump(), 1
    )
    assert digest.data["hex"] == first.data["sha256"]


@pytest.mark.parametrize("path", ["../escape", "/absolute", "dir/../../escape", "dir\\escape"])
def test_zip_rejects_traversal(context, path):
    with pytest.raises(ValidationError):
        artifacts.zip_action(
            context, None, artifacts.ZIPParams(files={path: "fixture"}).model_dump(), 1
        )


def test_artifact_binary_and_file_encodings(context):
    path = context.base_dir / "fixture.json"
    path.write_text('{"v": 1}')
    for encoding, expected in (
        ("text", '{"v": 1}'),
        ("base64", base64.b64encode(path.read_bytes()).decode()),
        ("json", {"v": 1}),
    ):
        result = artifacts.file_action(
            context,
            None,
            artifacts.FileParams(path="fixture.json", encoding=encoding).model_dump(),
            1,
        )
        assert result.data == expected
    assert artifacts.binary({"data": {"$bytes": "YWJj"}}, context) == {"data": b"abc"}
    context.config.artifacts.max_bytes = 2
    with pytest.raises(ValidationError):
        artifacts.binary({"$file": str(path)}, context)


def test_jwt_rs256_fixture_uses_pyjwt_and_masks_key(context):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    context.environment["FIXTURE_KEY"] = pem
    token = sign(
        context,
        None,
        JWTParams(key_env="FIXTURE_KEY", claims={"sub": "fixture", "aud": "service"}).model_dump(),
        1,
    )
    assert (
        jwt.decode(token.data["token"], key.public_key(), algorithms=["RS256"], audience="service")[
            "sub"
        ]
        == "fixture"
    )
    assert token.sensitive and context.redactor.text(pem) == "[REDACTED]"
    with pytest.raises(ValidationError):
        sign(context, None, JWTParams(key_env="MISSING", claims={}).model_dump(), 1)


class Answer:
    qname = "fixture.example."
    canonical_name = "target.example."
    rrset = SimpleNamespace(ttl=120)
    response = SimpleNamespace(to_text=lambda: "DNS full response")

    def __iter__(self):
        return iter([SimpleNamespace(to_text=lambda: "192.0.2.1")])


@pytest.mark.parametrize(
    "mode,status", [("answer", "ANSWER"), ("nodata", "NODATA"), ("nxdomain", "NXDOMAIN")]
)
def test_dns_structured_answers_and_absence(context, mode, status):
    context.config.providers["dns"] = DNSConfig(kind="dns", nameservers=["192.0.2.53"])

    def resolve(*args, **kwargs):
        if mode == "nxdomain":
            raise dns.resolver.NXDOMAIN
        answer = Answer()
        if mode == "nodata":
            answer.rrset = None
        return answer

    context.clients["dns"] = SimpleNamespace(resolve=resolve)
    result = provider_dns.query(
        context, "dns", provider_dns.DNSParams(name="fixture.example").model_dump(), 1
    )
    assert result.data["status"] == status
    if mode == "answer":
        assert result.data["ttl"] == 120 and result.data["canonicalName"].endswith(".")


@pytest.mark.parametrize("error", [dns.exception.Timeout, dns.resolver.NoNameservers])
def test_dns_errors_are_not_absence(context, error):
    context.config.providers["dns"] = DNSConfig(kind="dns")

    def resolve(*args, **kwargs):
        raise error

    context.clients["dns"] = SimpleNamespace(resolve=resolve)
    with pytest.raises(OperationError) as exc:
        provider_dns.query(
            context, "dns", provider_dns.DNSParams(name="fixture.example").model_dump(), 1
        )
    assert not exc.value.absent
