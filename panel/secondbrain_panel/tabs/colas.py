"""Tab "Colas": tabla con contadores por tipo y estado."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api_client import BackendClient
from ..worker_thread import BackgroundJob

_TIPOS = ["transcribe", "extract", "caption", "embed", "tagger"]
_ESTADOS = ["pendiente", "en_proceso", "completado", "fallido"]


class ColasTab(QWidget):
    def __init__(self, api: BackendClient, pool: QThreadPool) -> None:
        super().__init__()
        self.api = api
        self.pool = pool

        layout = QVBoxLayout(self)

        title = QLabel("Estado de las colas de processing.jobs")
        title.setStyleSheet("font-size: 14px; font-weight: 600; padding: 4px 0;")
        layout.addWidget(title)

        self.table = QTableWidget(len(_TIPOS), len(_ESTADOS) + 1)
        self.table.setHorizontalHeaderLabels(["Cola", *_ESTADOS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for i in range(1, len(_ESTADOS) + 1):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        for i, tipo in enumerate(_TIPOS):
            self.table.setItem(i, 0, QTableWidgetItem(tipo))
            for j in range(len(_ESTADOS)):
                self.table.setItem(i, j + 1, QTableWidgetItem("—"))

        self.foot_label = QLabel("")
        self.foot_label.setStyleSheet("color: #5f6368; padding-top: 6px;")
        layout.addWidget(self.foot_label)
        layout.addStretch()

        self.refresh()

    def refresh(self) -> None:
        job = BackgroundJob(self.api.panel_queue_counts)
        job.signals.finished.connect(self._render)
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _render(self, d: dict) -> None:
        counts = d.get("counts") or {}  # {tipo: {estado: n}}
        total_pend = 0
        for i, tipo in enumerate(_TIPOS):
            row = counts.get(tipo, {})
            for j, estado in enumerate(_ESTADOS):
                v = row.get(estado, 0)
                cell = self.table.item(i, j + 1)
                cell.setText(str(v))
                if estado == "pendiente":
                    total_pend += v
                    if v > 0:
                        cell.setForeground(Qt.GlobalColor.yellow)
                elif estado == "fallido" and v > 0:
                    cell.setForeground(Qt.GlobalColor.red)
                elif estado == "en_proceso" and v > 0:
                    cell.setForeground(Qt.GlobalColor.cyan)
        ts = d.get("at", "")
        self.foot_label.setText(f"Total pendientes: {total_pend}  ·  Actualizado: {ts}")

    def _on_error(self, msg: str) -> None:
        self.foot_label.setText(f"error: {msg[:200]}")
