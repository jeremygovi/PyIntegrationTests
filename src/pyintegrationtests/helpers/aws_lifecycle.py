"""Bounded lifecycle helpers for resources already guarded by the ownership journal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from ..context import Context
from ..contracts import Action, ActionResult
from ..errors import OperationError, ValidationError
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class S3Params(Model):
    bucket: str
    delete_bucket: bool = False
    max_pages: int = Field(default=1000, ge=1, le=10000)


class IAMParams(Model):
    role_name: str
    max_pages: int = Field(default=100, ge=1, le=10000)


class KMSParams(Model):
    key_id: str
    pending_window_in_days: int = Field(default=7, ge=7, le=30)
    max_pages: int = Field(default=100, ge=1, le=10000)


class KMSFindParams(Model):
    tags: dict[str, str] = Field(min_length=1)
    max_pages: int = Field(default=100, ge=1, le=10000)


def pages(context: Context, sdk: Any, operation: str, params: dict[str, Any], maximum: int) -> Any:
    for index, page in enumerate(sdk.get_paginator(operation).paginate(**params)):
        context.remaining()
        if index >= maximum:
            raise ValidationError("Lifecycle pagination exceeded maxPages")
        yield page


def s3_empty(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from ..providers.aws import client, sdk_call

    assert provider is not None
    sdk = client(context, provider, "s3", timeout)
    bucket = params["bucket"]
    deleted = 0
    try:
        for operation, collection in (
            ("list_object_versions", ("Versions", "DeleteMarkers")),
            ("list_objects_v2", ("Contents",)),
        ):
            for page in pages(context, sdk, operation, {"Bucket": bucket}, params["max_pages"]):
                objects = [
                    {k: obj[k] for k in ("Key", "VersionId") if k in obj}
                    for field in collection
                    for obj in page.get(field, [])
                ]
                for start in range(0, len(objects), 1000):
                    context.remaining()
                    batch = objects[start : start + 1000]
                    response = sdk_call(
                        sdk,
                        "delete_objects",
                        {"Bucket": bucket, "Delete": {"Objects": batch, "Quiet": True}},
                    )
                    if response.get("Errors"):
                        raise OperationError("aws", "PartialDeleteFailure")
                    deleted += len(batch)
        uploads = 0
        for page in pages(
            context, sdk, "list_multipart_uploads", {"Bucket": bucket}, params["max_pages"]
        ):
            for upload in page.get("Uploads", []):
                context.remaining()
                sdk_call(
                    sdk,
                    "abort_multipart_upload",
                    {"Bucket": bucket, "Key": upload["Key"], "UploadId": upload["UploadId"]},
                )
                uploads += 1
        if params["delete_bucket"]:
            sdk_call(sdk, "delete_bucket", {"Bucket": bucket})
        else:
            for operation, keys in (
                ("list_object_versions", ("Versions", "DeleteMarkers")),
                ("list_objects_v2", ("Contents",)),
                ("list_multipart_uploads", ("Uploads",)),
            ):
                response = sdk_call(sdk, operation, {"Bucket": bucket})
                if any(response.get(key) for key in keys):
                    raise OperationError("aws", "BucketStillContainsData")
        return ActionResult({"deletedObjects": deleted, "abortedUploads": uploads})
    except OperationError as exc:
        if exc.absent:
            return ActionResult({"absent": True})
        raise


def iam_delete(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from ..providers.aws import client, sdk_call

    assert provider is not None
    sdk = client(context, provider, "iam", timeout)
    role = params["role_name"]
    try:
        for page in pages(
            context, sdk, "list_attached_role_policies", {"RoleName": role}, params["max_pages"]
        ):
            for policy in page.get("AttachedPolicies", []):
                sdk_call(
                    sdk, "detach_role_policy", {"RoleName": role, "PolicyArn": policy["PolicyArn"]}
                )
        for page in pages(
            context, sdk, "list_role_policies", {"RoleName": role}, params["max_pages"]
        ):
            for name in page.get("PolicyNames", []):
                sdk_call(sdk, "delete_role_policy", {"RoleName": role, "PolicyName": name})
        for page in pages(
            context, sdk, "list_instance_profiles_for_role", {"RoleName": role}, params["max_pages"]
        ):
            for profile in page.get("InstanceProfiles", []):
                # Profiles are external unless separately owned; only detach the owned role.
                sdk_call(
                    sdk,
                    "remove_role_from_instance_profile",
                    {"RoleName": role, "InstanceProfileName": profile["InstanceProfileName"]},
                )
        sdk_call(sdk, "delete_role", {"RoleName": role})
        return ActionResult({"deleted": True})
    except OperationError as exc:
        if exc.absent:
            return ActionResult({"absent": True})
        raise


def kms_delete(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from ..providers.aws import client, sdk_call

    assert provider is not None
    sdk = client(context, provider, "kms", timeout)
    key = params["key_id"]
    state = sdk_call(sdk, "describe_key", {"KeyId": key})["KeyMetadata"]
    if state["KeyState"] == "PendingDeletion":
        return ActionResult(state)
    for page in pages(context, sdk, "list_aliases", {"KeyId": key}, params["max_pages"]):
        for alias in page.get("Aliases", []):
            if alias.get("TargetKeyId") == state["KeyId"] and not alias["AliasName"].startswith(
                "alias/aws/"
            ):
                sdk_call(sdk, "delete_alias", {"AliasName": alias["AliasName"]})
    sdk_call(sdk, "disable_key", {"KeyId": key})
    response = sdk_call(
        sdk,
        "schedule_key_deletion",
        {"KeyId": key, "PendingWindowInDays": params["pending_window_in_days"]},
    )
    return ActionResult(response, {"terminalCleanupState": "PendingDeletion"})


def kms_find(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    """Recover an unknown create_key outcome by exact tag equality, never by prefix."""
    from ..providers.aws import client, sdk_call

    assert provider is not None
    sdk = client(context, provider, "kms", timeout)
    matches = []
    for page in pages(context, sdk, "list_keys", {}, params["max_pages"]):
        for key in page.get("Keys", []):
            tags = []
            for tag_page in pages(
                context, sdk, "list_resource_tags", {"KeyId": key["KeyId"]}, params["max_pages"]
            ):
                tags.extend(tag_page.get("Tags", []))
            found = {tag["TagKey"]: tag["TagValue"] for tag in tags}
            if all(found.get(k) == v for k, v in params["tags"].items()):
                metadata = sdk_call(sdk, "describe_key", {"KeyId": key["KeyId"]})
                matches.append({**metadata, "Tags": tags})
    return ActionResult({"matches": matches}, {"complete": True})


def register(registry: Registry) -> None:
    registry.register(Action("aws.kms.find", KMSFindParams, kms_find, "read", "aws"))
    for name, model, handler in (
        ("aws.s3.empty", S3Params, s3_empty),
        ("aws.iam.delete-role", IAMParams, iam_delete),
        ("aws.kms.schedule-deletion", KMSParams, kms_delete),
    ):
        registry.register(Action(name, model, handler, "mutate", "aws"))
