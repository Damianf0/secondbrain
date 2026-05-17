"""Tab "Configuración": ajustes de calidad y throughput del worker/tagger en runtime.

Lo que se cambia acá NO se persiste a `.env` — se aplica en memoria y se pierde
al reiniciar el backend. Pensado para experimentar sin tocar archivos.

Para que un cambio quede permanente, hay que reflejarlo en `.env` y reiniciar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..api_client import BackendClient
from ..worker_thread import BackgroundJob


class ConfiguracionTab(QWidget):
    def __init__(self, api: BackendClient, pool: QThreadPool) -> None:
        super().__init__()
        self.api = api
        self.pool = pool

        root = QVBoxLayout(self)

        warn = QLabel(
            "⚠️ Estos cambios viven solo en memoria. Para persistirlos, replicalos en .env y reiniciá."
        )
        warn.setStyleSheet("background: #fef7e0; color: #5f6368; padding: 6px; border-radius: 4px;")
        warn.setWordWrap(True)
        root.addWidget(warn)

        # =================== Worker ===================
        worker_box = QGroupBox("Worker continuo — intervalo y batches por etapa")
        wf = QFormLayout(worker_box)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 600)
        self.interval_spin.setSuffix(" s")
        wf.addRow("Intervalo entre ticks:", self.interval_spin)

        self.b_transcribe = self._batch_spin()
        self.b_extract = self._batch_spin()
        self.b_caption = self._batch_spin()
        self.b_embed = self._batch_spin(maxv=500)
        self.b_tagger = self._batch_spin()
        wf.addRow("Batch transcribe:", self.b_transcribe)
        wf.addRow("Batch extract:", self.b_extract)
        wf.addRow("Batch caption:", self.b_caption)
        wf.addRow("Batch embed:", self.b_embed)
        wf.addRow("Batch tagger:", self.b_tagger)

        cap_row = QHBoxLayout()
        self.cap_start = QSpinBox()
        self.cap_start.setRange(0, 23)
        self.cap_start.setSuffix(":00")
        self.cap_end = QSpinBox()
        self.cap_end.setRange(0, 23)
        self.cap_end.setSuffix(":00")
        cap_row.addWidget(QLabel("De"))
        cap_row.addWidget(self.cap_start)
        cap_row.addWidget(QLabel("a"))
        cap_row.addWidget(self.cap_end)
        cap_row.addWidget(QLabel("(start==end → caption deshabilitado)"))
        cap_row.addStretch()
        wf.addRow("Ventana caption:", cap_row)

        self.save_worker_btn = QPushButton("Guardar config del worker")
        self.save_worker_btn.clicked.connect(self._on_save_worker)
        wf.addRow(self.save_worker_btn)

        root.addWidget(worker_box)

        # =================== Tagger ===================
        tagger_box = QGroupBox("Tagger — calidad de la extracción")
        tf = QFormLayout(tagger_box)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        tf.addRow("Modelo LLM:", self.model_combo)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        tf.addRow("Temperature:", self.temp_spin)

        hint = QLabel(
            "Modelo más grande (12B+) = mejor calidad pero más lento. "
            "Temperature baja (0.0-0.2) = determinista, ideal para extracción JSON."
        )
        hint.setStyleSheet("color: #5f6368; font-style: italic;")
        hint.setWordWrap(True)
        tf.addRow(hint)

        self.save_tagger_btn = QPushButton("Guardar config del tagger")
        self.save_tagger_btn.clicked.connect(self._on_save_tagger)
        tf.addRow(self.save_tagger_btn)

        root.addWidget(tagger_box)

        # =================== Feedback ===================
        self.feedback_label = QLabel("")
        self.feedback_label.setStyleSheet("color: #5f6368;")
        root.addWidget(self.feedback_label)

        root.addStretch()

        self.refresh()

    def _batch_spin(self, *, maxv: int = 100) -> QSpinBox:
        s = QSpinBox()
        s.setRange(1, maxv)
        return s

    # ------------------------------------------------------------

    def refresh(self) -> None:
        job = BackgroundJob(self.api.panel_config)
        job.signals.finished.connect(self._render_config)
        job.signals.failed.connect(lambda m: self.feedback_label.setText(f"error: {m[:200]}"))
        self.pool.start(job)

        job2 = BackgroundJob(self.api.panel_ollama_models)
        job2.signals.finished.connect(self._render_models)
        job2.signals.failed.connect(lambda m: self.feedback_label.setText(f"models error: {m[:200]}"))
        self.pool.start(job2)

        # Caption window viene también de worker.status (no la expone config en el GET aún
        # — la guardamos en memoria desde la última lectura, pero ponemos defaults razonables)
        job3 = BackgroundJob(self.api.worker_status)
        job3.signals.finished.connect(self._render_caption_window)
        self.pool.start(job3)

    def _render_config(self, d: dict) -> None:
        w = d.get("worker") or {}
        if "interval_s" in w:
            self.interval_spin.setValue(int(w["interval_s"]))
        batch = w.get("batch") or {}
        if "transcribe" in batch:
            self.b_transcribe.setValue(int(batch["transcribe"]))
        if "extract" in batch:
            self.b_extract.setValue(int(batch["extract"]))
        if "caption" in batch:
            self.b_caption.setValue(int(batch["caption"]))
        if "embed" in batch:
            self.b_embed.setValue(int(batch["embed"]))
        if "tagger" in batch:
            self.b_tagger.setValue(int(batch["tagger"]))

        t = d.get("tagger") or {}
        if "model" in t and t["model"]:
            self._set_combo_current(self.model_combo, str(t["model"]))
        if "temperature" in t and t["temperature"] is not None:
            self.temp_spin.setValue(float(t["temperature"]))

    def _render_models(self, d: dict) -> None:
        models = d.get("models") or []
        current = self.model_combo.currentText()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for m in models:
            name = m.get("name") or ""
            label = f"{name}  ({m.get('size_gb', '?')} GB, {m.get('params', '?')})"
            self.model_combo.addItem(label, name)
        # restore current
        if current:
            self._set_combo_current(self.model_combo, current)
        self.model_combo.blockSignals(False)

    def _set_combo_current(self, combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value or combo.itemText(i).startswith(value):
                combo.setCurrentIndex(i)
                return
        combo.setEditText(value)

    def _render_caption_window(self, d: dict) -> None:
        cw = d.get("caption_window") or {}
        if "hour_start" in cw:
            self.cap_start.setValue(int(cw["hour_start"]))
        if "hour_end" in cw:
            self.cap_end.setValue(int(cw["hour_end"]))

    # ------------------------------------------------------------

    def _on_save_worker(self) -> None:
        batch = {
            "transcribe": self.b_transcribe.value(),
            "extract": self.b_extract.value(),
            "caption": self.b_caption.value(),
            "embed": self.b_embed.value(),
            "tagger": self.b_tagger.value(),
        }
        kwargs = {
            "interval_s": self.interval_spin.value(),
            "batch": batch,
            "caption_hour_start": self.cap_start.value(),
            "caption_hour_end": self.cap_end.value(),
        }
        self.save_worker_btn.setEnabled(False)
        job = BackgroundJob(self.api.panel_update_worker_config, **kwargs)
        job.signals.finished.connect(self._on_save_done)
        job.signals.failed.connect(self._on_save_error)
        self.pool.start(job)

    def _on_save_tagger(self) -> None:
        model = self.model_combo.currentData() or self.model_combo.currentText().split(" ")[0]
        temp = self.temp_spin.value()
        self.save_tagger_btn.setEnabled(False)
        job = BackgroundJob(self.api.panel_update_tagger_config, model=model, temperature=temp)
        job.signals.finished.connect(self._on_save_done)
        job.signals.failed.connect(self._on_save_error)
        self.pool.start(job)

    def _on_save_done(self, r: dict) -> None:
        self.save_worker_btn.setEnabled(True)
        self.save_tagger_btn.setEnabled(True)
        changes = r.get("changes") or {}
        self.feedback_label.setText(f"✓ guardado en runtime: {changes}")
        self.refresh()

    def _on_save_error(self, msg: str) -> None:
        self.save_worker_btn.setEnabled(True)
        self.save_tagger_btn.setEnabled(True)
        self.feedback_label.setText(f"error: {msg[:200]}")
        QMessageBox.warning(self, "Configuración", msg)
