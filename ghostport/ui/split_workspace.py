from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QByteArray, QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ghostport.constants import MAX_SPLIT_PANES

MIME_TYPE = "application/x-ghostport-tab"


class DropSide(str, Enum):
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


def drop_side_for(pos: QPoint, size: QSize) -> DropSide:
    """Any-direction split, not a fixed grid. Edge strips split; center docks as a tab."""
    w, h = max(1, size.width()), max(1, size.height())
    x, y = pos.x(), pos.y()
    edge_x = w * 0.22
    edge_y = h * 0.22
    if x < edge_x:
        return DropSide.LEFT
    if x > w - edge_x:
        return DropSide.RIGHT
    if y < edge_y:
        return DropSide.TOP
    if y > h - edge_y:
        return DropSide.BOTTOM
    return DropSide.CENTER


class PaneGrip(QWidget):
    """Visible handle to drag the current tab into another pane."""

    def __init__(self, pane: SessionPane) -> None:
        super().__init__(pane)
        self._pane = pane
        self.setFixedWidth(16)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to split or move this tab")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setPen(QColor("#6b6b6b"))
        cx = self.width() // 2
        for i, y in enumerate(range(8, self.height() - 8, 5)):
            painter.drawPoint(cx - 2, y)
            painter.drawPoint(cx + 2, y)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pane.begin_tab_drag(self._pane.tabs.currentIndex())
        super().mousePressEvent(event)


class DropOverlay(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.side = DropSide.CENTER
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def set_side(self, side: DropSide) -> None:
        self.side = side
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#3d9b94")
        color.setAlpha(90)
        painter.setBrush(color)
        painter.setPen(QColor("#3d9b94"))
        painter.drawRect(self._rect_for(self.side).adjusted(2, 2, -2, -2))
        painter.end()

    def _rect_for(self, side: DropSide) -> QRect:
        r = self.rect()
        if side is DropSide.LEFT:
            return QRect(0, 0, r.width() // 2, r.height())
        if side is DropSide.RIGHT:
            return QRect(r.width() // 2, 0, r.width() // 2, r.height())
        if side is DropSide.TOP:
            return QRect(0, 0, r.width(), r.height() // 2)
        if side is DropSide.BOTTOM:
            return QRect(0, r.height() // 2, r.width(), r.height() // 2)
        return r.adjusted(r.width() // 8, r.height() // 8, -r.width() // 8, -r.height() // 8)


class DragTabBar(QTabBar):
    def __init__(self, pane: SessionPane) -> None:
        super().__init__(pane)
        self._pane = pane
        self._press: QPoint | None = None
        self.setMovable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.position().toPoint() - self._press
            if delta.manhattanLength() > 12:
                index = self.tabAt(self._press)
                self._press = None
                if index >= 0:
                    self._pane.begin_tab_drag(index)
                    return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press = None
        super().mouseReleaseEvent(event)


class SessionPane(QWidget):
    activated = Signal(object)

    def __init__(self, workspace: SplitWorkspace, *, home_locked: bool) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.home_locked = home_locked
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(False)
        bar = DragTabBar(self)
        self.tabs.setTabBar(bar)
        self.tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grip = PaneGrip(self)
        self.tabs.setCornerWidget(self.grip, Qt.Corner.TopLeftCorner)
        self.overlay = DropOverlay(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)
        self.setAcceptDrops(True)
        self.tabs.currentChanged.connect(lambda _i: self.activated.emit(self))
        self.tabs.tabCloseRequested.connect(self._emit_close)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(
            lambda pos: self.workspace.tab_menu_requested.emit(self, pos)
        )

    def _emit_close(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is not None:
            self.workspace.tab_close_requested.emit(widget)

    def can_drag_index(self, index: int) -> bool:
        if index < 0:
            return False
        widget = self.tabs.widget(index)
        if widget is None:
            return False
        if self.home_locked and getattr(widget, "is_home_page", False):
            return False
        return True

    def begin_tab_drag(self, index: int) -> None:
        if not self.can_drag_index(index):
            return
        if self.workspace.total_tab_count() < 2:
            return
        widget = self.tabs.widget(index)
        if widget is None or not isValid(widget):
            return
        mime = QMimeData()
        mime.setData(MIME_TYPE, QByteArray(str(id(widget)).encode("ascii")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Collapse runs after exec returns. deleteLater inside this nested loop
        # destroys the drag source and any Home page still parented under it.
        self.workspace._drag_depth += 1
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.workspace._drag_depth = max(0, self.workspace._drag_depth - 1)
            if self.workspace._drag_depth == 0:
                self.workspace._flush_collapse()

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()
            self.overlay.setGeometry(self.rect())
            self.overlay.show()
            self.overlay.raise_()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.mimeData().hasFormat(MIME_TYPE):
            event.ignore()
            return
        event.acceptProposedAction()
        side = drop_side_for(event.position().toPoint(), self.size())
        self.overlay.set_side(side)
        self.overlay.setGeometry(self.rect())
        self.overlay.show()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.overlay.hide()
        raw = bytes(event.mimeData().data(MIME_TYPE)).decode("ascii")
        try:
            widget_id = int(raw)
        except ValueError:
            event.ignore()
            return
        side = drop_side_for(event.position().toPoint(), self.size())
        if self.workspace.handle_drop(widget_id, self, side):
            event.acceptProposedAction()
        else:
            event.ignore()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())


class SplitWorkspace(QWidget):
    """Nested splitters. Free resize, no preset grid. Max MAX_SPLIT_PANES leaves."""

    pane_activated = Signal(object)
    tab_close_requested = Signal(object)
    current_widget_changed = Signal(object)
    tab_menu_requested = Signal(object, object)

    def __init__(self, parent: QWidget | None = None, *, home_locked: bool = False) -> None:
        super().__init__(parent)
        self._home_locked = home_locked
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self.primary = SessionPane(self, home_locked=home_locked)
        self.primary.activated.connect(self._on_pane_activated)
        self._root_layout.addWidget(self.primary)
        self._active: SessionPane = self.primary
        self._drag_depth = 0
        self._pending_collapse: list[SessionPane] = []
        self.primary.tabs.currentChanged.connect(self._emit_current)

    def _on_pane_activated(self, pane: SessionPane) -> None:
        self._active = pane
        self.pane_activated.emit(pane)
        self._emit_current()

    def _emit_current(self) -> None:
        pane = self._active if self._active is not None and isValid(self._active) else None
        if pane is None or not isValid(pane.tabs):
            pane = self.live_pane()
        widget = None
        if pane is not None:
            try:
                widget = pane.tabs.currentWidget()
            except RuntimeError:
                widget = None
        self.current_widget_changed.emit(widget)

    def live_pane(self) -> SessionPane | None:
        """Active pane whose C++ tab widget is still alive."""
        active = self._active
        if self._pane_alive(active):
            return active
        for pane in self.panes():
            if self._pane_alive(pane):
                self._active = pane
                return pane
        if self._pane_alive(self.primary):
            self._active = self.primary
            return self.primary
        return None

    def _pane_alive(self, pane: SessionPane | None) -> bool:
        if pane is None or not isValid(pane):
            return False
        tabs = getattr(pane, "tabs", None)
        return tabs is not None and isValid(tabs)

    @property
    def tabs(self) -> QTabWidget:
        return self.primary.tabs

    def panes(self) -> list[SessionPane]:
        found: list[SessionPane] = []
        self._collect(self, found)
        return found

    def _collect(self, node: QWidget, out: list[SessionPane]) -> None:
        try:
            if not isValid(node):
                return
        except RuntimeError:
            return
        if isinstance(node, SessionPane):
            if self._pane_alive(node):
                out.append(node)
            return
        if isinstance(node, QSplitter):
            for i in range(node.count()):
                child = node.widget(i)
                if child is not None:
                    self._collect(child, out)
            return
        layout = node.layout()
        if layout is None:
            return
        for i in range(layout.count()):
            item = layout.itemAt(i)
            child = item.widget() if item is not None else None
            if child is not None:
                self._collect(child, out)

    def pane_count(self) -> int:
        return len(self.panes())

    def session_tab_count(self) -> int:
        n = 0
        for pane in self.panes():
            for i in range(pane.tabs.count()):
                widget = pane.tabs.widget(i)
                if widget is not None and isValid(widget) and not getattr(widget, "is_home_page", False):
                    n += 1
        return n

    def total_tab_count(self) -> int:
        return sum(pane.tabs.count() for pane in self.panes())

    def locate(self, widget: QWidget) -> tuple[SessionPane, int] | None:
        if widget is None or not isValid(widget):
            return None
        for pane in self.panes():
            try:
                index = pane.tabs.indexOf(widget)
            except RuntimeError:
                continue
            if index >= 0:
                return pane, index
        return None

    def iter_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = []
        for pane in self.panes():
            for i in range(pane.tabs.count()):
                widget = pane.tabs.widget(i)
                if widget is not None and isValid(widget):
                    widgets.append(widget)
        return widgets

    def handle_drop(self, widget_id: int, target: SessionPane, side: DropSide) -> bool:
        source_widget = None
        source_pane = None
        source_index = -1
        title = ""
        icon = None
        for pane in self.panes():
            for i in range(pane.tabs.count()):
                w = pane.tabs.widget(i)
                if w is not None and id(w) == widget_id:
                    source_widget = w
                    source_pane = pane
                    source_index = i
                    title = pane.tabs.tabText(i)
                    icon = pane.tabs.tabIcon(i)
                    break
        if source_widget is None or source_pane is None:
            return False
        if not source_pane.can_drag_index(source_index):
            return False
        if side is not DropSide.CENTER and self.pane_count() >= MAX_SPLIT_PANES:
            side = DropSide.CENTER
        if side is not DropSide.CENTER and source_pane is target and source_pane.tabs.count() <= 1:
            return False
        if side is DropSide.CENTER and source_pane is target:
            return False

        source_pane.tabs.removeTab(source_index)
        if side is DropSide.CENTER:
            self._add_tab(target, source_widget, title, icon)
        else:
            self._split(target, side, source_widget, title, icon)
        self._queue_collapse(source_pane)
        self._ensure_active()
        return True

    def _queue_collapse(self, pane: SessionPane) -> None:
        if self._drag_depth:
            if pane not in self._pending_collapse:
                self._pending_collapse.append(pane)
            return
        self._collapse_empty(pane)

    def _flush_collapse(self) -> None:
        pending = list(self._pending_collapse)
        self._pending_collapse.clear()
        for pane in pending:
            self._collapse_empty(pane)
        self._ensure_active()

    def _add_tab(self, pane: SessionPane, widget: QWidget, title: str, icon: QIcon) -> None:
        index = pane.tabs.addTab(widget, title)
        if icon is not None and not icon.isNull():
            pane.tabs.setTabIcon(index, icon)
        pane.tabs.setCurrentWidget(widget)

    def _split(
        self,
        pane: SessionPane,
        side: DropSide,
        widget: QWidget,
        title: str,
        icon,
    ) -> None:
        orient = (
            Qt.Orientation.Horizontal
            if side in (DropSide.LEFT, DropSide.RIGHT)
            else Qt.Orientation.Vertical
        )
        splitter = QSplitter(orient)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        new_pane = SessionPane(self, home_locked=False)
        new_pane.activated.connect(self._on_pane_activated)
        new_pane.tabs.currentChanged.connect(self._emit_current)
        self._add_tab(new_pane, widget, title, icon)
        first, second = (new_pane, pane) if side in (DropSide.LEFT, DropSide.TOP) else (pane, new_pane)
        if not self._replace(pane, splitter):
            self._add_tab(pane, widget, title, icon)
            new_pane.deleteLater()
            splitter.deleteLater()
            return
        splitter.addWidget(first)
        splitter.addWidget(second)
        splitter.setSizes([5000, 5000])
        self._active = new_pane

    def _replace(self, old: QWidget, new: QWidget) -> bool:
        if not isValid(old) or not isValid(new):
            return False
        parent = old.parentWidget()
        if parent is self:
            if self._root_layout.indexOf(old) < 0:
                return False
            self._root_layout.replaceWidget(old, new)
            old.setParent(None)
            return True
        if isinstance(parent, QSplitter) and isValid(parent):
            index = parent.indexOf(old)
            if index < 0:
                return False
            parent.replaceWidget(index, new)
            old.setParent(None)
            return True
        return False

    def _pane_has_home(self, pane: SessionPane) -> bool:
        if not self._pane_alive(pane):
            return False
        for i in range(pane.tabs.count()):
            widget = pane.tabs.widget(i)
            if widget is not None and isValid(widget) and getattr(widget, "is_home_page", False):
                return True
        return False

    def _collapse_empty(self, pane: SessionPane) -> None:
        if not self._pane_alive(pane):
            return
        if pane.tabs.count() > 0:
            return
        if pane is self.primary or pane.home_locked or self._pane_has_home(pane):
            return
        parent = pane.parentWidget()
        if not isinstance(parent, QSplitter) or not isValid(parent):
            return
        kept: list[QWidget] = []
        for i in range(parent.count()):
            child = parent.widget(i)
            if child is not None and child is not pane and isValid(child):
                kept.append(child)
        if len(kept) != 1:
            return
        sibling = kept[0]
        # Park the survivor on the workspace before the empty splitter is destroyed.
        sibling.setParent(self)
        if not self._replace(parent, sibling):
            parent.addWidget(sibling)
            return
        pane.setParent(None)
        pane.deleteLater()
        parent.setParent(None)
        parent.deleteLater()
        if isinstance(sibling, SessionPane) and self._pane_alive(sibling):
            self._active = sibling
        self._ensure_active()

    def _ensure_active(self) -> None:
        if self._pane_alive(self._active):
            return
        self.live_pane()

    def set_active(self, pane: SessionPane) -> None:
        if self._pane_alive(pane):
            self._active = pane
