from __future__ import annotations

import json
from pathlib import Path

from ghostport.config.paths import AppPaths
from ghostport.errors import ProfileNotFound
from ghostport.profiles.manager import ProfileManager
from ghostport.profiles.models import AuthMethod, ServerProfile, coerce_auth_method
from ghostport.security.credentials import CredentialStore, MemoryBackend


def _profile(**kwargs: object) -> ServerProfile:
    base = dict(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="QA_SERVER",
        host="15.206.101.4",
        port=22,
        username="simtra",
        auth_method=AuthMethod.PASSWORD,
        secret_ref="gp-11111111-1111-1111-1111-111111111111",
        favorite=True,
        sort_index=0,
    )
    base.update(kwargs)
    return ServerProfile(**base)  # type: ignore[arg-type]


def test_upsert_and_get(profiles: ProfileManager) -> None:
    saved = profiles.upsert(_profile())
    loaded = profiles.get(saved.id)
    assert loaded.name == "QA_SERVER"
    assert loaded.host == "15.206.101.4"


def test_rename_does_not_change_id(profiles: ProfileManager) -> None:
    profile = profiles.upsert(_profile())
    profile.name = "PROD_API"
    profiles.upsert(profile)
    again = profiles.get(profile.id)
    assert again.id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert again.name == "PROD_API"


def test_delete_does_not_call_vault(profiles: ProfileManager) -> None:
    store = CredentialStore(MemoryBackend())
    ref = store.put_secret("secret-password")
    profile = profiles.upsert(_profile(secret_ref=ref))
    profiles.delete(profile.id)
    assert store.get_secret(ref) == "secret-password"


def test_reorder_and_search(profiles: ProfileManager) -> None:
    a = profiles.upsert(_profile(id="id-a", name="PROD_API", host="10.0.0.1", sort_index=0))
    b = profiles.upsert(_profile(id="id-b", name="QA_SERVER", host="10.0.0.2", sort_index=1))
    c = profiles.upsert(_profile(id="id-c", name="DEV_SERVER", host="10.0.0.3", sort_index=2, favorite=False))
    profiles.reorder([b.id, a.id, c.id])
    names = [p.name for p in profiles.favorites()]
    assert names == ["QA_SERVER", "PROD_API"]
    hits = profiles.search("prod")
    assert [p.name for p in hits] == ["PROD_API"]
    assert profiles.search("10.0.0.3")[0].name == "DEV_SERVER"


def test_atomic_write_creates_bak(profiles: ProfileManager, portable_paths: AppPaths) -> None:
    profiles.upsert(_profile())
    profiles.upsert(_profile(id="id-2", name="NVR", host="10.0.0.9"))
    assert portable_paths.profiles_file.is_file()
    bak = portable_paths.profiles_file.with_suffix(".json.bak")
    assert bak.is_file()
    data = json.loads(portable_paths.profiles_file.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["profiles"]) == 2


def test_corrupt_file_recovery(portable_paths: AppPaths) -> None:
    portable_paths.profiles_file.write_text("{not-json", encoding="utf-8")
    manager = ProfileManager(portable_paths)
    manager.load()
    assert manager.all() == []
    assert manager.load_warning is not None
    leftovers = list(portable_paths.config_dir.glob("profiles.json.corrupt-*"))
    assert leftovers


def test_delete_missing_raises(profiles: ProfileManager) -> None:
    try:
        profiles.delete("missing")
        raise AssertionError("expected ProfileNotFound")
    except ProfileNotFound:
        pass


def test_coerce_auth_method_from_qt_string() -> None:
    assert coerce_auth_method("password") is AuthMethod.PASSWORD
    assert coerce_auth_method("private_key") is AuthMethod.PRIVATE_KEY
    assert coerce_auth_method("agent") is AuthMethod.AGENT
    assert coerce_auth_method(AuthMethod.PASSWORD) is AuthMethod.PASSWORD
    assert coerce_auth_method("nope") is AuthMethod.PASSWORD
    assert coerce_auth_method(None) is AuthMethod.PASSWORD
