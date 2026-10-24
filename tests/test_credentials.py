from __future__ import annotations

from pathlib import Path

import pytest

from ghostport.config.paths import AppPaths
from ghostport.security.credentials import CredentialStore, KeyringBackend, MemoryBackend


def test_memory_round_trip(memory_store: CredentialStore) -> None:
    ref = memory_store.put_secret("hunter2")
    assert ref.startswith("gp-")
    assert memory_store.get_secret(ref) == "hunter2"


def test_overwrite_existing_ref(memory_store: CredentialStore) -> None:
    ref = memory_store.put_secret("first")
    again = memory_store.put_secret("second", secret_ref=ref)
    assert again == ref
    assert memory_store.get_secret(ref) == "second"


def test_mint_when_ref_is_none(memory_store: CredentialStore) -> None:
    a = memory_store.put_secret("a", secret_ref=None)
    b = memory_store.put_secret("b", secret_ref=None)
    assert a != b
    assert memory_store.get_secret(a) == "a"
    assert memory_store.get_secret(b) == "b"


def test_delete_and_missing(memory_store: CredentialStore) -> None:
    ref = memory_store.put_secret("gone")
    memory_store.delete(ref)
    assert memory_store.get_secret(ref) is None
    memory_store.delete(ref)


def test_put_password_and_passphrase_are_aliases(memory_store: CredentialStore) -> None:
    ref = memory_store.put_password("pw")
    assert memory_store.get_secret(ref) == "pw"
    ref2 = memory_store.put_passphrase("ph", secret_ref=ref)
    assert ref2 == ref
    assert memory_store.get_secret(ref) == "ph"


def test_invalid_ref_rejected(memory_store: CredentialStore) -> None:
    with pytest.raises(ValueError):
        memory_store.put_secret("x", secret_ref="../etc")
    with pytest.raises(ValueError):
        memory_store.get_secret("not-a-ref")


def test_factory_portable_uses_dpapi_backend(tmp_path: Path) -> None:
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")
    paths = AppPaths.resolve(tmp_path)
    paths.ensure_dirs()
    store = CredentialStore.for_paths(paths)
    assert type(store._backend).__name__ == "DpapiFileBackend"


def test_factory_appdata_uses_keyring_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    install = tmp_path / "install"
    install.mkdir()
    paths = AppPaths.resolve(install)
    store = CredentialStore.for_paths(paths)
    assert isinstance(store._backend, KeyringBackend)


def test_new_ref_prefix() -> None:
    ref = CredentialStore.new_ref()
    assert ref.startswith("gp-")
    assert len(ref) > 10
