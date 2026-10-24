from __future__ import annotations

import threading
from pathlib import Path

from paramiko.rsakey import RSAKey

from ghostport.ssh.known_hosts import KnownHostsStore, fingerprint_sha256


def _key() -> RSAKey:
    return RSAKey.generate(1024)


def test_hostname_key_includes_port_22() -> None:
    store = KnownHostsStore(Path("unused"))
    assert store.hostname_key("15.206.101.4", 22) == "[15.206.101.4]:22"
    assert store.hostname_key("example.com", 2222) == "[example.com]:2222"


def test_unknown_match_mismatch_remember_replace(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    store = KnownHostsStore(path)
    key_a = _key()
    key_b = _key()
    host, port = "10.0.0.1", 22
    assert store.lookup(host, port, key_a) == "unknown"
    store.remember(host, port, key_a)
    assert store.lookup(host, port, key_a) == "match"
    assert store.lookup(host, port, key_b) == "mismatch"
    store.replace(host, port, key_b)
    assert store.lookup(host, port, key_b) == "match"
    assert store.lookup(host, port, key_a) == "mismatch"


def test_fingerprint_sha256_openssh_format() -> None:
    key = _key()
    fp = fingerprint_sha256(key)
    assert fp.startswith("SHA256:")
    assert "=" not in fp[7:]
    rest = fp[len("SHA256:") :]
    assert rest.isascii()
    assert len(rest) >= 40


def test_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    store = KnownHostsStore(path)
    key = _key()
    store.remember("qa.example", 22, key)
    text = path.read_text(encoding="utf-8")
    assert "[qa.example]:22" in text
    again = KnownHostsStore(path)
    assert again.lookup("qa.example", 22, key) == "match"


def test_lock_serializes_writers(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    store = KnownHostsStore(path)
    keys = [_key() for _ in range(8)]

    def write(i: int) -> None:
        store.remember(f"h{i}.example", 22, keys[i])

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    again = KnownHostsStore(path)
    for i, key in enumerate(keys):
        assert again.lookup(f"h{i}.example", 22, key) == "match"
