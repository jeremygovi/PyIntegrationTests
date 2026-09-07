"""Generic controller conditions, with terminal conditions taking precedence."""

from __future__ import annotations

from typing import Any

from ..errors import AssertionFailure, IntegrationError


def conditions(resource: dict[str, Any], *, expected_generation: int | None = None) -> None:
    status = resource.get("status", {})
    entries = status.get("conditions", [])
    if any(
        c.get("type") == "ACK.Terminal" and str(c.get("status")).lower() == "true" for c in entries
    ):
        raise IntegrationError("ACK terminal condition is true")
    if (
        expected_generation is not None
        and status.get("observedGeneration", -1) < expected_generation
    ):
        raise AssertionFailure("Controller has not observed the required generation")
    if not any(
        c.get("type") == "ACK.ResourceSynced" and str(c.get("status")).lower() == "true"
        for c in entries
    ):
        raise AssertionFailure("ACK resource is not synchronized")


def deployment(resource: dict[str, Any]) -> None:
    generation = resource.get("metadata", {}).get("generation")
    status = resource.get("status", {})
    desired = resource.get("spec", {}).get("replicas", 1)
    if generation is None or status.get("observedGeneration", -1) < generation:
        raise AssertionFailure("Deployment status is stale")
    if any(
        status.get(field, 0) != desired
        for field in ("updatedReplicas", "availableReplicas", "readyReplicas")
    ):
        raise AssertionFailure("Deployment replicas are not ready")


def job(resource: dict[str, Any]) -> None:
    entries = resource.get("status", {}).get("conditions", [])
    if any(c.get("type") == "Failed" and c.get("status") == "True" for c in entries):
        raise IntegrationError("Job failed")
    if not any(c.get("type") == "Complete" and c.get("status") == "True" for c in entries):
        raise AssertionFailure("Job is incomplete")
