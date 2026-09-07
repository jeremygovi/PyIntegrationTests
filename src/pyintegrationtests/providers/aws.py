"""Generic public boto3 operations, modeled validation, pagination and bounded streams."""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ..config import AWSConfig
from ..context import Context
from ..contracts import Action, ActionResult, Effect
from ..errors import OperationError, OwnershipError, ValidationError
from ..helpers.artifacts import binary
from ..schema import Model

if TYPE_CHECKING:
    from ..registry import Registry


class AWSParams(Model):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    parameters: dict[str, Any] = Field(default_factory=dict)
    pagination: Literal["page", "all"] = "page"
    max_pages: int = Field(default=100, ge=1, le=10000)
    read_streams: bool = False


def effect(params: dict[str, Any], config: Any) -> Effect:
    if not isinstance(config, AWSConfig):
        raise ValidationError("AWS provider required")
    key = f"{params.get('service')}.{params.get('operation')}"
    classified = config.operations.get(key)
    if classified is None:
        raise ValidationError(f"AWS operation needs an explicit read/mutate classification: {key}")
    if classified == "mutate" and params.get("pagination") == "all":
        raise ValidationError("Mutations cannot be paginated or retried by the engine")
    return classified


@lru_cache(maxsize=256)
def operation_model(service: str, operation: str) -> Any:
    from botocore import xform_name
    from botocore.session import Session

    try:
        model: Any = Session().get_service_model(service)
        mapping = {xform_name(name): name for name in model.operation_names}
        if operation not in mapping:
            raise ValidationError("Unknown public AWS operation")
        return model.operation_model(mapping[operation])
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Unknown AWS service model") from None


def validate(params: dict[str, Any], config: Any) -> None:
    if importlib.util.find_spec("botocore") is None:
        return
    if isinstance(params.get("service"), str) and isinstance(params.get("operation"), str):
        model = operation_model(params["service"], params["operation"])
        parameters = params.get("parameters", {})
        if model.input_shape is None:
            if parameters:
                raise ValidationError("This AWS operation does not accept parameters")
        else:
            validate_shape(parameters, model.input_shape)


def validate_shape(value: Any, shape: Any) -> None:
    from botocore.validate import validate_parameters

    if isinstance(value, dict) and any(k in value for k in ("$ref", "$format", "$file", "$bytes")):
        return
    if shape.type_name == "structure" and isinstance(value, dict):
        if set(value) - set(shape.members) or set(shape.required_members) - set(value):
            raise ValidationError("AWS parameters contain unknown fields or omit required fields")
        for key, child in value.items():
            validate_shape(child, shape.members[key])
    elif shape.type_name == "list" and isinstance(value, list):
        for child in value:
            validate_shape(child, shape.member)
    elif shape.type_name == "map" and isinstance(value, dict):
        for key, child in value.items():
            validate_shape(key, shape.key)
            validate_shape(child, shape.value)
    else:
        try:
            validate_parameters(value, shape)
        except Exception:
            raise ValidationError("AWS parameters do not match the SDK request model") from None


def client(context: Context, provider: str, service: str, timeout: float) -> Any:
    import boto3
    from botocore.config import Config as SDKConfig
    from botocore.credentials import EnvProvider
    from botocore.session import Session

    settings = context.config.providers[provider]
    assert isinstance(settings, AWSConfig)
    key = f"{provider}:{service}"
    if key in context.clients:
        return context.clients[key]
    session_key = f"{provider}:session"
    session = context.clients.get(session_key)
    if session is None:
        # A dedicated session preserves native refreshable profile/role credentials.
        core = Session()
        profile = settings.profile or context.environment.get("AWS_PROFILE")
        for variable, config_key in (
            ("AWS_SHARED_CREDENTIALS_FILE", "credentials_file"),
            ("AWS_CONFIG_FILE", "config_file"),
        ):
            if variable in context.environment:
                core.set_config_variable(config_key, context.environment[variable])
        if not profile:
            core.get_component("credential_provider").insert_before(
                "env", EnvProvider(environ=context.environment)
            )
        session = boto3.Session(
            botocore_session=core,
            profile_name=profile,
            region_name=settings.region,
        )
        context.clients[session_key] = session
    result = session.client(
        service,
        region_name=settings.region,
        endpoint_url=settings.endpoint_url,
        verify=settings.verify,
        config=SDKConfig(
            connect_timeout=min(timeout, 5), read_timeout=timeout, retries={"total_max_attempts": 1}
        ),
    )
    context.clients[key] = result
    return result


def sdk_call(sdk: Any, operation: str, parameters: dict[str, Any]) -> Any:
    from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError

    try:
        return getattr(sdk, operation)(**parameters)
    except ClientError as exc:
        error = exc.response.get("Error", {})
        code = str(error.get("Code", "Unknown"))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        raise OperationError(
            "aws",
            code,
            status,
            retryable=code
            in {
                "Throttling",
                "ThrottlingException",
                "TooManyRequestsException",
                "ServiceUnavailable",
            },
            absent=code
            in {
                "ResourceNotFoundException",
                "NoSuchEntity",
                "NoSuchBucket",
                "NoSuchKey",
                "NotFoundException",
                "NotFound",
                "404",
            },
        ) from None
    except ParamValidationError:
        raise ValidationError("AWS parameters do not match the SDK request model") from None
    except BotoCoreError as exc:
        raise OperationError("aws", type(exc).__name__, retryable=False) from None


def identity(context: Context, provider: str) -> dict[str, Any]:
    config = context.config.providers[provider]
    assert isinstance(config, AWSConfig)
    response = sdk_call(
        client(context, provider, "sts", min(context.remaining(), 15)), "get_caller_identity", {}
    )
    if response["Account"] != config.account_id:
        raise OwnershipError("AWS caller account differs from the explicitly configured account")
    return {
        "kind": "aws",
        "account": response["Account"],
        "region": config.region,
        "endpoint": config.endpoint_url,
    }


def streams(value: Any, limit: int, consume: bool) -> Any:
    if hasattr(value, "read") and hasattr(value, "close"):
        try:
            if not consume:
                raise ValidationError("Response contains a stream; opt into readStreams")
            data = value.read(limit + 1)
            if len(data) > limit:
                raise ValidationError("AWS stream exceeds configured artifact byte limit")
            return data
        finally:
            value.close()
    if isinstance(value, dict):
        result = {}
        try:
            for key, child in value.items():
                result[key] = streams(child, limit, consume)
        except Exception:
            # Also close unvisited sibling streams on failure.
            close_streams(value)
            raise
        return result
    if isinstance(value, list):
        try:
            return [streams(v, limit, consume) for v in value]
        except Exception:
            close_streams(value)
            raise
    return value


def close_streams(value: Any) -> None:
    if hasattr(value, "close") and hasattr(value, "read"):
        value.close()
    elif isinstance(value, dict):
        for v in value.values():
            close_streams(v)
    elif isinstance(value, list):
        for v in value:
            close_streams(v)


def call(
    context: Context, provider: str | None, params: dict[str, Any], timeout: float
) -> ActionResult:
    from botocore.exceptions import ClientError
    from botocore.validate import validate_parameters

    assert provider is not None
    model = operation_model(params["service"], params["operation"])
    parameters = binary(params["parameters"], context)
    try:
        if model.input_shape is not None:
            validate_parameters(parameters, model.input_shape)
        elif parameters:
            raise ValueError
    except Exception:
        raise ValidationError("AWS parameters do not match the SDK request model") from None
    sdk = client(context, provider, params["service"], timeout)
    if provider not in context.bindings:
        context.bindings[provider] = identity(context, provider)
    if params["pagination"] == "page":
        data = sdk_call(sdk, params["operation"], parameters)
        data = streams(data, context.config.artifacts.max_bytes, params["read_streams"])
        return ActionResult(
            data,
            {"provider": provider, "operation": params["operation"], "pagination": "page"},
            sensitive=True,
        )
    if not sdk.can_paginate(params["operation"]):
        raise ValidationError(
            "AWS operation has no SDK paginator; use page mode and explicit tokens"
        )
    pages: list[Any] = []
    try:
        for page in sdk.get_paginator(params["operation"]).paginate(**parameters):
            context.remaining()
            if len(pages) >= params["max_pages"]:
                close_streams(page)
                raise ValidationError("AWS pagination exceeded maxPages; result is incomplete")
            pages.append(streams(page, context.config.artifacts.max_bytes, params["read_streams"]))
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "Unknown"))
        raise OperationError(
            "aws", code, exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        ) from None
    return ActionResult(
        {"pages": pages},
        {"provider": provider, "pagination": "all", "pages": len(pages), "complete": True},
        sensitive=True,
    )


def register(registry: Registry) -> None:
    from ..helpers.aws_lifecycle import register as register_lifecycle

    registry.register(
        Action("aws.call", AWSParams, call, effect, "aws", sensitive=True, validate=validate)
    )
    register_lifecycle(registry)
