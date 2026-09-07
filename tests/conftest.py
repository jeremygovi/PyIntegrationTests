"""The ordinary test suite is offline and never uses ambient credentials."""

import socket

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "offline")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "offline-secret")


@pytest.fixture
def context(tmp_path):
    from pyintegrationtests.config import Config
    from pyintegrationtests.context import Context
    from pyintegrationtests.registry import default_registry

    config = Config.model_validate({"artifacts": {"directory": str(tmp_path / "runs")}})
    return Context(config, {}, default_registry(config), "suite", "case", tmp_path, {})
