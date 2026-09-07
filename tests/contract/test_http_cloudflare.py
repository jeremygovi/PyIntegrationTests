import httpx
import pytest
from cloudflare import Cloudflare

from pyintegrationtests.config import CloudflareConfig, HTTPConfig
from pyintegrationtests.errors import OperationError, OwnershipError, ValidationError
from pyintegrationtests.providers import cloudflare, http


def http_params(**kwargs):
    return http.HTTPParams(url="https://fixture.invalid/test", **kwargs).model_dump()


def test_http_full_response_and_explicit_redirects(context):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            302, headers={"location": "/next", "x-future-field": "kept"}, json={"extra": {"v": 4}}
        )

    context.config.providers["web"] = HTTPConfig(kind="http")
    context.clients["web"] = httpx.Client(transport=httpx.MockTransport(handle))
    result = http.request(
        context, "web", http_params(headers={"Authorization": "Bearer fixture-secret"}), 1
    )
    assert len(seen) == 1
    assert result.data["status"] == 302
    assert result.data["headers"]["x-future-field"] == "kept"
    assert result.data["json"]["extra"]["v"] == 4
    assert context.redactor.text("Bearer fixture-secret") == "[REDACTED]"


@pytest.mark.parametrize(
    "status,absent,retry", [(404, True, False), (403, False, False), (429, False, True)]
)
def test_http_targeted_errors(context, status, absent, retry):
    context.config.providers["web"] = HTTPConfig(kind="http")
    context.clients["web"] = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status))
    )
    with pytest.raises(OperationError) as exc:
        http.request(context, "web", http_params(raise_for_status=True), 1)
    assert exc.value.absent is absent and exc.value.retryable is retry


def test_http_size_bound_and_transport_errors(context):
    context.config.providers["web"] = HTTPConfig(kind="http")
    context.config.artifacts.max_bytes = 4
    context.clients["web"] = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"12345"))
    )
    with pytest.raises(ValidationError, match="limit"):
        http.request(context, "web", http_params(), 1)

    def timeout(request):
        raise httpx.ReadTimeout("do not leak this", request=request)

    context.clients["web"] = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(OperationError, match="Timeout"):
        http.request(context, "web", http_params(), 1)


def cf_context(context, handler):
    context.config.providers["cf"] = CloudflareConfig(kind="cloudflare", zone_id="sandbox-zone")
    context.clients["cf"] = Cloudflare(
        api_token="fixture-token",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    context.bindings["cf"] = {"zoneId": "sandbox-zone"}
    return context


def cf_params(**kwargs):
    return cloudflare.CloudflareParams(
        path="/zones/sandbox-zone/dns_records", **kwargs
    ).model_dump()


def test_cloudflare_official_sdk_preserves_envelope_extra_fields_and_null(context):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fixture-token"
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"id": "record", "unknown": {"nullable": None}},
                "extraEnvelope": 12,
            },
        )

    result = cloudflare.request(cf_context(context, handler), "cf", cf_params(), 1)
    assert result.data["result"]["unknown"]["nullable"] is None
    assert "missing" not in result.data["result"]
    assert result.data["extraEnvelope"] == 12
    assert len(requests) == 1


def test_cloudflare_multiple_pages_and_visible_limits(context):
    seen = []

    def handler(request):
        page = int(request.url.params.get("page", 1))
        seen.append(page)
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": [{"id": str(page)}],
                "result_info": {"page": page, "total_pages": 2},
            },
        )

    cf_context(context, handler)
    result = cloudflare.request(context, "cf", cf_params(pagination="all"), 1)
    assert result.meta["complete"] is True and len(result.data["pages"]) == 2
    assert seen == [1, 2]
    with pytest.raises(ValidationError, match="incomplete"):
        cloudflare.request(context, "cf", cf_params(pagination="all", max_pages=1), 1)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.invalid/steal",
        "//evil.invalid",
        "/%2fhost",
        "/zones/../accounts",
        "/zones/%2e%2e/accounts",
        "/zones/x?token=y",
        "/\\evil",
        "/zones/\nsecret",
    ],
)
def test_cloudflare_path_confinement(path):
    with pytest.raises(ValidationError):
        cloudflare.safe_path(path)


@pytest.mark.parametrize(
    "status,code,absent", [(404, 81044, True), (403, 10000, False), (429, 1015, False)]
)
def test_cloudflare_typed_errors_no_body_leak(context, status, code, absent):
    cf_context(
        context,
        lambda r: httpx.Response(
            status,
            headers={"retry-after": "2"},
            json={"success": False, "errors": [{"code": code, "message": "fixture-secret"}]},
        ),
    )
    with pytest.raises(OperationError) as exc:
        cloudflare.request(context, "cf", cf_params(), 1)
    assert exc.value.code == str(code) and exc.value.absent is absent
    assert "fixture-secret" not in str(exc.value)


def test_external_zone_cannot_be_deleted(context):
    cf_context(context, lambda r: pytest.fail("No request may escape the ownership scope"))
    params = cloudflare.CloudflareParams(path="/zones/sandbox-zone", method="DELETE").model_dump()
    with pytest.raises(OwnershipError):
        cloudflare.request(context, "cf", params, 1)


def test_cloudflare_zone_identity(context):
    cf_context(
        context,
        lambda r: httpx.Response(
            200,
            json={"success": True, "result": {"id": "sandbox-zone", "account": {"id": "account"}}},
        ),
    )
    assert cloudflare.identity(context, "cf")["accountId"] == "account"
