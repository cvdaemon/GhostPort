from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Protocol

from ghostport.config.paths import AppPaths
from ghostport.constants import KEYRING_SERVICE, SECRET_REF_PREFIX
from ghostport.security import dpapi

log = logging.getLogger("ghostport.security")

_REF_RE = re.compile(r"^gp-[0-9a-fA-F-]{36}$")


class CredentialBackend(Protocol):
    def get(self, secret_ref: str) -> str | None: ...
    def set(self, secret_ref: str, secret: str) -> None: ...
    def delete(self, secret_ref: str) -> None: ...


def _validate_ref(secret_ref: str) -> str:
    if not _REF_RE.match(secret_ref):
        raise ValueError(f"Invalid secret_ref: {secret_ref!r}")
    return secret_ref


class DpapiFileBackend:
    """config/secrets/<secret_ref>.dpapi — portable default."""

    def __init__(self, secrets_dir: Path) -> None:
        self._dir = secrets_dir

    def _path(self, secret_ref: str) -> Path:
        return self._dir / f"{_validate_ref(secret_ref)}.dpapi"

    def get(self, secret_ref: str) -> str | None:
        path = self._path(secret_ref)
        if not path.is_file():
            return None
        blob = path.read_bytes()
        return dpapi.unprotect(blob).decode("utf-8")

    def set(self, secret_ref: str, secret: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(secret_ref)
        path.write_bytes(dpapi.protect(secret.encode("utf-8")))

    def delete(self, secret_ref: str) -> None:
        path = self._path(secret_ref)
        try:
            path.unlink()
        except FileNotFoundError:
            return


class KeyringBackend:
    """Windows Credential Locker via keyring. service=Ghostport, username=secret_ref."""

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self._service = service

    def get(self, secret_ref: str) -> str | None:
        import keyring

        _validate_ref(secret_ref)
        return keyring.get_password(self._service, secret_ref)

    def set(self, secret_ref: str, secret: str) -> None:
        import keyring

        _validate_ref(secret_ref)
        keyring.set_password(self._service, secret_ref, secret)

    def delete(self, secret_ref: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        _validate_ref(secret_ref)
        try:
            keyring.delete_password(self._service, secret_ref)
        except PasswordDeleteError:
            return


class MemoryBackend:
    """Tests only. Never used in production."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, secret_ref: str) -> str | None:
        return self._store.get(_validate_ref(secret_ref))

    def set(self, secret_ref: str, secret: str) -> None:
        self._store[_validate_ref(secret_ref)] = secret

    def delete(self, secret_ref: str) -> None:
        self._store.pop(_validate_ref(secret_ref), None)


class CredentialStore:
    def __init__(self, backend: CredentialBackend) -> None:
        self._backend = backend

    @staticmethod
    def new_ref() -> str:
        return SECRET_REF_PREFIX + str(uuid.uuid4())

    @staticmethod
    def for_paths(paths: AppPaths) -> CredentialStore:
        if paths.is_portable:
            return CredentialStore(DpapiFileBackend(paths.secrets_dir))
        return CredentialStore(KeyringBackend())

    def put_secret(self, secret: str, *, secret_ref: str | None = None) -> str:
        """Overwrite `secret_ref` if given; else mint `gp-<uuid4>` and write."""
        ref = secret_ref or self.new_ref()
        self._backend.set(ref, secret)
        return ref

    def put_password(self, secret: str, *, secret_ref: str | None = None) -> str:
        return self.put_secret(secret, secret_ref=secret_ref)

    def put_passphrase(self, secret: str, *, secret_ref: str | None = None) -> str:
        return self.put_secret(secret, secret_ref=secret_ref)

    def get_secret(self, secret_ref: str) -> str | None:
        return self._backend.get(secret_ref)

    def delete(self, secret_ref: str) -> None:
        self._backend.delete(secret_ref)
