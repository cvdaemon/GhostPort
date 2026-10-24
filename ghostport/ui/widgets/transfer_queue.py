from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ghostport.sftp.transfer_manager import (
    TransferDirection,
    TransferJob,
    TransferManager,
    TransferStatus,
    format_bytes,
    format_eta,
    format_speed,
)

_ACTIVE = (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.PAUSED)


class TransferQueueView(QWidget):
    is_utility_tab = True

    def __init__(self, manager: TransferManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._rows: dict[str, int] = {}
        self._show_past = False

        heading = QLabel("Transfers")
        heading.setStyleSheet("font-weight: 600; color: #9d9d9d;")
        self._hint = QLabel("Speed, size, and ETA update while files copy.")
        self._hint.setStyleSheet("color: #6b6b6b; font-size: 11px;")
        self.past_btn = QPushButton("Show past")
        self.past_btn.setCheckable(True)
        self.past_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.past_btn.setToolTip("Show finished, failed, and cancelled transfers")
        self.past_btn.toggled.connect(self._on_show_past)

        head = QHBoxLayout()
        head.addWidget(heading)
        head.addWidget(self._hint, 1)
        head.addWidget(self.past_btn)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["File", "Direction", "Progress", "Size", "Speed", "ETA", ""]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Interactive, not ResizeToContents: speed text used to change width every tick
        # (512 KB/s vs 1.2 MB/s) and the columns jittered. Defaults fit the fixed formats.
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 120)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(head)
        layout.addWidget(self.table, 1)

        manager.job_updated.connect(self._on_job)
        self._rebuild()

    def _visible(self, job: TransferJob) -> bool:
        return self._show_past or job.status in _ACTIVE

    def _on_show_past(self, on: bool) -> None:
        self._show_past = on
        self.past_btn.setText("Hide past" if on else "Show past")
        self._rebuild()

    def _rebuild(self) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        for job in self._manager.jobs():
            if self._visible(job):
                self._ensure_row(job)
                self._fill(self._rows[job.id], job)

    def _on_job(self, job: object) -> None:
        if not isinstance(job, TransferJob):
            return
        if not self._visible(job):
            row = self._rows.pop(job.id, None)
            if row is not None:
                self.table.removeRow(row)
                self._reindex()
            return
        self._ensure_row(job)
        self._fill(self._rows[job.id], job)

    def _reindex(self) -> None:
        updated: dict[str, int] = {}
        for job_id, row in self._rows.items():
            if row > self.table.rowCount() - 1:
                continue
            updated[job_id] = row
        # After removeRow, later rows shift down. Rebuild from table order is safer.
        rebuilt: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            jid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(jid, str):
                rebuilt[jid] = row
        self._rows = rebuilt

    def _ensure_row(self, job: TransferJob) -> None:
        if job.id in self._rows:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[job.id] = row
        name = QTableWidgetItem("")
        name.setData(Qt.ItemDataRole.UserRole, job.id)
        self.table.setItem(row, 0, name)
        self.table.setItem(row, 1, QTableWidgetItem(""))
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(True)
        self.table.setCellWidget(row, 2, bar)
        self.table.setItem(row, 3, QTableWidgetItem(""))
        self.table.setItem(row, 4, QTableWidgetItem(""))
        self.table.setItem(row, 5, QTableWidgetItem(""))
        wrap = QWidget()
        box = QHBoxLayout(wrap)
        box.setContentsMargins(4, 0, 4, 0)
        box.setSpacing(4)
        pause = QPushButton("Pause")
        pause.setObjectName("pauseBtn")
        pause.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        resume = QPushButton("Resume")
        resume.setObjectName("resumeBtn")
        resume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelBtn")
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        pause.clicked.connect(lambda _=False, jid=job.id: self._manager.pause(jid))
        resume.clicked.connect(lambda _=False, jid=job.id: self._manager.resume(jid))
        cancel.clicked.connect(lambda _=False, jid=job.id: self._manager.cancel(jid))
        box.addWidget(pause)
        box.addWidget(resume)
        box.addWidget(cancel)
        self.table.setCellWidget(row, 6, wrap)

    def _fill(self, row: int, job: TransferJob) -> None:
        name = self.table.item(row, 0)
        if name is not None:
            name.setText(job.display_name or job.local_path)
            name.setData(Qt.ItemDataRole.UserRole, job.id)
        direction = self.table.item(row, 1)
        if direction is not None:
            direction.setText("↓ Download" if job.direction is TransferDirection.DOWNLOAD else "↑ Upload")
        bar = self.table.cellWidget(row, 2)
        if isinstance(bar, QProgressBar):
            if job.nbytes_total and job.nbytes_total > 0:
                bar.setRange(0, 100)
                bar.setValue(min(100, int(100 * job.nbytes_done / job.nbytes_total)))
                bar.setFormat("%p%")
            elif job.status is TransferStatus.RUNNING:
                bar.setRange(0, 0)
                bar.setFormat("Working…")
            elif job.status is TransferStatus.COMPLETED:
                bar.setRange(0, 100)
                bar.setValue(100)
                bar.setFormat("Done")
            elif job.status is TransferStatus.PAUSED:
                bar.setRange(0, 100)
                if job.nbytes_total and job.nbytes_total > 0:
                    bar.setValue(min(100, int(100 * job.nbytes_done / job.nbytes_total)))
                bar.setFormat("Paused")
            else:
                bar.setRange(0, 100)
                bar.setValue(0)
                bar.setFormat(job.status.value)
        size = self.table.item(row, 3)
        if size is not None:
            if job.nbytes_total:
                size.setText(f"{format_bytes(job.nbytes_done)} / {format_bytes(job.nbytes_total)}")
            else:
                size.setText(format_bytes(job.nbytes_done))
        speed = self.table.item(row, 4)
        if speed is not None:
            speed.setText(format_speed(job.speed_bps) if job.status is TransferStatus.RUNNING else "")
        eta = self.table.item(row, 5)
        if eta is not None:
            if job.status is TransferStatus.RUNNING:
                eta.setText(format_eta(job.eta_seconds()))
            elif job.status is TransferStatus.COMPLETED:
                eta.setText("Done")
            elif job.status is TransferStatus.FAILED:
                eta.setText(job.error or "Failed")
            elif job.status is TransferStatus.CANCELLED:
                eta.setText("Cancelled")
            elif job.status is TransferStatus.PAUSED:
                eta.setText("Paused")
            else:
                eta.setText("Queued")
        wrap = self.table.cellWidget(row, 6)
        if wrap is None:
            return
        pause = wrap.findChild(QPushButton, "pauseBtn")
        resume = wrap.findChild(QPushButton, "resumeBtn")
        cancel = wrap.findChild(QPushButton, "cancelBtn")
        if isinstance(pause, QPushButton):
            pause.setVisible(job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING))
        if isinstance(resume, QPushButton):
            resume.setVisible(job.status is TransferStatus.PAUSED)
        if isinstance(cancel, QPushButton):
            cancel.setVisible(job.status in _ACTIVE)
