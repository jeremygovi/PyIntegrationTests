import io
from datetime import UTC, datetime

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from pyintegrationtests.config import AWSConfig
from pyintegrationtests.errors import OperationError, OwnershipError, ValidationError
from pyintegrationtests.helpers import aws_lifecycle
from pyintegrationtests.providers import aws


def aws_context(context, service):
    context.config.providers["aws"] = AWSConfig(
        kind="aws", region="eu-west-3", account_id="123456789012"
    )
    sdk = boto3.client(
        service,
        region_name="eu-west-3",
        aws_access_key_id="offline",
        aws_secret_access_key="offline",
    )
    context.clients[f"aws:{service}"] = sdk
    context.bindings["aws"] = {"account": "123456789012"}
    return sdk


def test_sdk_pagination_and_full_native_response(context):
    sdk = aws_context(context, "s3")
    with Stubber(sdk) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "IsTruncated": True,
                "NextContinuationToken": "next",
                "Contents": [{"Key": "one", "Size": 3}],
            },
            {"Bucket": "fixture-bucket"},
        )
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [{"Key": "two", "Size": 4}]},
            {"Bucket": "fixture-bucket", "ContinuationToken": "next"},
        )
        result = aws.call(
            context,
            "aws",
            aws.AWSParams(
                service="s3",
                operation="list_objects_v2",
                parameters={"Bucket": "fixture-bucket"},
                pagination="all",
            ).model_dump(),
            1,
        )
        assert len(result.data["pages"]) == 2
        assert result.data["pages"][1]["Contents"][0]["Size"] == 4
        assert result.meta["complete"] is True
        stub.assert_no_pending_responses()


def test_aws_stream_is_explicit_bounded_and_closed(context):
    sdk = aws_context(context, "lambda")
    stream = io.BytesIO(b'{"futureField": true}')
    body = StreamingBody(stream, len(stream.getvalue()))
    with Stubber(sdk) as stub:
        stub.add_response(
            "invoke", {"StatusCode": 200, "Payload": body}, {"FunctionName": "fixture"}
        )
        result = aws.call(
            context,
            "aws",
            aws.AWSParams(
                service="lambda",
                operation="invoke",
                parameters={"FunctionName": "fixture"},
                read_streams=True,
            ).model_dump(),
            1,
        )
        assert "$bytes" in result.data["Payload"] and stream.closed
    sibling = io.BytesIO(b"secret")
    with pytest.raises(ValidationError):
        aws.streams({"a": io.BytesIO(b"12345"), "b": sibling}, 4, True)
    assert sibling.closed
    stream = io.BytesIO(b"123")
    with pytest.raises(ValidationError, match="readStreams"):
        aws.streams(stream, 100, False)
    assert stream.closed


def test_aws_model_rejects_private_operations_and_parameters(context):
    with pytest.raises(ValidationError):
        aws.operation_model("s3", "_make_api_call")
    with pytest.raises(ValidationError):
        aws.call(
            context,
            "aws",
            aws.AWSParams(
                service="s3", operation="head_bucket", parameters={"Unexpected": 3}
            ).model_dump(),
            1,
        )


@pytest.mark.parametrize(
    "code,status,absent",
    [("AccessDenied", 403, False), ("NoSuchBucket", 404, True), ("Throttling", 429, False)],
)
def test_aws_error_contract(context, code, status, absent):
    sdk = aws_context(context, "s3")
    with Stubber(sdk) as stub:
        stub.add_client_error(
            "head_bucket",
            service_error_code=code,
            service_message="secret",
            http_status_code=status,
        )
        with pytest.raises(OperationError) as exc:
            aws.sdk_call(sdk, "head_bucket", {"Bucket": "fixture-bucket"})
        assert exc.value.absent is absent and exc.value.code == code
        assert "secret" not in str(exc.value)


def test_effects_are_explicit_not_inferred(context):
    config = AWSConfig(
        kind="aws",
        region="eu-west-3",
        account_id="123456789012",
        operations={"sqs.receive_message": "mutate"},
    )
    assert aws.effect({"service": "sqs", "operation": "receive_message"}, config) == "mutate"
    with pytest.raises(ValidationError, match="classification"):
        aws.effect({"service": "s3", "operation": "head_bucket"}, config)


def test_identity_checks_account(context):
    sdk = aws_context(context, "sts")
    with Stubber(sdk) as stub:
        stub.add_response(
            "get_caller_identity",
            {
                "Account": "999999999999",
                "Arn": "arn:aws:iam::999999999999:user/fixture",
                "UserId": "fixture",
            },
        )
        with pytest.raises(OwnershipError):
            aws.identity(context, "aws")


def test_s3_version_markers_multipart_and_bucket_cleanup(context):
    sdk = aws_context(context, "s3")
    with Stubber(sdk) as stub:
        stub.add_response(
            "list_object_versions",
            {
                "IsTruncated": True,
                "NextKeyMarker": "a",
                "NextVersionIdMarker": "v1",
                "Versions": [{"Key": "a", "VersionId": "v1"}],
            },
            {"Bucket": "fixture-bucket"},
        )
        stub.add_response(
            "delete_objects",
            {},
            {
                "Bucket": "fixture-bucket",
                "Delete": {"Objects": [{"Key": "a", "VersionId": "v1"}], "Quiet": True},
            },
        )
        stub.add_response(
            "list_object_versions",
            {"IsTruncated": False, "DeleteMarkers": [{"Key": "a", "VersionId": "marker"}]},
            {"Bucket": "fixture-bucket", "KeyMarker": "a", "VersionIdMarker": "v1"},
        )
        stub.add_response(
            "delete_objects",
            {},
            {
                "Bucket": "fixture-bucket",
                "Delete": {"Objects": [{"Key": "a", "VersionId": "marker"}], "Quiet": True},
            },
        )
        stub.add_response("list_objects_v2", {"IsTruncated": False}, {"Bucket": "fixture-bucket"})
        stub.add_response(
            "list_multipart_uploads",
            {"IsTruncated": False, "Uploads": [{"Key": "unfinished", "UploadId": "upload"}]},
            {"Bucket": "fixture-bucket"},
        )
        stub.add_response(
            "abort_multipart_upload",
            {},
            {"Bucket": "fixture-bucket", "Key": "unfinished", "UploadId": "upload"},
        )
        stub.add_response("delete_bucket", {}, {"Bucket": "fixture-bucket"})
        result = aws_lifecycle.s3_empty(
            context,
            "aws",
            aws_lifecycle.S3Params(bucket="fixture-bucket", delete_bucket=True).model_dump(),
            1,
        )
        assert result.data["deletedObjects"] == 2 and result.data["abortedUploads"] == 1
        stub.assert_no_pending_responses()


def test_iam_policies_detached_before_role(context):
    sdk = aws_context(context, "iam")
    with Stubber(sdk) as stub:
        stub.add_response(
            "list_attached_role_policies",
            {
                "AttachedPolicies": [
                    {
                        "PolicyName": "fixture",
                        "PolicyArn": "arn:aws:iam::123456789012:policy/fixture",
                    }
                ]
            },
            {"RoleName": "fixture"},
        )
        stub.add_response(
            "detach_role_policy",
            {},
            {"RoleName": "fixture", "PolicyArn": "arn:aws:iam::123456789012:policy/fixture"},
        )
        stub.add_response(
            "list_role_policies", {"PolicyNames": ["inline"]}, {"RoleName": "fixture"}
        )
        stub.add_response("delete_role_policy", {}, {"RoleName": "fixture", "PolicyName": "inline"})
        stub.add_response(
            "list_instance_profiles_for_role", {"InstanceProfiles": []}, {"RoleName": "fixture"}
        )
        stub.add_response("delete_role", {}, {"RoleName": "fixture"})
        assert aws_lifecycle.iam_delete(
            context, "aws", aws_lifecycle.IAMParams(role_name="fixture").model_dump(), 1
        ).data["deleted"]
        stub.assert_no_pending_responses()


def test_kms_pending_deletion_is_terminal_and_idempotent(context):
    sdk = aws_context(context, "kms")
    key = "12345678-1234-1234-1234-123456789012"
    with Stubber(sdk) as stub:
        stub.add_response(
            "describe_key", {"KeyMetadata": {"KeyId": key, "KeyState": "Enabled"}}, {"KeyId": key}
        )
        stub.add_response(
            "list_aliases",
            {"Aliases": [{"AliasName": "alias/fixture", "TargetKeyId": key}]},
            {"KeyId": key},
        )
        stub.add_response("delete_alias", {}, {"AliasName": "alias/fixture"})
        stub.add_response("disable_key", {}, {"KeyId": key})
        stub.add_response(
            "schedule_key_deletion",
            {
                "KeyId": key,
                "KeyState": "PendingDeletion",
                "DeletionDate": datetime(2026, 9, 14, tzinfo=UTC),
            },
            {"KeyId": key, "PendingWindowInDays": 7},
        )
        params = aws_lifecycle.KMSParams(key_id=key).model_dump()
        assert (
            aws_lifecycle.kms_delete(context, "aws", params, 1).data["KeyState"]
            == "PendingDeletion"
        )
        stub.add_response(
            "describe_key",
            {"KeyMetadata": {"KeyId": key, "KeyState": "PendingDeletion"}},
            {"KeyId": key},
        )
        assert (
            aws_lifecycle.kms_delete(context, "aws", params, 1).data["KeyState"]
            == "PendingDeletion"
        )


def test_kms_unknown_creation_recovery_uses_exact_tags(context):
    sdk = aws_context(context, "kms")
    with Stubber(sdk) as stub:
        stub.add_response("list_keys", {"Keys": [{"KeyId": "owned"}, {"KeyId": "external"}]})
        stub.add_response(
            "list_resource_tags",
            {"Tags": [{"TagKey": "run", "TagValue": context.run_id}]},
            {"KeyId": "owned"},
        )
        stub.add_response(
            "describe_key",
            {"KeyMetadata": {"KeyId": "owned", "KeyState": "Enabled"}},
            {"KeyId": "owned"},
        )
        stub.add_response(
            "list_resource_tags",
            {"Tags": [{"TagKey": "run", "TagValue": "someone-else"}]},
            {"KeyId": "external"},
        )
        result = aws_lifecycle.kms_find(
            context,
            "aws",
            aws_lifecycle.KMSFindParams(tags={"run": context.run_id}).model_dump(),
            1,
        )
        assert len(result.data["matches"]) == 1
        assert result.data["matches"][0]["KeyMetadata"]["KeyId"] == "owned"
        stub.assert_no_pending_responses()


def test_sdk_request_validation_is_offline_even_with_references():
    params = {
        "service": "lambda",
        "operation": "create_function",
        "parameters": {
            "FunctionName": {"$ref": {"source": "inputs", "path": "$.name"}},
            "Role": "role",
            "Runtime": "python3.12",
            "Handler": "handler.main",
            "Code": {"ZipFile": {"$file": "fixture.zip"}},
            "Environment": {"Variables": {"FLAG": "value"}},
            "Tags": {"run": "fixture"},
        },
    }
    aws.validate(params, None)
    params["parameters"]["InventedField"] = True
    with pytest.raises(ValidationError, match="unknown fields"):
        aws.validate(params, None)


def test_aws_dotenv_credentials_use_a_dedicated_native_session(context):
    context.config.providers["aws"] = AWSConfig(
        kind="aws", region="eu-west-3", account_id="123456789012"
    )
    context.environment.update(
        AWS_ACCESS_KEY_ID="dotenv-key", AWS_SECRET_ACCESS_KEY="dotenv-secret"
    )
    aws.client(context, "aws", "s3", 1)
    credentials = context.clients["aws:session"].get_credentials().get_frozen_credentials()
    assert credentials.access_key == "dotenv-key"
    assert context.clients["aws:s3"].meta.config.retries["total_max_attempts"] == 1
    context.close()
