"""Tab "Tagger": stats + trigger manual de procesamiento.

Dos modos:
- "Procesar ahora N items" (sync): para batches chicos (≤ 5). Llama a /api/tagger/run.
- "Encolar N items" (async): encola jobs en processing.jobs para que el worker
  los drene. Sirve para backfill grandes — usás el tab Worker para ver progreso.
"""

from __future__ import annotations

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..api_client import BackendClient
from ..worker_thread import BackgroundJob


class TaggerTab(QWidget):
    def __init__(self, api: BackendClient, pool: QThreadPool) -> None:
        super().__init__()
        self.api = api
        self.pool = pool

        layout = QVBoxLayout(self)

        # ---------------- Stats ----------------
        stats_box = QGroupBox("Estado del tagger")
        sf = QFormLayout(stats_box)
        self.total_label = QLabel("…")
        self.taggeados_label = QLabel("…")
        self.pendientes_label = QLabel("…")
        sf.addRow("Items WhatsApp total:", self.total_label)
        sf.addRow("Ya taggeados:", self.taggeados_label)
        sf.addRow("Pendientes (nivel=0):", self.pendientes_label)
        layout.addWidget(stats_box)

        # ---------------- Run ahora ----------------
        run_box = QGroupBox("Procesar ahora (sync — bloquea hasta terminar)")
        rh = QHBoxLayout(run_box)
        self.run_spin = QSpinBox()
        self.run_spin.setRange(1, 20)
        self.run_spin.setValue(3)
        self.run_spin.setPrefix("N=")
        self.run_btn = QPushButton("▶  Procesar ahora")
        rh.addWidget(QLabel("Items:"))
        rh.addWidget(self.run_spin)
        rh.addStretch()
        rh.addWidget(self.run_btn)
        layout.addWidget(run_box)

        hint_run = QLabel("Hace cada item con qwen3:8b en el momento (~3-5s c/u). Usar para validar calidad de a poco.")
        hint_run.setStyleSheet("color: #5f6368; font-style: italic; padding-left: 8px;")
        layout.addWidget(hint_run)

        # ---------------- Encolar batch ----------------
        enq_box = QGroupBox("Encolar lote (async — los procesa el worker continuo)")
        ef = QFormLayout(enq_box)
        self.enq_days_spin = QSpinBox()
        self.enq_days_spin.setRange(1, 90)
        self.enq_days_spin.setValue(2)
        self.enq_days_spin.setSuffix(" días")
        self.enq_limit_spin = QSpinBox()
        self.enq_limit_spin.setRange(0, 10_000)
        self.enq_limit_spin.setValue(0)
        self.enq_limit_spin.setSpecialValueText("sin límite")
        ef.addRow("Ventana:", self.enq_days_spin)
        ef.addRow("Tope (0 = todos):", self.enq_limit_spin)
        self.enq_btn = QPushButton("➕  Encolar items recientes sin taggear")
        ef.addRow(self.enq_btn)
        layout.addWidget(enq_box)

        hint_enq = QLabel("Encola SIN duplicar (si ya hay un tagger job pendiente para ese item, se saltea).")
        hint_enq.setStyleSheet("color: #5f6368; font-style: italic; padding-left: 8px;")
        layout.addWidget(hint_enq)

        # ---------------- Resultados / log ----------------
        out_box = QGroupBox("Salida")
        ol = QVBoxLayout(out_box)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFontFamily("Consolas")
        self.output.setMinimumHeight(140)
        ol.addWidget(self.output)
        layout.addWidget(out_box)

        # Conexiones
        self.run_btn.clicked.connect(self._on_run)
        self.enq_btn.clicked.connect(self._on_enqueue)

        self.refresh()

    # ------------------------------------------------------------
    # Refresh stats
    # ------------------------------------------------------------

    def refresh(self) -> None:
        job = BackgroundJob(self.api.tagger_stats)
        job.signals.finished.connect(self._render_stats)
        job.signals.failed.connect(lambda m: self._log(f"[stats] error: {m}"))
        self.pool.start(job)

    def _render_stats(self, d: dict) -> None:
        self.total_label.setText(str(d.get("total", "—")))
        self.taggeados_label.setText(str(d.get("taggeados", "—")))
        self.pendientes_label.setText(str(d.get("pendientes", "—")))

    # ------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------

    def _on_run(self) -> None:
        n = self.run_spin.value()
        self._log(f"▶ Procesando {n} items sincrónicamente…")
        self.run_btn.setEnabled(False)
        job = BackgroundJob(self.api.tagger_run, n, True)
        job.signals.finished.connect(self._on_run_done)
        job.signals.failed.connect(self._on_action_error)
        self.pool.start(job)

    def _on_run_done(self, r: dict) -> None:
        self.run_btn.setEnabled(True)
        self._log(
            f"   procesados={r.get('procesados')} taggeados={r.get('taggeados')} "
            f"triviales={r.get('saltados_triviales')} fallidos={r.get('fallidos')} "
            f"pendientes_restantes={r.get('pendientes_restantes')}"
        )
        det = r.get("detalle_creado") or {}
        if det:
            self._log(f"   creó: " + ", ".join(f"{k}={v}" for k, v in det.items() if v))
        if r.get("errores"):
            for e in r["errores"]:
                self._log(f"   error: {e}")
        self.refresh()

    def _on_enqueue(self) -> None:
        days = self.enq_days_spin.value()
        limit = self.enq_limit_spin.value() or None
        self._log(f"➕ Encolando jobs (días={days}, tope={limit or 'todos'})…")
        self.enq_btn.setEnabled(False)
        job = BackgroundJob(self.api.panel_enqueue_tagger, days, limit)
        job.signals.finished.connect(self._on_enqueue_done)
        job.signals.failed.connect(self._on_action_error)
        self.pool.start(job)

    def _on_enqueue_done(self, r: dict) -> None:
        self.enq_btn.setEnabled(True)
        self._log(
            f"   encolados={r.get('encolados')} ya_tenían_job={r.get('ya_encolados', 0)} "
            f"sin_contenido={r.get('sin_contenido', 0)} "
            f"pendientes_totales={r.get('pendientes_totales')}"
        )
        self.refresh()

    def _on_action_error(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.enq_btn.setEnabled(True)
        self._log(f"error: {msg}")
        QMessageBox.warning(self, "Tagger", msg)

    def _log(self, line: str) -> None:
        self.output.append(line)
