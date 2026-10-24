from __future__ import annotations

import os

import pytest


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


def test_canvas_uses_widget_device_pixel_ratio(qapp) -> None:
    from ghostport.ui.widgets.terminal_widget import TerminalWidget

    class Scaled(TerminalWidget):
        def devicePixelRatioF(self) -> float:
            return 1.5

    widget = Scaled()
    widget._cell_w = 8
    widget._cell_h = 16
    created = widget._ensure_canvas(10, 5)
    assert created is True
    canvas = widget._canvas
    assert canvas is not None
    assert canvas.devicePixelRatio() == pytest.approx(1.5)
    assert canvas.width() == 120
    assert canvas.height() == 120
    assert widget._ensure_canvas(10, 5) is False


def test_terminal_font_is_hinted_monospace(qapp) -> None:
    from PySide6.QtGui import QFont

    from ghostport.ui.widgets.terminal_widget import pick_terminal_font

    font = pick_terminal_font("", 11)
    assert font.fixedPitch()
    assert font.hintingPreference() == QFont.HintingPreference.PreferFullHinting
    assert font.styleStrategy() & QFont.StyleStrategy.NoSubpixelAntialias
