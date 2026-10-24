from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyte
from pyte.screens import Char, HistoryScreen


# pyte 0.8 has no alt-screen buffer. We track DECSET 1049/1047/47 ourselves
# and treat in_alt_screen as a flag only (full snapshot repaint, no dual resize).
_ALT_MODES = {47, 1047, 1049}
_DECCKM = 1


@dataclass(frozen=True)
class CellSnapshot:
    cols: int
    rows: int
    lines: tuple[tuple[Char, ...], ...]
    dirty: frozenset[int]
    cursor_x: int
    cursor_y: int
    cursor_hidden: bool
    in_alt_screen: bool
    application_cursor: bool
    title: str


class _GhostScreen(HistoryScreen):
    def __init__(self, columns: int, lines: int, history: int) -> None:
        super().__init__(columns, lines, history=history)
        self.in_alt_screen = False
        self.application_cursor = False

    def set_mode(self, *modes: int, **kwargs: Any) -> None:
        super().set_mode(*modes, **kwargs)
        if kwargs.get("private"):
            if any(m in _ALT_MODES for m in modes):
                self.in_alt_screen = True
                self.dirty.update(range(self.lines))
            if _DECCKM in modes:
                self.application_cursor = True

    def reset_mode(self, *modes: int, **kwargs: Any) -> None:
        super().reset_mode(*modes, **kwargs)
        if kwargs.get("private"):
            if any(m in _ALT_MODES for m in modes):
                self.in_alt_screen = False
                self.dirty.update(range(self.lines))
            if _DECCKM in modes:
                self.application_cursor = False

    def reset(self) -> None:
        super().reset()
        self.in_alt_screen = False
        self.application_cursor = False


DEFAULT_HISTORY = 4000
MAX_HISTORY = 10000
MIN_HISTORY = 200


def clamp_history(history: int) -> int:
    try:
        value = int(history)
    except (TypeError, ValueError):
        return DEFAULT_HISTORY
    return max(MIN_HISTORY, min(MAX_HISTORY, value))


class PtyBridge:
    """Owns pyte HistoryScreen + Stream. Mutated only on the GUI thread."""

    def __init__(self, cols: int = 80, rows: int = 24, history: int = DEFAULT_HISTORY) -> None:
        self._history_max = max(1, int(history))
        self._screen = _GhostScreen(cols, rows, history=self._history_max)
        self._stream = pyte.Stream(self._screen)
        self._lines: tuple[tuple[Char, ...], ...] | None = None

    @property
    def cols(self) -> int:
        return self._screen.columns

    @property
    def rows(self) -> int:
        return self._screen.lines

    @property
    def in_alt_screen(self) -> bool:
        return self._screen.in_alt_screen

    def history_len(self) -> int:
        return len(self._screen.history.top)

    def buffer_len(self) -> int:
        return self.history_len() + self.rows

    def abs_y_for_viewport(self, viewport_y: int, scroll_offset: int) -> int:
        if self._screen.in_alt_screen:
            return max(0, viewport_y)
        start = max(0, self.history_len() - max(0, scroll_offset))
        return start + max(0, viewport_y)

    def viewport_y_for_abs(self, abs_y: int, scroll_offset: int) -> int:
        if self._screen.in_alt_screen:
            return abs_y
        start = max(0, self.history_len() - max(0, scroll_offset))
        return abs_y - start

    def row_at_abs(self, abs_y: int) -> tuple[Char, ...]:
        blank = tuple(self._screen.default_char for _ in range(self._screen.columns))
        if self._screen.in_alt_screen:
            if 0 <= abs_y < self._screen.lines:
                return self._copy_live_row(abs_y)
            return blank
        if abs_y < 0:
            return blank
        hist = self._screen.history.top
        n = len(hist)
        if abs_y < n:
            return self._line_from_buffer_row(hist[abs_y], self._screen.columns)
        live_y = abs_y - n
        if 0 <= live_y < self._screen.lines:
            return self._copy_live_row(live_y)
        return blank

    def line_text(self, abs_y: int) -> str:
        return "".join(ch.data or " " for ch in self.row_at_abs(abs_y)).rstrip()

    def feed(self, data: bytes) -> CellSnapshot:
        text = data.decode("utf-8", errors="replace")
        self._stream.feed(text)
        self._trim_history()
        return self._snapshot(dirty=frozenset(self._screen.dirty))

    def reset(self) -> CellSnapshot:
        self._screen.reset()
        self._lines = None
        self._screen.dirty.update(range(self._screen.lines))
        return self._snapshot(dirty=frozenset(range(self._screen.lines)))

    def resize(self, cols: int, rows: int) -> CellSnapshot:
        cols = max(8, cols)
        rows = max(2, rows)
        if cols != self._screen.columns or rows != self._screen.lines:
            self._screen.resize(lines=rows, columns=cols)
        self._trim_history()
        self._screen.dirty.update(range(self._screen.lines))
        return self._snapshot(dirty=frozenset(range(self._screen.lines)))

    def display_row(self, y: int, *, scroll_offset: int = 0) -> tuple[Char, ...]:
        """y is 0 at the top of the visible widget. scroll_offset lines of history above live."""
        blank = tuple(self._screen.default_char for _ in range(self._screen.columns))
        if y < 0:
            return blank
        if scroll_offset <= 0 or self._screen.in_alt_screen:
            if 0 <= y < self._screen.lines:
                return self._copy_live_row(y)
            return blank
        hist = self._screen.history.top
        n = len(hist)
        start = max(0, n - scroll_offset)
        idx = start + y
        if idx < n:
            return self._line_from_buffer_row(hist[idx], self._screen.columns)
        live_y = y - (n - start)
        if 0 <= live_y < self._screen.lines:
            return self._copy_live_row(live_y)
        return blank

    def _trim_history(self) -> None:
        top = self._screen.history.top
        extra = len(top) - self._history_max
        if extra <= 0:
            return
        for _ in range(extra):
            top.popleft()

    def _copy_live_row(self, y: int) -> tuple[Char, ...]:
        line = self._screen.buffer[y]
        return tuple(line[x] for x in range(self._screen.columns))

    def _line_from_buffer_row(self, row: object, columns: int) -> tuple[Char, ...]:
        default = self._screen.default_char
        try:
            return tuple(row[x] if x in row else default for x in range(columns))  # type: ignore[index]
        except Exception:
            return tuple(default for _ in range(columns))

    def _snapshot(self, dirty: frozenset[int]) -> CellSnapshot:
        screen = self._screen
        if (
            self._lines is None
            or len(self._lines) != screen.lines
            or (self._lines and len(self._lines[0]) != screen.columns)
            or len(dirty) >= screen.lines
        ):
            lines = tuple(self._copy_live_row(y) for y in range(screen.lines))
            dirty = frozenset(range(screen.lines))
        else:
            mutable = list(self._lines)
            for y in dirty:
                if 0 <= y < screen.lines:
                    mutable[y] = self._copy_live_row(y)
            lines = tuple(mutable)
        self._lines = lines
        screen.dirty.clear()
        return CellSnapshot(
            cols=screen.columns,
            rows=screen.lines,
            lines=lines,
            dirty=dirty,
            cursor_x=screen.cursor.x,
            cursor_y=screen.cursor.y,
            cursor_hidden=bool(screen.cursor.hidden),
            in_alt_screen=screen.in_alt_screen,
            application_cursor=screen.application_cursor,
            title=screen.title or "",
        )
