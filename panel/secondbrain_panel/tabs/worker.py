"""Tab "Worker": status del worker continuo + controles."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..api_client import BackendClient, BackendError
from ..worker_thread import BackgroundJob


def _human_dur_ms(ms: int | None) -> str:
    if not ms:
        return "—"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m {int(s % 60)}s"


class WorkerTab(QWidget):
    def __init__(self, api: BackendClient, pool: QThreadPool) -> None:
        super().__init__()
        self.api = api
        self.pool = pool

        layout = QVBoxLayout(self)

        # Header: estado + controles
        head = QHBoxLayout()
        self.state_label = QLabel("…")
        self.state_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        head.addWidget(self.state_label)
        head.addStretch()
        self.pause_btn = QPushButton("⏸  Pausar")
        self.resume_btn = QPushButton("▶  Reanudar")
        self.tick_btn = QPushButton("⏭  Tick ahora")
        for b in (self.pause_btn, self.resume_btn, self.tick_btn):
            head.addWidget(b)
        layout.addLayout(head)

        # Group: último tick
        last_box = QGroupBox("Último tick")
        last_form = QFormLayout(last_box)
        self.last_at_label = QLabel("—")
        self.last_dur_label = QLabel("—")
        self.next_label = QLabel("—")
        last_form.addRow("Cuándo:", self.last_at_label)
        last_form.addRow("Duración:", self.last_dur_label)
        last_form.addRow("Etapas:", self._etapas_label())
        layout.addWidget(last_box)

        # Group: acumulado de la sesión
        ac_box = QGroupBox("Procesados desde el último restart")
        ac_form = QFormLayout(ac_box)
        self.ac_transcribe = QLabel("0")
        self.ac_extract = QLabel("0")
        self.ac_caption = QLabel("0")
        self.ac_embed = QLabel("0")
        self.ac_tagger = QLabel("0")
        self.ac_err = QLabel("0")
        ac_form.addRow("transcribe:", self.ac_transcribe)
        ac_form.addRow("extract:", self.ac_extract)
        ac_form.addRow("caption:", self.ac_caption)
        ac_form.addRow("embed:", self.ac_embed)
        ac_form.addRow("tagger:", self.ac_tagger)
        ac_form.addRow("errores:", self.ac_err)
        layout.addWidget(ac_box)

        # Group: batches actuales (info)
        b_box = QGroupBox("Configuración runtime")
        b_form = QFormLayout(b_box)
        self.batch_label = QLabel("…")
        self.caption_window_label = QLabel("…")
        self.interval_label = QLabel("…")
        b_form.addRow("Batches:", self.batch_label)
        b_form.addRow("Ventana caption:", self.caption_window_label)
        b_form.addRow("Intervalo:", self.interval_label)
        layout.addWidget(b_box)

        layout.addStretch()

        self.pause_btn.clicked.connect(self._on_pause)
        self.resume_btn.clicked.connect(self._on_resume)
        self.tick_btn.clicked.connect(self._on_tick)

        self.refresh()

    # ------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------

    def _etapas_label(self) -> QLabel:
        self.etapas_label = QLabel("—")
        self.etapas_label.setWordWrap(True)
        self.etapas_label.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        return self.etapas_label

    def refresh(self) -> None:
        job = BackgroundJob(self.api.worker_status)
        job.signals.finished.connect(self._render)
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _render(self, d: dict) -> None:
        running = d.get("running")
        paused = d.get("paused")
        state_txt, color = ("PAUSADO", "#f9ab00") if paused else (("CORRIENDO", "#1e8e3e") if running else ("DETENIDO", "#d93025"))
        self.state_label.setText(state_txt)
        self.state_label.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {color};")
        self.pause_btn.setEnabled(running and not paused)
        self.resume_btn.setEnabled(running and paused)

        # último tick
        self.last_at_label.setText((d.get("last_tick_at") or "—")[:19].replace("T", " "))
        self.last_dur_label.setText(_human_dur_ms(d.get("last_tick_duration_ms")))
        ur = d.get("ultimo_resultado") or {}
        etapas = ur.get("etapas") or {}
        if etapas:
            lines = []
            for nombre in ("transcribe", "extract", "caption", "embed", "tagger"):
                info = etapas.get(nombre, {})
                if "saltado" in info:
                    lines.append(f"{nombre:10} saltado ({info['saltado']})")
                elif "error" in info:
                    lines.append(f"{nombre:10} ERROR — {str(info['error'])[:60]}")
                else:
                    proc = info.get("procesados", 0)
                    pend = info.get("pendientes_restantes", 0)
                    lines.append(f"{nombre:10} proc={proc} pend_restantes={pend}")
            self.etapas_label.setText("\n".join(lines))
        else:
            self.etapas_label.setText("—")

        # acumulado
        ac = d.get("acumulado") or {}
        self.ac_transcribe.setText(str(ac.get("transcribe_procesados", 0)))
        self.ac_extract.setText(str(ac.get("extract_procesados", 0)))
        self.ac_caption.setText(str(ac.get("caption_procesados", 0)))
        self.ac_embed.setText(str(ac.get("embed_procesados", 0)))
        self.ac_tagger.setText(str(ac.get("tagger_procesados", 0)))
        self.ac_err.setText(str(ac.get("errores", 0)))

        # config
        batches = d.get("batch") or {}
        self.batch_label.setText("  ".join(f"{k}={v}" for k, v in batches.items()))
        cw = d.get("caption_window") or {}
        en_v = cw.get("en_ventana_ahora")
        self.caption_window_label.setText(
            f"{cw.get('hour_start', '?')}:00 — {cw.get('hour_end', '?')}:00 ({cw.get('tz','')})  → {'ACTIVA' if en_v else 'inactiva'}"
        )
        self.interval_label.setText(f"{d.get('interval_s', '?')}s")

    # ------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------

    def _on_pause(self) -> None:
        job = BackgroundJob(self.api.worker_pause)
        job.signals.finished.connect(lambda _r: self.refresh())
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _on_resume(self) -> None:
        job = BackgroundJob(self.api.worker_resume)
        job.signals.finished.connect(lambda _r: self.refresh())
        job.signals.failed.connect(self._on_error)
        self.pool.start(job)

    def _on_tick(self) -> None:
        self.tick_btn.setEnabled(False)
        job = BackgroundJob(self.api.worker_tick_now)
        job.signals.finished.connect(lambda _r: (self.tick_btn.setEnabled(True), self.refresh()))
        job.signals.failed.connect(lambda m: (self.tick_btn.setEnabled(True), self._on_error(m)))
        self.pool.start(job)

    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Worker", msg)
