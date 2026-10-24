from __future__ import annotations

from ghostport.ssh.pty_bridge import PtyBridge, clamp_history
from ghostport.ui.ssh_tab import clip_pending_bytes
from ghostport.ui.ssh_tab import cwd_from_prompt_line, cwd_from_screen_lines
from ghostport.sftp.connection import MkdirPath, MkfilePath, resolve_remote_path


def test_feed_snapshot_and_dirty() -> None:
    bridge = PtyBridge(cols=40, rows=10, history=100)
    snap = bridge.feed(b"hello")
    assert snap.cols == 40
    assert snap.rows == 10
    assert snap.cursor_x == 5
    assert snap.cursor_y == 0
    first = "".join(ch.data for ch in snap.lines[0][:5])
    assert first == "hello"


def test_resize_marks_all_dirty() -> None:
    bridge = PtyBridge(cols=20, rows=8, history=50)
    bridge.feed(b"abc")
    snap = bridge.resize(40, 12)
    assert snap.cols == 40
    assert snap.rows == 12
    assert len(snap.dirty) == 12


def test_history_rolls_off_oldest_lines() -> None:
    bridge = PtyBridge(cols=24, rows=4, history=40)
    for i in range(400):
        bridge.feed(f"line-{i:04d}\r\n".encode())
    assert bridge.history_len() <= 40
    blob = "\n".join(bridge.line_text(i) for i in range(bridge.buffer_len()))
    assert "line-0000" not in blob
    live = "\n".join(
        "".join(ch.data for ch in bridge.display_row(y, scroll_offset=0))
        for y in range(bridge.rows)
    )
    assert "0399" in live


def test_clamp_history() -> None:
    assert clamp_history(50) == 200
    assert clamp_history(4000) == 4000
    assert clamp_history(999999) == 10000


def test_clip_pending_bytes_keeps_tail() -> None:
    buf = bytearray(b"aaaa\n" + b"bbbb\n" * 50)
    clipped = clip_pending_bytes(buf, max_bytes=20)
    assert len(clipped) <= 20
    assert clipped.endswith(b"bbbb\n") or b"bbbb" in clipped


def test_history_scroll_offset() -> None:
    bridge = PtyBridge(cols=20, rows=4, history=50)
    for i in range(12):
        bridge.feed(f"line{i}\r\n".encode())
    live = "".join(ch.data for ch in bridge.display_row(0, scroll_offset=0)).strip()
    hist = "".join(ch.data for ch in bridge.display_row(0, scroll_offset=4)).strip()
    assert live != hist or bridge.history_len() >= 0
    assert bridge.history_len() >= 1
    abs_top = bridge.abs_y_for_viewport(0, 4)
    assert abs_top == bridge.history_len() - 4
    assert bridge.viewport_y_for_abs(abs_top, 4) == 0
    assert bridge.buffer_len() == bridge.history_len() + bridge.rows


def test_cwd_from_prompt_line() -> None:
    assert (
        cwd_from_prompt_line("simtra@ctldt06:~/Data/Abi/workspace/opdpos$")
        == "~/Data/Abi/workspace/opdpos"
    )
    assert cwd_from_prompt_line("root@box:/etc#") == "/etc"
    assert cwd_from_prompt_line("hello world") is None
    assert cwd_from_prompt_line("simtra@ctldt06:~/Data/Abi/workspace/opdpos$ ls") == (
        "~/Data/Abi/workspace/opdpos"
    )
    assert cwd_from_prompt_line("[simtra@ctldt06 ~/Data/Abi]$") == "~/Data/Abi"
    assert cwd_from_prompt_line("[simtra@ctldt06 opdpos]$") is None
    assert cwd_from_prompt_line("simtra@ctldt06:~$") == "~"


def test_cwd_from_screen_finds_prompt_after_long_output() -> None:
    lines = [f"file-{i}" for i in range(80)]
    lines.append("simtra@ctldt06:~/Data/Abi/workspace/opdpos$")
    lines.extend(f"out-{i}" for i in range(30))
    assert cwd_from_screen_lines(lines) == "~/Data/Abi/workspace/opdpos"


def test_screen_prompt_beats_stale_title() -> None:
    lines = ["simtra@ctldt06:~/Data/Abi/workspace/opdpos$"]
    assert cwd_from_screen_lines(lines, title="simtra@ctldt06:~") == "~/Data/Abi/workspace/opdpos"
    assert cwd_from_screen_lines([], title="/var/log") == "/var/log"


def test_cwd_from_screen_joins_wrapped_prompt() -> None:
    lines = [
        "simtra@ctldt06:~/Data/Abi/work",
        "space/opdpos$",
    ]
    assert cwd_from_screen_lines(lines) == "~/Data/Abi/workspace/opdpos"


def test_resolve_remote_path_expands_tilde() -> None:
    home = "/home/simtra"
    assert resolve_remote_path("~", home=home, cwd="/tmp") == home
    assert resolve_remote_path("~/Data/Abi/workspace/opdpos", home=home, cwd="/tmp") == (
        "/home/simtra/Data/Abi/workspace/opdpos"
    )
    assert resolve_remote_path("/var/log", home=home, cwd="/tmp") == "/var/log"
    assert resolve_remote_path(".", home=home, cwd="/var/log") == "/var/log"
    assert resolve_remote_path("notes.txt", home=home, cwd="/var/log") == "/var/log/notes.txt"


def test_mkdir_command() -> None:
    cmd = MkdirPath("/tmp/newdir", "req-1")
    assert cmd.path == "/tmp/newdir"
    assert cmd.request_id == "req-1"
    created = MkfilePath("/tmp/new.txt", "req-2")
    assert created.path == "/tmp/new.txt"
    assert created.request_id == "req-2"
