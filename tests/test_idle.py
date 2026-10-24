from __future__ import annotations

import os

import pytest

from ghostport.config.settings import Settings
from ghostport.ui.widgets.idle_closer import IdleCloser, idle_timeout_ms


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


def test_idle_timeout_ms() -> None:
    assert idle_timeout_ms(45) == 45 * 60 * 1000
    assert idle_timeout_ms(0) == 0
    assert idle_timeout_ms(-3) == 0
    assert idle_timeout_ms("nope") == 0  # type: ignore[arg-type]


def test_idle_timeout_default_is_45() -> None:
    settings = Settings.from_dict({})
    assert settings.connections.idle_timeout_min == 45
    loaded = Settings.from_dict({"connections": {"idle_timeout_min": 0}})
    assert loaded.connections.idle_timeout_min == 0
    clamped = Settings.from_dict({"connections": {"idle_timeout_min": 9999}})
    assert clamped.connections.idle_timeout_min == 24 * 60


def test_idle_closer_fires(qapp) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    hits: list[int] = []
    closer = IdleCloser(0, interval_ms=30)
    closer.idle.connect(lambda: hits.append(1))
    closer.set_armed(True)
    loop = QEventLoop()
    QTimer.singleShot(120, loop.quit)
    loop.exec()
    assert hits == [1]


def test_idle_closer_zero_never_starts(qapp) -> None:
    closer = IdleCloser(0)
    closer.set_armed(True)
    assert not closer.is_active()


def test_idle_closer_touch_postpones(qapp) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    hits: list[int] = []
    closer = IdleCloser(0, interval_ms=80)
    closer.idle.connect(lambda: hits.append(1))
    closer.set_armed(True)
    loop = QEventLoop()
    QTimer.singleShot(40, closer.touch)
    QTimer.singleShot(70, loop.quit)
    loop.exec()
    assert hits == []
    assert closer.is_active()
