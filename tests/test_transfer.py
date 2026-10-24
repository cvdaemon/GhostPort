from __future__ import annotations

import os

import pytest

from ghostport.sftp.file_model import FileEntry, entries_from_mime, entries_to_mime
from ghostport.sftp.transfer_manager import (
    TransferDirection,
    TransferJob,
    TransferStatus,
    average_speed,
    format_bytes,
    format_eta,
    format_speed,
    note_speed_sample,
)


def test_format_bytes_and_eta() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536).endswith("KB")
    assert format_bytes(3 * 1024 * 1024).endswith("MB")
    assert format_eta(None) == "—"
    assert format_eta(9) == "9s"
    assert format_eta(75) == "1m 15s"
    assert format_speed(0) == "—"
    assert format_speed(2048).endswith("/s")


def test_speed_is_average_of_last_100_samples() -> None:
    samples: list[tuple[float, int]] = [(0.0, 0), (0.1, 5_000_000)]
    samples.extend((1.0 + i * 0.1, 5_000_000 + i * 100) for i in range(1, 130))
    speed = None
    window: list[tuple[float, int]] = []
    for when, done in samples:
        speed = note_speed_sample(window, when, done)
    assert len(window) == 100
    assert speed is not None
    assert speed == pytest.approx(1000, rel=0.05)
    assert average_speed(window) == pytest.approx(1000, rel=0.05)


def test_format_speed_keeps_a_stable_width() -> None:
    slow = format_speed(100 * 1024)
    fast = format_speed(2 * 1024 * 1024)
    assert slow.endswith("MB/s")
    assert fast.endswith("MB/s")
    assert len(slow) == len(fast)
    assert "KB" not in slow
    assert "KB" not in fast


def test_job_eta_and_cancel() -> None:
    job = TransferJob(
        id="j1",
        profile_id="p",
        direction=TransferDirection.DOWNLOAD,
        local_path=r"C:\tmp\a.bin",
        remote_path="/a.bin",
        nbytes_total=1000,
        nbytes_done=250,
        speed_bps=250,
        status=TransferStatus.RUNNING,
    )
    assert job.eta_seconds() == 3
    job.cancel_event.set()
    assert job.cancel_event.is_set()
    job.pause_event.set()
    assert job.pause_event.is_set()
    job.pause_event.clear()
    job.status = TransferStatus.PAUSED
    assert job.status is TransferStatus.PAUSED


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ghostport.app import GhostportApp

    existing = GhostportApp.instance()
    if existing is not None:
        yield existing
        return
    app = GhostportApp(["ghostport-test"])
    yield app
    app.quit()


def test_remote_mime_roundtrip(qapp) -> None:
    entry = FileEntry(
        name="notes.txt",
        path="/home/simtra/notes.txt",
        is_dir=False,
        size=12,
        modified_ts=1_700_000_000.0,
        mode=0o644,
    )
    mime = entries_to_mime([entry])
    back = entries_from_mime(mime)
    assert len(back) == 1
    assert back[0].name == "notes.txt"
    assert back[0].path == "/home/simtra/notes.txt"
    assert back[0].size == 12
