from __future__ import annotations

import base64
import hashlib
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from paramiko.hostkeys import HostKeys
from paramiko.pkey import PKey


class HostKeyDecision(str, Enum):
    ABORT = "abort"
    TRUST = "trust"
    REPLACE = "replace"


class HostKeyKind(str, Enum):
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class HostKeyPrompt:
    prompt_id: str
    kind: HostKeyKind
    host: str
    port: int
    hostname_key: str
    key_type: str
    fingerprint_sha256: str
    stored_key_type: str | None
    stored_fingerprint_sha256: str | None


def fingerprint_sha256(key: PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class KnownHostsStore:
    """Process-wide lock around load/lookup/remember/replace/save."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._keys = HostKeys()
        if path.is_file():
            self._keys.load(str(path))

    def hostname_key(self, host: str, port: int) -> str:
        return f"[{host}]:{port}"

    def lookup(self, host: str, port: int, key: PKey) -> Literal["match", "unknown", "mismatch"]:
        name = self.hostname_key(host, port)
        with self._lock:
            entries = self._keys.lookup(name)
            if not entries:
                return "unknown"
            stored = entries.get(key.get_name())
            if stored is not None and stored.asbytes() == key.asbytes():
                return "match"
            return "mismatch"

    def stored_identity(self, host: str, port: int) -> tuple[str | None, str | None]:
        name = self.hostname_key(host, port)
        with self._lock:
            entries = self._keys.lookup(name)
            if not entries:
                return None, None
            first_type = next(iter(entries))
            return first_type, fingerprint_sha256(entries[first_type])

    def remember(self, host: str, port: int, key: PKey) -> None:
        name = self.hostname_key(host, port)
        with self._lock:
            self._keys.add(name, key.get_name(), key)
            self._save_locked()

    def replace(self, host: str, port: int, key: PKey) -> None:
        name = self.hostname_key(host, port)
        with self._lock:
            if name in self._keys:
                del self._keys[name]
            self._keys.add(name, key.get_name(), key)
            self._save_locked()

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._keys.save(str(self._path))


class HostKeyGate:
    """Worker-side waiter. No timeout. Dialog reject = ABORT."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, HostKeyDecision] = {}

    def begin(self, prompt_id: str) -> None:
        event = threading.Event()
        with self._lock:
            self._events[prompt_id] = event

    def wait(self, prompt_id: str) -> HostKeyDecision:
        with self._lock:
            event = self._events.get(prompt_id)
        if event is None:
            self.begin(prompt_id)
            with self._lock:
                event = self._events[prompt_id]
        event.wait()
        with self._lock:
            return self._results.pop(prompt_id, HostKeyDecision.ABORT)

    def resolve(self, prompt_id: str, decision: HostKeyDecision) -> None:
        with self._lock:
            self._results[prompt_id] = decision
            event = self._events.pop(prompt_id, None)
        if event is not None:
            event.set()

    @staticmethod
    def new_prompt_id() -> str:
        return str(uuid.uuid4())
