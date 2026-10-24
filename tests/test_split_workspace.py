from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QEvent, QPoint, QSize
from PySide6.QtWidgets import QLabel, QWidget
from shiboken6 import isValid

from ghostport.ui.split_workspace import DropSide, SplitWorkspace, drop_side_for


class _Home(QWidget):
    is_home_page = True


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


def test_drop_side_edges_and_center() -> None:
    size = QSize(200, 200)
    assert drop_side_for(QPoint(10, 100), size) is DropSide.LEFT
    assert drop_side_for(QPoint(190, 100), size) is DropSide.RIGHT
    assert drop_side_for(QPoint(100, 10), size) is DropSide.TOP
    assert drop_side_for(QPoint(100, 190), size) is DropSide.BOTTOM
    assert drop_side_for(QPoint(100, 100), size) is DropSide.CENTER


def test_unsplit_workspace_finds_primary_pane(qapp) -> None:
    workspace = SplitWorkspace()
    assert workspace.pane_count() == 1
    label = QLabel("one")
    workspace._active.tabs.addTab(label, "one")
    found = workspace.locate(label)
    assert found is not None
    pane, index = found
    assert pane is workspace._active
    assert index == 0
    workspace.deleteLater()


def test_split_and_unsplit_keeps_home_alive(qapp) -> None:
    workspace = SplitWorkspace(home_locked=True)
    home = _Home()
    ssh = QLabel("ssh")
    workspace.primary.tabs.addTab(home, "Home")
    workspace.primary.tabs.addTab(ssh, "ssh")
    assert workspace.handle_drop(id(ssh), workspace.primary, DropSide.RIGHT)
    assert workspace.pane_count() == 2
    assert isValid(home)
    found = workspace.locate(home)
    assert found is not None
    home_pane, _index = found
    workspace._drag_depth = 1
    assert workspace.handle_drop(id(ssh), home_pane, DropSide.CENTER)
    qapp.processEvents()
    assert workspace.pane_count() == 2
    assert isValid(home)
    workspace._drag_depth = 0
    workspace._flush_collapse()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert isValid(home)
    assert workspace.locate(home) is not None
    pane = workspace.live_pane()
    assert pane is not None
    assert isValid(pane.tabs)
    assert workspace.pane_count() == 1
    workspace.deleteLater()


def test_locate_deleted_widget_does_not_raise(qapp) -> None:
    workspace = SplitWorkspace()
    gone = QLabel("gone")
    workspace.primary.tabs.addTab(gone, "gone")
    gone.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert workspace.locate(gone) is None
    workspace.deleteLater()
