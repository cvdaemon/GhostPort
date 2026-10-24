from __future__ import annotations

import re

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ghostport.config.settings import Settings
from ghostport.errors import GhostportError
from ghostport.profiles.models import ServerProfile
from ghostport.ssh.pty_bridge import PtyBridge, clamp_history
from ghostport.ssh.session import ConnectionState, SshSession
from ghostport.ui.widgets.idle_closer import IdleCloser
from ghostport.ui.widgets.terminal_widget import TerminalWidget

_MAX_FEED_BYTES = 48 * 1024
_MAX_PENDING_BYTES = 192 * 1024


def clip_pending_bytes(buf: bytearray, max_bytes: int = _MAX_PENDING_BYTES) -> bytearray:
    """Keep the tail of a flood of PTY output so docker logs cannot fill RAM."""
    if len(buf) <= max_bytes:
        return buf
    keep = bytes(buf[-max_bytes:])
    nl = keep.find(b"\n")
    if 0 <= nl < len(keep) - 1:
        keep = keep[nl + 1 :]
    return bytearray(keep)

# Colon prompts (`user@host:~/path$`) and bracket prompts (`[user@host ~/path]$`).
# The path must be absolute or `~`, so a basename-only prompt is ignored.
_PROMPT_COLON = re.compile(
    r"(?P<user>[^\s@]+)@(?P<host>[^\s:]+):\s*(?P<path>~/[^\s#$%]*|~(?=$|[\s#$%])|/[^\s#$%]*)"
)
_PROMPT_BRACKET = re.compile(
    r"\[(?P<user>[^\s@\]]+)@(?P<host>[^\s\]]+)\s+(?P<path>~/[^\s\]]+|~|/[^\s\]]+)\]"
)


def _usable_path(path: str) -> str | None:
    text = (path or "").strip()
    if text == "~" or text.startswith("~/") or text.startswith("/"):
        return text
    return None


def cwd_from_prompt_line(line: str) -> str | None:
    """Last shell prompt path on this line, including a command typed after it."""
    text = (line or "").strip()
    if not text:
        return None
    best_at = -1
    best: str | None = None
    for pattern in (_PROMPT_COLON, _PROMPT_BRACKET):
        for match in pattern.finditer(text):
            path = _usable_path(match.group("path") or "")
            if path is None or match.end() < best_at:
                continue
            best = path
            best_at = match.end()
    return best


def cwd_from_screen_lines(lines: list[str], *, title: str = "", lookback: int = 400) -> str | None:
    """Prompt path from recent rows, then the window title. Joins the tail so a wrapped prompt matches."""
    window = list(lines[-lookback:]) if lines else []
    if window:
        tail = "".join(part.rstrip() for part in window[-4:])
        found = cwd_from_prompt_line(tail)
        if found:
            return found
        for line in reversed(window):
            found = cwd_from_prompt_line(line)
            if found:
                return found
    from_title = cwd_from_prompt_line(title)
    if from_title:
        return from_title
    title_text = (title or "").strip()
    if title_text.startswith("~") or title_text.startswith("/"):
        token = _usable_path(title_text.split()[0])
        if token:
            return token
    return None


class SshTab(QWidget):
    close_requested = Signal()
    reconnect_requested = Signal()
    open_sftp_requested = Signal()
    edit_server_requested = Signal(str)
    title_changed = Signal(str)
    state_icon_changed = Signal(object)

    def __init__(
        self,
        profile: ServerProfile,
        session: SshSession,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tab_id = id(self)
        self.profile = profile
        self.session = session
        self.title_override: str | None = None
        self.bridge = PtyBridge(
            cols=80,
            rows=24,
            history=clamp_history(settings.terminal.scrollback_lines),
        )
        self.terminal = TerminalWidget(self)
        self.terminal.set_bridge(self.bridge)
        self.terminal.configure(
            font_family=settings.terminal.font_family,
            font_size=settings.terminal.font_size,
            cursor_style=settings.terminal.cursor_style,
            paste_warning_threshold=settings.terminal.paste_warning_threshold,
            copy_on_select=settings.terminal.copy_on_select,
        )
        self._buf = bytearray()
        self._remote_cwd: str | None = None
        self._flush = QTimer(self)
        self._flush.setInterval(8)
        self._flush.setSingleShot(True)
        self._flush.timeout.connect(self._flush_bytes)
        self._last_error: GhostportError | None = None

        self._overlay = QWidget(self)
        self._overlay.setObjectName("sshOverlay")
        self._overlay.setStyleSheet("background-color: rgba(18, 18, 18, 235);")
        self._overlay_title = QLabel("Connecting...")
        self._overlay_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._overlay_body = QLabel(f"{profile.host}:{profile.port}")
        self._overlay_body.setWordWrap(True)
        self._reconnect_btn = QPushButton("Reconnect")
        self._reconnect_btn.setObjectName("primaryButton")
        self._reconnect_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_btn = QPushButton("Close Session")
        self._close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._edit_btn = QPushButton("Edit Server")
        self._edit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._reconnect_btn)
        row.addWidget(self._edit_btn)
        row.addWidget(self._close_btn)
        row.addStretch(1)
        ov = QVBoxLayout(self._overlay)
        ov.addStretch(1)
        ov.addWidget(self._overlay_title, alignment=Qt.AlignmentFlag.AlignHCenter)
        ov.addWidget(self._overlay_body, alignment=Qt.AlignmentFlag.AlignHCenter)
        ov.addSpacing(12)
        ov.addLayout(row)
        ov.addStretch(1)

        self._find = QLineEdit()
        self._find.setPlaceholderText("Find in terminal...")
        self._find.setVisible(False)
        self._find.returnPressed.connect(self._do_find)
        find_close = QPushButton("×")
        find_close.setFixedWidth(28)
        find_close.clicked.connect(lambda: self._find.setVisible(False))
        find_row = QHBoxLayout()
        find_row.addWidget(self._find, 1)
        find_row.addWidget(find_close)
        self._find_wrap = QWidget()
        self._find_wrap.setLayout(find_row)
        self._find_wrap.setVisible(False)

        stack = QStackedLayout()
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self.terminal)
        stack.addWidget(self._overlay)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._find_wrap)
        holder = QWidget()
        holder.setLayout(stack)
        root.addWidget(holder, 1)

        session.bytes_received.connect(self.on_bytes)
        session.state_changed.connect(self.on_state)
        session.error_occurred.connect(self.on_error)
        self.terminal.pty_resized.connect(self._on_pty_resized)
        self.terminal.pty_resized.connect(session.resize_pty)
        self.terminal.stdin_bytes.connect(session.send_stdin)
        self._idle = IdleCloser(settings.connections.idle_timeout_min, self)
        self._idle.idle.connect(self.close_requested)
        self.terminal.stdin_bytes.connect(self._idle.touch)
        self.terminal.installEventFilter(self)
        self.terminal.reconnect_requested.connect(self.reconnect_requested)
        self.terminal.disconnect_requested.connect(session.disconnect)
        self.terminal.open_sftp_requested.connect(self.open_sftp_requested)
        self.terminal.find_requested.connect(self.show_find)
        self.terminal.clear_requested.connect(self._clear)
        self._reconnect_btn.clicked.connect(self.reconnect_requested)
        self._close_btn.clicked.connect(self.close_requested)
        self._edit_btn.clicked.connect(lambda: self.edit_server_requested.emit(self.profile.id))
        self.on_state(session.state)

    def display_title(self) -> str:
        return self.title_override or self.profile.name

    def on_bytes(self, data: bytes) -> None:
        self._buf.extend(data)
        self._buf = clip_pending_bytes(self._buf)
        # Keystrokes are tiny: paint now. Bulk output coalesces on an 8ms timer.
        if len(self._buf) < 1024:
            self._flush.stop()
            self._flush_bytes()
        elif not self._flush.isActive():
            self._flush.start()

    def _flush_bytes(self) -> None:
        if not self._buf:
            return
        take = min(len(self._buf), _MAX_FEED_BYTES)
        chunk = bytes(self._buf[:take])
        del self._buf[:take]
        n = len(chunk)
        snap = self.bridge.feed(chunk)
        self.terminal.apply_snapshot(snap)
        self.session.ack_bytes(n)
        if self._buf and not self._flush.isActive():
            self._flush.start()
        if snap.title:
            self.title_override = self.title_override or None
        if not snap.in_alt_screen:
            found = self._cwd_near_cursor(snap)
            if found:
                self._remote_cwd = found

    def _on_pty_resized(self, cols: int, rows: int, width_px: int, height_px: int) -> None:
        snap = self.bridge.resize(cols, rows)
        self.terminal.apply_snapshot(snap)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        etype = event.type()
        if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self._idle.touch()
        return super().eventFilter(watched, event)

    def on_state(self, state: ConnectionState) -> None:
        self.terminal.set_interactive(state is ConnectionState.CONNECTED)
        self.state_icon_changed.emit(state)
        self._idle.set_armed(state is ConnectionState.CONNECTED)
        if state is ConnectionState.CONNECTED:
            self._overlay.hide()
            self.terminal.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._overlay.show()
        self._overlay.raise_()
        if state is ConnectionState.CONNECTING or state is ConnectionState.RECONNECTING:
            self._overlay_title.setText("Connecting...")
            self._overlay_body.setText(f"{self.profile.host}:{self.profile.port}")
            self._reconnect_btn.hide()
            self._edit_btn.hide()
        elif state is ConnectionState.FAILED:
            self._overlay_title.setText("Connection failed")
            body = self._last_error.user_body() if self._last_error else "Unable to connect."
            self._overlay_body.setText(body)
            self._reconnect_btn.show()
            self._edit_btn.show()
        else:
            self._overlay_title.setText("Connection lost")
            self._overlay_body.setText(f"{self.profile.name}\n{self.profile.host}:{self.profile.port}")
            self._reconnect_btn.show()
            self._edit_btn.hide()

    def on_error(self, error: GhostportError) -> None:
        self._last_error = error

    def show_find(self) -> None:
        self._find_wrap.setVisible(True)
        self._find.setVisible(True)
        self._find.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._find.selectAll()

    def _do_find(self) -> None:
        needle = self._find.text().lower()
        if not needle:
            return
        cols, rows = self.terminal.grid_size()
        for y in range(rows):
            row = self.bridge.display_row(y, scroll_offset=self.terminal.scroll_offset)
            hay = "".join(ch.data for ch in row).lower()
            if needle in hay:
                return

    def _clear(self) -> None:
        snap = self.bridge.reset()
        self.terminal.apply_snapshot(snap)
        self.terminal.reset_view()

    def _cwd_near_cursor(self, snap) -> str | None:  # type: ignore[no-untyped-def]
        cursor_abs = self.bridge.history_len() + max(0, snap.cursor_y)
        parts = [
            self.bridge.line_text(y).rstrip()
            for y in range(max(0, cursor_abs - 3), cursor_abs + 1)
        ]
        # Title is often stale (login dir). Only the rows around the cursor update the cache.
        return cwd_from_screen_lines(parts, title="")

    def guess_remote_cwd(self) -> str | None:
        """Directory the interactive shell is in, from its prompt (cached across long output)."""
        snap = self.terminal._snapshot
        if snap is not None and snap.in_alt_screen:
            return self._remote_cwd
        lines: list[str] = []
        last = self.bridge.buffer_len() - 1
        start = max(0, last - 399)
        for abs_y in range(start, last + 1):
            lines.append(self.bridge.line_text(abs_y))
        title = snap.title if snap is not None else ""
        found = cwd_from_screen_lines(lines, title=title)
        if found:
            self._remote_cwd = found
            return found
        return self._remote_cwd

    def shutdown(self) -> None:
        self._idle.set_armed(False)
        self.session.disconnect()


def state_tab_icon(state: ConnectionState, size: int = 12) -> QIcon:
    colors = {
        ConnectionState.CONNECTED: QColor("#3d9b94"),
        ConnectionState.CONNECTING: QColor("#c9a227"),
        ConnectionState.RECONNECTING: QColor("#c9a227"),
        ConnectionState.FAILED: QColor("#c45454"),
        ConnectionState.DISCONNECTED: QColor("#6b6b6b"),
        ConnectionState.IDLE: QColor("#6b6b6b"),
    }
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(colors.get(state, QColor("#6b6b6b")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pix)
