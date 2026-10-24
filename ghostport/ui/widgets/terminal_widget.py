from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from ghostport.ssh.pty_bridge import CellSnapshot, PtyBridge
from ghostport.ui.theme import DARK_TOKENS

TERM_BG = QColor("#121212")
TERM_FG = QColor("#d4d4d4")
_MIN_FG_LUMA = 145.0

# VGA navy (#0000ee) and friends are unreadable on a black terminal.
# These are dark-background 16-color values; bold maps to the bright set.
_NAMED = {
    "default": None,
    "black": "#1a1a1a",
    "red": "#ff6b6b",
    "green": "#3dd68c",
    "brown": "#e6c35c",
    "yellow": "#e6c35c",
    "blue": "#6ea8fe",
    "magenta": "#d989e0",
    "cyan": "#4ecbdb",
    "white": "#e8e8e8",
    "brightblack": "#9a9a9a",
    "brightred": "#ff8a8a",
    "brightgreen": "#5eeba8",
    "brightbrown": "#ffe66d",
    "brightyellow": "#ffe66d",
    "brightblue": "#8cbcff",
    "brightmagenta": "#eea8f5",
    "brightcyan": "#7ae6f2",
    "brightwhite": "#ffffff",
}

_CHROME_KEYS = {
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_T),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_W),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_R),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_S),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_C),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_V),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_F),
    (Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_Tab),
    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_Backtab),
    (Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_Insert),
    (Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_Insert),
}

_FKEYS = {
    Qt.Key.Key_F1: b"\x1bOP",
    Qt.Key.Key_F2: b"\x1bOQ",
    Qt.Key.Key_F3: b"\x1bOR",
    Qt.Key.Key_F4: b"\x1bOS",
    Qt.Key.Key_F5: b"\x1b[15~",
    Qt.Key.Key_F6: b"\x1b[17~",
    Qt.Key.Key_F7: b"\x1b[18~",
    Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~",
    Qt.Key.Key_F10: b"\x1b[21~",
    Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
}


def _luma(color: QColor) -> float:
    r, g, b, _a = color.getRgb()
    return 0.299 * r + 0.587 * g + 0.114 * b


def ensure_readable_fg(color: QColor) -> QColor:
    """Mix toward white so foreground paint stays visible on TERM_BG."""
    y = _luma(color)
    if y >= _MIN_FG_LUMA:
        return color
    y_white = 255.0
    t = (_MIN_FG_LUMA - y) / (y_white - y) if y_white > y else 1.0
    t = min(1.0, max(0.0, t))
    r, g, b, a = color.getRgb()
    return QColor(
        int(round(r + t * (255 - r))),
        int(round(g + t * (255 - g))),
        int(round(b + t * (255 - b))),
        a,
    )


def resolve_color(name: str, *, default: QColor, bold: bool = False) -> QColor:
    if not name or name == "default":
        return default
    if bold and not str(name).startswith("bright"):
        bright = _NAMED.get("bright" + name)
        if bright:
            return QColor(bright)
    named = _NAMED.get(name)
    if named:
        return QColor(named)
    if len(name) == 6 and all(c in "0123456789abcdefABCDEF" for c in name):
        return QColor("#" + name)
    return default


def pick_terminal_font(family: str, size: int) -> QFont:
    point = size if size > 0 else 11
    wanted = [family] if family else []
    wanted += ["Cascadia Mono", "Cascadia Code", "Consolas"]
    available = set(QFontDatabase.families())
    font = QFont()
    for name in wanted:
        if name and name in available:
            font = QFont(name)
            break
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setKerning(False)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias
        | QFont.StyleStrategy.NoSubpixelAntialias
        | QFont.StyleStrategy.NoFontMerging
    )
    if point > 0:
        font.setPointSize(point)
    if font.pointSize() <= 0:
        font.setPixelSize(max(12, point + 2))
    return font


class TerminalWidget(QWidget):
    stdin_bytes = Signal(bytes)
    pty_resized = Signal(int, int, int, int)
    copy_requested = Signal()
    paste_requested = Signal()
    select_all_requested = Signal()
    clear_requested = Signal()
    find_requested = Signal()
    reconnect_requested = Signal()
    disconnect_requested = Signal()
    open_sftp_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(False)
        self._bridge: PtyBridge | None = None
        self._snapshot: CellSnapshot | None = None
        self.scroll_offset = 0
        self._cursor_style = "block"
        self._cursor_on = True
        self._paste_threshold = 2000
        self._copy_on_select = True
        self._interactive = False
        self._sel_anchor: tuple[int, int] | None = None
        self._sel_end: tuple[int, int] | None = None
        self._cell_w = 8
        self._cell_h = 16
        self._font = pick_terminal_font("", 11)
        self.setFont(self._font)
        self._font_cache: dict[tuple[bool, bool, bool], QFont] = {}
        self._color_cache: dict[tuple[str, bool], QColor] = {}
        self._canvas: QPixmap | None = None
        self._canvas_key: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
        self._recompute_cell()
        self._blink = QTimer(self)
        self._blink.setInterval(530)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start()
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(40)
        self._edge_timer.timeout.connect(self._edge_scroll)
        self._dragging = False

    def set_bridge(self, bridge: PtyBridge) -> None:
        self._bridge = bridge

    def set_interactive(self, on: bool) -> None:
        self._interactive = on

    def configure(
        self,
        *,
        font_family: str,
        font_size: int,
        cursor_style: str,
        paste_warning_threshold: int,
        copy_on_select: bool,
    ) -> None:
        self._font = pick_terminal_font(font_family, font_size)
        self.setFont(self._font)
        self._font_cache.clear()
        self._canvas = None
        self._cursor_style = cursor_style
        self._paste_threshold = paste_warning_threshold
        self._copy_on_select = True
        self._recompute_cell()
        self._emit_resize()

    def apply_snapshot(self, snap: CellSnapshot) -> None:
        self._snapshot = snap
        if snap.in_alt_screen:
            self.scroll_offset = 0
        elif self._bridge is not None:
            self.scroll_offset = min(self.scroll_offset, self._bridge.history_len())
        if self.scroll_offset == 0:
            self._blit_snapshot(snap)
        self.update()

    def reset_view(self) -> None:
        self.scroll_offset = 0
        self._sel_anchor = None
        self._sel_end = None
        self.update()

    def _device_pixel_ratio(self) -> float:
        dpr = float(self.devicePixelRatioF() or 1.0)
        return dpr if dpr > 0 else 1.0

    def _recompute_cell(self) -> None:
        metrics = QFontMetrics(self._font, self)
        self._cell_w = max(1, metrics.horizontalAdvance("M"))
        self._cell_h = max(1, metrics.height())
        self._canvas = None

    def grid_size(self) -> tuple[int, int]:
        cols = max(8, self.width() // self._cell_w)
        rows = max(2, self.height() // self._cell_h)
        return cols, rows

    def _emit_resize(self) -> None:
        cols, rows = self.grid_size()
        self.pty_resized.emit(cols, rows, self.width(), self.height())

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._emit_resize()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._recompute_cell()
        self._emit_resize()

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        etype = event.type()
        watched = {
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            getattr(QEvent.Type, "DevicePixelRatioChange", None),
        }
        if etype in watched:
            self._recompute_cell()
            self._emit_resize()
            self.update()

    def _toggle_cursor(self) -> None:
        if not self.hasFocus() or self.scroll_offset != 0:
            return
        self._cursor_on = not self._cursor_on
        snap = self._snapshot
        if snap is None:
            self.update()
            return
        self.update(
            QRect(
                snap.cursor_x * self._cell_w,
                snap.cursor_y * self._cell_h,
                self._cell_w,
                self._cell_h,
            )
        )

    def _cached_fg(self, name: str, bold: bool) -> QColor:
        key = ("fg", name or "default", bold)
        color = self._color_cache.get(key)
        if color is None:
            color = ensure_readable_fg(resolve_color(name, default=TERM_FG, bold=bold))
            self._color_cache[key] = color
        return color

    def _cached_bg(self, name: str) -> QColor:
        key = ("bg", name or "default")
        color = self._color_cache.get(key)
        if color is None:
            color = resolve_color(name, default=TERM_BG)
            self._color_cache[key] = color
        return color

    def _style_font(self, bold: bool, underline: bool, italic: bool) -> QFont:
        key = (bold, underline, italic)
        font = self._font_cache.get(key)
        if font is None:
            font = QFont(self._font)
            font.setBold(bold)
            font.setUnderline(underline)
            font.setItalic(italic)
            self._font_cache[key] = font
        return font

    def _ensure_canvas(self, cols: int, rows: int) -> bool:
        dpr = self._device_pixel_ratio()
        key = (cols, rows, self._cell_w, self._cell_h, int(round(dpr * 1000)))
        if self._canvas is not None and self._canvas_key == key:
            return False
        logical_w = max(1, cols * self._cell_w)
        logical_h = max(1, rows * self._cell_h)
        phys_w = max(1, int(round(logical_w * dpr)))
        phys_h = max(1, int(round(logical_h * dpr)))
        canvas = QPixmap(phys_w, phys_h)
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(TERM_BG)
        self._canvas = canvas
        self._canvas_key = key
        return True

    def _blit_snapshot(self, snap: CellSnapshot) -> None:
        rebuilt = self._ensure_canvas(snap.cols, snap.rows)
        assert self._canvas is not None
        painter = QPainter(self._canvas)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self._font)
        logical_w = max(1, snap.cols * self._cell_w)
        rows = range(snap.rows) if rebuilt or not snap.dirty else (y for y in snap.dirty if 0 <= y < snap.rows)
        for y in rows:
            row = snap.lines[y] if y < len(snap.lines) else ()
            painter.fillRect(0, y * self._cell_h, logical_w, self._cell_h, TERM_BG)
            self._paint_run_row(painter, y, row, snap.cols)
        painter.end()

    def _paint_run_row(self, painter: QPainter, y: int, row, cols: int) -> None:
        x = 0
        n = min(len(row), cols)
        while x < n:
            ch = row[x]
            glyph = ch.data or " "
            start = x
            fg_name, bg_name = ch.fg, ch.bg
            bold, under, italic, reverse = ch.bold, ch.underscore, ch.italics, ch.reverse
            parts = [glyph]
            x += 1
            while x < n:
                nxt = row[x]
                if (
                    nxt.fg != fg_name
                    or nxt.bg != bg_name
                    or nxt.bold != bold
                    or nxt.underscore != under
                    or nxt.italics != italic
                    or nxt.reverse != reverse
                ):
                    break
                parts.append(nxt.data or " ")
                x += 1
            text = "".join(parts)
            fg = self._cached_fg(fg_name, bold)
            bg = self._cached_bg(bg_name)
            if reverse:
                fg, bg = ensure_readable_fg(bg), fg
            rect = QRect(start * self._cell_w, y * self._cell_h, (x - start) * self._cell_w, self._cell_h)
            if bg != TERM_BG:
                painter.fillRect(rect, bg)
            painter.setPen(fg)
            painter.setFont(self._style_font(bold, under, italic))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), TERM_BG)
        snap = self._snapshot
        if snap is None:
            painter.end()
            return
        offset = 0 if snap.in_alt_screen else self.scroll_offset
        if offset == 0 and self._canvas is not None:
            painter.drawPixmap(0, 0, self._canvas)
        else:
            bridge = self._bridge
            painter.setFont(self._font)
            if bridge is not None:
                for y in range(snap.rows):
                    row = bridge.display_row(y, scroll_offset=offset)
                    self._paint_run_row(painter, y, row, snap.cols)
        if self._sel_anchor is not None and self._sel_end is not None:
            sel = QColor(DARK_TOKENS["accent"])
            sel.setAlpha(90)
            ax, ay = self._sel_anchor
            bx, by = self._sel_end
            if (ay, ax) > (by, bx):
                ax, ay, bx, by = bx, by, ax, ay
            for abs_y in range(ay, by + 1):
                view_y = self._view_y(abs_y)
                if view_y < 0 or view_y >= snap.rows:
                    continue
                start = ax if abs_y == ay else 0
                end = bx + 1 if abs_y == by else snap.cols
                painter.fillRect(
                    start * self._cell_w,
                    view_y * self._cell_h,
                    max(1, end - start) * self._cell_w,
                    self._cell_h,
                    sel,
                )
        if self._cursor_on and self.hasFocus() and offset == 0 and not snap.cursor_hidden:
            self._paint_cursor(painter, snap)
        painter.end()

    def _paint_cursor(self, painter: QPainter, snap: CellSnapshot) -> None:
        x, y = snap.cursor_x, snap.cursor_y
        rect = QRect(x * self._cell_w, y * self._cell_h, self._cell_w, self._cell_h)
        if self._cursor_style == "bar":
            painter.fillRect(QRect(rect.x(), rect.y(), 2, rect.height()), TERM_FG)
        elif self._cursor_style == "underline":
            painter.fillRect(QRect(rect.x(), rect.bottom() - 2, rect.width(), 2), TERM_FG)
        else:
            painter.fillRect(rect, TERM_FG)
            if y < len(snap.lines) and x < len(snap.lines[y]):
                ch = snap.lines[y][x].data
                painter.setPen(TERM_BG)
                painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, ch)

    def _cell_selected(self, x: int, y: int) -> bool:
        if self._sel_anchor is None or self._sel_end is None:
            return False
        a = self._sel_anchor[1] * 4096 + self._sel_anchor[0]
        b = self._sel_end[1] * 4096 + self._sel_end[0]
        lo, hi = (a, b) if a <= b else (b, a)
        v = y * 4096 + x
        return lo <= v <= hi

    def encode_key(self, event: QKeyEvent) -> bytes | None:
        mods = event.modifiers()
        key = event.key()
        chrome = (mods & ~Qt.KeyboardModifier.KeypadModifier, key)
        # Ctrl+Shift+Tab arrives as Key_Backtab
        if chrome in _CHROME_KEYS or (
            mods & Qt.KeyboardModifier.ControlModifier
            and mods & Qt.KeyboardModifier.ShiftModifier
            and key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        ):
            return None
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return b"\r"
        if key == Qt.Key.Key_Backspace:
            return b"\x7f"
        if key == Qt.Key.Key_Tab:
            return b"\t"
        if key == Qt.Key.Key_Backtab:
            return b"\x1b[Z"
        if key == Qt.Key.Key_Escape:
            return b"\x1b"
        if key == Qt.Key.Key_Delete:
            return b"\x1b[3~"
        app_cursor = bool(self._snapshot and self._snapshot.application_cursor)
        arrows = {
            Qt.Key.Key_Up: (b"\x1b[A", b"\x1bOA"),
            Qt.Key.Key_Down: (b"\x1b[B", b"\x1bOB"),
            Qt.Key.Key_Right: (b"\x1b[C", b"\x1bOC"),
            Qt.Key.Key_Left: (b"\x1b[D", b"\x1bOD"),
            Qt.Key.Key_Home: (b"\x1b[H", b"\x1bOH"),
            Qt.Key.Key_End: (b"\x1b[F", b"\x1bOF"),
        }
        if key in arrows:
            csi, ss3 = arrows[key]
            return ss3 if app_cursor else csi
        if key == Qt.Key.Key_PageUp:
            return b"\x1b[5~"
        if key == Qt.Key.Key_PageDown:
            return b"\x1b[6~"
        if key in _FKEYS:
            return _FKEYS[key]
        if mods & Qt.KeyboardModifier.ControlModifier and not (mods & Qt.KeyboardModifier.AltModifier):
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                return bytes([key - Qt.Key.Key_A + 1])
            if key == Qt.Key.Key_Space:
                return b"\x00"
        text = event.text()
        if text:
            return text.encode("utf-8", errors="replace")
        return b""

    def event(self, event: QEvent) -> bool:
        # Qt uses Tab for focus chaining; grab it so bash/zsh completion works.
        etype = event.type()
        if etype == QEvent.Type.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.keyPressEvent(event)  # type: ignore[arg-type]
                return True
        if etype == QEvent.Type.ShortcutOverride:
            key = event.key()  # type: ignore[attr-defined]
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and event.key() in (
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
        ):
            if self._snapshot and not self._snapshot.in_alt_screen:
                delta = self.grid_size()[1] if event.key() == Qt.Key.Key_PageUp else -self.grid_size()[1]
                self._scroll(delta)
                event.accept()
                return
        encoded = self.encode_key(event)
        if encoded is None:
            event.ignore()
            return
        if not self._interactive:
            event.ignore()
            return
        if encoded:
            self.scroll_offset = 0
            self.stdin_bytes.emit(encoded)
            event.accept()
            return
        super().keyPressEvent(event)

    def inputMethodEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        commit = event.commitString()
        if commit and self._interactive:
            self.scroll_offset = 0
            self.stdin_bytes.emit(commit.encode("utf-8", errors="replace"))
            event.accept()
            return
        super().inputMethodEvent(event)

    def inputMethodQuery(self, query: Qt.InputMethodQuery):  # type: ignore[no-untyped-def]
        if query == Qt.InputMethodQuery.ImCursorRectangle and self._snapshot:
            x, y = self._snapshot.cursor_x, self._snapshot.cursor_y
            return QRect(x * self._cell_w, y * self._cell_h, self._cell_w, self._cell_h)
        return super().inputMethodQuery(query)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._snapshot and self._snapshot.in_alt_screen:
            event.ignore()
            return
        delta = event.angleDelta().y()
        steps = 3 if delta > 0 else -3
        self._scroll(steps)
        if self._dragging:
            self._sel_end = self._pos_to_cell(event.position())
            self.update()
        event.accept()

    def _scroll(self, lines: int) -> None:
        if self._bridge is None:
            return
        max_off = self._bridge.history_len()
        self.scroll_offset = max(0, min(max_off, self.scroll_offset + lines))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.paste_clipboard()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._sel_anchor = self._pos_to_cell(event.position())
            self._sel_end = self._sel_anchor
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and self._sel_anchor is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._sel_end = self._pos_to_cell(event.position())
            y = event.position().y()
            edge = self._cell_h
            if y <= edge or y >= self.height() - edge:
                self._edge_timer.start()
            else:
                self._edge_timer.stop()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._edge_timer.stop()
            if self._copy_on_select:
                if self._sel_anchor is not None and self._sel_end is not None and self._sel_anchor != self._sel_end:
                    self.copy_selection()
        super().mouseReleaseEvent(event)

    def _edge_scroll(self) -> None:
        if not self._dragging or self._bridge is None:
            self._edge_timer.stop()
            return
        pos = self.mapFromGlobal(QCursor.pos())
        edge = self._cell_h
        if pos.y() <= edge:
            self._scroll(3)
        elif pos.y() >= self.height() - edge:
            self._scroll(-3)
        else:
            self._edge_timer.stop()
            return
        self._sel_end = self._pos_to_cell(pos)
        self.update()

    def _view_y(self, abs_y: int) -> int:
        if self._bridge is None or (self._snapshot and self._snapshot.in_alt_screen):
            return abs_y
        return self._bridge.viewport_y_for_abs(abs_y, self.scroll_offset)

    def _pos_to_cell(self, pos) -> tuple[int, int]:
        cols, rows = self.grid_size()
        x = min(max(0, int(pos.x()) // self._cell_w), max(0, cols - 1))
        view_y = min(max(0, int(pos.y()) // self._cell_h), max(0, rows - 1))
        if self._bridge is None or (self._snapshot and self._snapshot.in_alt_screen):
            return x, view_y
        abs_y = self._bridge.abs_y_for_viewport(view_y, self.scroll_offset)
        abs_y = min(max(0, abs_y), max(0, self._bridge.buffer_len() - 1))
        return x, abs_y

    def selected_text(self) -> str:
        if self._sel_anchor is None or self._sel_end is None or self._bridge is None:
            return ""
        ax, ay = self._sel_anchor
        bx, by = self._sel_end
        if (ay, ax) > (by, bx):
            ax, ay, bx, by = bx, by, ax, ay
        lines: list[str] = []
        for abs_y in range(ay, by + 1):
            row = self._bridge.row_at_abs(abs_y)
            start = ax if abs_y == ay else 0
            end = bx + 1 if abs_y == by else len(row)
            chunk = "".join(ch.data or " " for ch in row[start:end])
            lines.append(chunk.rstrip())
        return "\n".join(lines)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def paste_clipboard(self) -> None:
        if not self._interactive:
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\n", "\r")
        if len(text) >= self._paste_threshold:
            box = QMessageBox.question(
                self,
                "Paste warning",
                f"Paste {len(text)} characters into the terminal?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if box != QMessageBox.StandardButton.Yes:
                return
        payload = text.encode("utf-8", errors="replace")
        self.scroll_offset = 0
        chunk = 4096
        for i in range(0, len(payload), chunk):
            self.stdin_bytes.emit(payload[i : i + chunk])

    def select_all(self) -> None:
        cols, rows = self.grid_size()
        last = max(0, rows - 1)
        if self._bridge is not None and not (self._snapshot and self._snapshot.in_alt_screen):
            last = max(0, self._bridge.buffer_len() - 1)
        self._sel_anchor = (0, 0)
        self._sel_end = (max(0, cols - 1), last)
        self.update()

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Right-click already pasted in mousePressEvent. No terminal menu.
        event.accept()
