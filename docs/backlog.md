# Backlog

Product notes as they came in. Status is at the end of each item.

1. Transfers window: hide past by default, toggle to show past, pause/resume. **Done**
2. SSH drag-copy across large scrolled regions. **Done**
3. SFTP new folder button. **Done**
4. SFTP button opens the SSH session’s current directory (prompt cwd, including `~`). **Done**
5. Transfer ETA is the average of the last 100 samples; speed column stays a stable width. **Done**
6. SFTP right-click: create file as well as create folder. **Done**
7. Dragging / resizing SSH splits no longer blanks the terminal. **Done**
8. Idle timeout for SSH and SFTP: close the tab, no alert (45 min, Settings → Connections). **Done**
9. Tailing high-volume output (`docker logs`) must not keep every line in RAM — rolling window only. **Done** (default 4000 lines, pending PTY bytes clipped to the tail)
