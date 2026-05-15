"""Unit tests for replay authentication wiring."""

from __future__ import annotations

import sys
import types

import pytest

from evaluations import replay

_fake_credential_instances: list[_FakeDefaultAzureCredential] = []


class _FakeDefaultAzureCredential:
    """Minimal async credential double for replay auth tests."""

    def __init__(self, **kwargs: str) -> None:
        self.kwargs = kwargs
        self.requested_scope = ""
        self.issued_token = f"generated-{len(_fake_credential_instances) + 1}"
        _fake_credential_instances.append(self)

    async def __aenter__(self) -> _FakeDefaultAzureCredential:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        return None

    async def get_token(self, scope: str) -> types.SimpleNamespace:
        self.requested_scope = scope
        return types.SimpleNamespace(token=self.issued_token)


def _install_fake_identity_modules() -> None:
    default_credential_attr = "DefaultAzureCredential"
    aio_attr = "aio"
    identity_attr = "identity"

    azure_module = types.ModuleType("azure")
    identity_module = types.ModuleType("azure.identity")
    aio_module = types.ModuleType("azure.identity.aio")
    setattr(aio_module, default_credential_attr, _FakeDefaultAzureCredential)
    setattr(identity_module, aio_attr, aio_module)
    setattr(azure_module, identity_attr, identity_module)
    sys.modules["azure"] = azure_module
    sys.modules["azure.identity"] = identity_module
    sys.modules["azure.identity.aio"] = aio_module


def _clear_fake_identity_modules() -> None:
    for module_name in ("azure.identity.aio", "azure.identity", "azure"):
        sys.modules.pop(module_name, None)


def test_build_default_credential_uses_azure_client_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_builder_attr = "_build_default_credential"

    _fake_credential_instances.clear()
    _install_fake_identity_modules()
    monkeypatch.setenv("AZURE_CLIENT_ID", "runner-client-id")

    try:
        credential_builder = getattr(replay, credential_builder_attr)
        credential_builder()
    finally:
        _clear_fake_identity_modules()

    assert len(_fake_credential_instances) == 1
    assert _fake_credential_instances[0].kwargs == {
        "managed_identity_client_id": "runner-client-id"
    }


async def test_acquire_bearer_token_uses_requested_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    acquire_bearer_fn_attr = "_acquire_bearer_token"

    _fake_credential_instances.clear()
    _install_fake_identity_modules()
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    try:
        acquire_bearer_token = getattr(replay, acquire_bearer_fn_attr)
        token = await acquire_bearer_token("api-client-id")
    finally:
        _clear_fake_identity_modules()

    assert token == _fake_credential_instances[0].issued_token
    assert len(_fake_credential_instances) == 1
    assert _fake_credential_instances[0].kwargs == {}
    assert _fake_credential_instances[0].requested_scope == ("api://api-client-id/.default")
