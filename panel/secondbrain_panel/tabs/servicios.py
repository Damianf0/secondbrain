"""Tab "Servicios": tabla con containers del stack + acciones por fila.

Cada fila muestra: servicio, estado/health, ports, y botones Restart/Stop/Start/Logs.
Las acciones de docker se ejecutan en threads — no congelan la UI.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..docker_client import DockerComposeClient, DockerError
from ..worker_thread import BackgroundJob

# Servicios "nuestros" en el orden en que querés verlos arriba.
_ORDEN = ["backend", "frontend", "ollama", "whisper", "qdrant", "minio", "postgres", "bridge"]


def _color_for_state(state: str, health: str) -> str:
    state = (state or "").lower()
    health = (health or "").lower()
    if state == "running" and health in ("healthy", ""):
        return "#1e8e3e"  # verde
    if state == "running" and health == "starting":
        return "#f9ab00"  # ámbar
    if state == "running":
        return "#f9ab00"
    if state in ("exited", "stopped", "dead"):
        return "#d93025"  # rojo
    return "#5f6368"


class ServiciosTab(QWidget):
    def __init__(self, docker: DockerComposeClient, pool: QThreadPool) -> None:
        super().__init__()
        self.docker = docker
        self.pool = pool

        layout = QVBoxLayout(self)

        # Top bar de stack global
        top = QHBoxLayout()
        self.up_btn = QPushButton("⏵  Levantar stack")
        self.down_btn = QPushButton("⏹  Bajar stack")
        self.refresh_btn = QPushButton("↻  Refrescar")
        for b in (self.up_btn, self.down_btn, self.refresh_btn):
            top.addWidget(b)
        top.addStretch()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #5f6368;")
        top.addWidget(self.status_label)
        layout.addLayout(top)

        # Tabla
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Servicio", "Estado", "Puertos", "Acciones"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        # Conexiones
        self.up_btn.clicked.connect(self._on_up)
        self.down_btn.clicked.connect(self._on_down)
        self.refresh_btn.clicked.connect(self.refresh)

        self.refresh()

    # ------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------

    def refresh(self) -> None:
        if not self.docker.available:
            self.status_label.setText("docker-compose.yml no encontrado")
            return
        self.status_label.setText("Cargando…")
        job = BackgroundJob(self.docker.ps)
        job.signals.finished.connect(self._render)
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _render(self, rows: list[dict]) -> None:
        self.status_label.setText(f"{len(rows)} containers")
        # Ordenar por _ORDEN, luego alfabético
        order_idx = {n: i for i, n in enumerate(_ORDEN)}
        rows = sorted(rows, key=lambda r: (order_idx.get(r.get("Service", ""), 999), r.get("Service", "")))

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._render_row(i, row)

    def _render_row(self, i: int, row: dict) -> None:
        service = row.get("Service") or row.get("Name") or "?"
        state = row.get("State") or row.get("Status") or "?"
        health = row.get("Health") or ""
        publishers = row.get("Publishers") or []
        ports = self._fmt_ports(publishers)

        # Servicio
        self.table.setItem(i, 0, QTableWidgetItem(service))

        # Estado (con color)
        estado_txt = state if not health else f"{state} ({health})"
        est_item = QTableWidgetItem(estado_txt)
        color = _color_for_state(state, health)
        est_item.setForeground(Qt.GlobalColor.white)
        est_item.setBackground(Qt.GlobalColor.transparent)
        est_item.setData(Qt.ItemDataRole.UserRole, color)
        self.table.setItem(i, 1, est_item)
        # estilo via cell widget para que se vea el pill
        pill = QLabel(f" ●  {estado_txt} ")
        pill.setStyleSheet(f"color: {color}; padding: 2px 6px; font-weight: 600;")
        self.table.setCellWidget(i, 1, pill)

        # Puertos
        self.table.setItem(i, 2, QTableWidgetItem(ports))

        # Acciones
        acciones = QWidget()
        h = QHBoxLayout(acciones)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(4)
        restart_btn = QPushButton("Restart")
        stop_btn = QPushButton("Stop")
        start_btn = QPushButton("Start")
        logs_btn = QPushButton("Logs")
        for b in (restart_btn, stop_btn, start_btn, logs_btn):
            b.setMaximumHeight(24)
            h.addWidget(b)
        restart_btn.clicked.connect(lambda _=False, s=service: self._action("restart", s))
        stop_btn.clicked.connect(lambda _=False, s=service: self._action("stop", s))
        start_btn.clicked.connect(lambda _=False, s=service: self._action("start", s))
        logs_btn.clicked.connect(lambda _=False, s=service: self._show_logs(s))
        self.table.setCellWidget(i, 3, acciones)

    def _fmt_ports(self, publishers: list[dict]) -> str:
        parts = []
        seen = set()
        for p in publishers or []:
            published = p.get("PublishedPort")
            target = p.get("TargetPort")
            if not published:
                continue
            key = (published, target)
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"{published}→{target}" if target else str(published))
        return ", ".join(parts)

    # ------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------

    def _action(self, action: str, service: str) -> None:
        self.status_label.setText(f"docker compose {action} {service}…")
        fn_map = {"restart": self.docker.restart, "stop": self.docker.stop, "start": self.docker.start}
        job = BackgroundJob(fn_map[action], service)
        job.signals.finished.connect(lambda _r: (self.status_label.setText(f"{action} {service}: OK"), self.refresh()))
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _on_up(self) -> None:
        self.status_label.setText("docker compose up -d…")
        job = BackgroundJob(self.docker.up)
        job.signals.finished.connect(lambda _r: (self.status_label.setText("stack arriba"), self.refresh()))
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _on_down(self) -> None:
        confirm = QMessageBox.question(
            self, "Bajar stack",
            "¿Detener TODO el stack? Bridge se va a desconectar de WhatsApp (la sesión persiste, no pide QR de nuevo).",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText("docker compose down…")
        job = BackgroundJob(self.docker.down)
        job.signals.finished.connect(lambda _r: (self.status_label.setText("stack abajo"), self.refresh()))
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _show_logs(self, service: str) -> None:
        self.status_label.setText(f"docker compose logs {service}…")
        job = BackgroundJob(self.docker.logs, service, 300)
        job.signals.finished.connect(lambda r, s=service: self._render_logs(s, r))
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _render_logs(self, service: str, result: tuple[int, str, str]) -> None:
        rc, out, err = result
        text = out if rc == 0 else (err or out or f"rc={rc}")
        dlg = QMessageBox(self)
        dlg.setWindowTitle(f"Logs — {service}")
        # Usamos QTextEdit anidado para que sea scrolleable y monoespaciado.
        te = QTextEdit()
        te.setReadOnly(True)
        te.setFontFamily("Consolas")
        te.setPlainText(text[-30_000:])
        # Hack: meter el QTextEdit en el layout del QMessageBox.
        dlg.layout().addWidget(te, 1, 0, 1, dlg.layout().columnCount())
        dlg.setStandardButtons(QMessageBox.StandardButton.Close)
        dlg.resize(900, 600)
        dlg.exec()
        self.status_label.setText("listo")

    def _on_error(self, msg: str) -> None:
        self.status_label.setText(f"error: {msg[:120]}")
        QMessageBox.warning(self, "Docker", msg)
