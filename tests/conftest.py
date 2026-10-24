from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ghostport.config.paths import AppPaths
from ghostport.profiles.manager import ProfileManager
from ghostport.security.credentials import CredentialStore, MemoryBackend


@pytest.fixture
def portable_root(tmp_path: Path) -> Path:
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")
    return tmp_path


@pytest.fixture
def portable_paths(portable_root: Path) -> AppPaths:
    paths = AppPaths.resolve(portable_root)
    paths.ensure_dirs()
    return paths


@pytest.fixture
def memory_store() -> CredentialStore:
    return CredentialStore(MemoryBackend())


@pytest.fixture
def profiles(portable_paths: AppPaths) -> ProfileManager:
    manager = ProfileManager(portable_paths)
    manager.load()
    return manager
