"""Tab "Chats": lista de conversaciones con acciones por chat.

Permite seleccionar una conversación y encolar trabajo (re-tag / re-embed / solo
pendientes) para ese chat puntual.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api_client import BackendClient
from ..worker_thread import BackgroundJob


_TIPO_OPCIONES = [
    ("tagger", "Tagger (extraer hechos/promesas/etc.)"),
    ("embed", "Embed (re-vectorizar)"),
    ("transcribe", "Transcribe (audios)"),
    ("extract", "Extract (documentos)"),
    ("caption", "Caption (imágenes)"),
]


class ChatsTab(QWidget):
    def __init__(self, api: BackendClient, pool: QThreadPool) -> None:
        super().__init__()
        self.api = api
        self.pool = pool
        self._all_rows: list[dict] = []

        root = QVBoxLayout(self)

        # ----- Filtros -----
        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Buscar:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("filtrar por nombre del chat…")
        self.search_input.textChanged.connect(self._apply_filter)
        filt_row.addWidget(self.search_input, 1)
        self.seguidas_chk = QCheckBox("solo seguidas")
        self.seguidas_chk.toggled.connect(self.refresh)
        filt_row.addWidget(self.seguidas_chk)
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setMaximumWidth(36)
        self.refresh_btn.clicked.connect(self.refresh)
        filt_row.addWidget(self.refresh_btn)
        root.addLayout(filt_row)

        # ----- Tabla -----
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Chat", "Tipo", "Items", "Embebidos", "Taggeados", "Última actividad"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 6):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        # ----- Acciones sobre el chat seleccionado -----
        actions_box = QGroupBox("Procesar chat seleccionado")
        ah = QVBoxLayout(actions_box)

        ah_row1 = QHBoxLayout()
        ah_row1.addWidget(QLabel("Etapa:"))
        self.tipo_combo = QComboBox()
        for key, label in _TIPO_OPCIONES:
            self.tipo_combo.addItem(label, key)
        ah_row1.addWidget(self.tipo_combo, 1)
        self.solo_pendientes_chk = QCheckBox("Solo pendientes")
        self.solo_pendientes_chk.setChecked(True)
        self.solo_pendientes_chk.setToolTip("Si está marcado, no re-procesa items que ya tienen la marca correspondiente.")
        ah_row1.addWidget(self.solo_pendientes_chk)
        ah_row1.addWidget(QLabel("Tope:"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 10_000)
        self.limit_spin.setValue(0)
        self.limit_spin.setSpecialValueText("sin tope")
        ah_row1.addWidget(self.limit_spin)
        ah.addLayout(ah_row1)

        ah_row2 = QHBoxLayout()
        self.enqueue_btn = QPushButton("➕ Encolar para este chat")
        self.enqueue_btn.setEnabled(False)
        self.enqueue_btn.clicked.connect(self._on_enqueue)
        ah_row2.addWidget(self.enqueue_btn)
        ah_row2.addStretch()
        self.feedback_label = QLabel("")
        self.feedback_label.setStyleSheet("color: #5f6368;")
        ah_row2.addWidget(self.feedback_label)
        ah.addLayout(ah_row2)

        root.addWidget(actions_box)

        self.table.itemSelectionChanged.connect(
            lambda: self.enqueue_btn.setEnabled(self.table.currentRow() >= 0)
        )

        self.refresh()

    # ------------------------------------------------------------

    def refresh(self) -> None:
        seguidas = self.seguidas_chk.isChecked()
        job = BackgroundJob(self.api.panel_conversations, seguidas, 300)
        job.signals.finished.connect(self._render)
        job.signals.failed.connect(lambda m: self.feedback_label.setText(f"error: {m[:200]}"))
        self.pool.start(job)

    def _render(self, d: dict) -> None:
        self._all_rows = d.get("conversaciones") or []
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self.search_input.text().strip().lower()
        rows = [r for r in self._all_rows if not needle or needle in (r.get("nombre") or "").lower()]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            nombre = r.get("nombre", "?")
            cell_nombre = QTableWidgetItem(("⭐ " if r.get("seguir") else "   ") + nombre)
            cell_nombre.setData(Qt.ItemDataRole.UserRole, r.get("conversation_id"))
            self.table.setItem(i, 0, cell_nombre)
            self.table.setItem(i, 1, QTableWidgetItem(r.get("tipo", "")))
            self.table.setItem(i, 2, QTableWidgetItem(str(r.get("items", 0))))
            self.table.setItem(i, 3, QTableWidgetItem(str(r.get("embebidos", 0))))
            self.table.setItem(i, 4, QTableWidgetItem(str(r.get("taggeados", 0))))
            ult = r.get("ultima_actividad") or ""
            self.table.setItem(i, 5, QTableWidgetItem(ult[:19].replace("T", " ")))

    def _selected_conv_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        cell = self.table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def _selected_conv_name(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return "?"
        cell = self.table.item(row, 0)
        return (cell.text() if cell else "?").strip("⭐ ").strip()

    # ------------------------------------------------------------

    def _on_enqueue(self) -> None:
        cid = self._selected_conv_id()
        if not cid:
            return
        tipo = self.tipo_combo.currentData()
        solo_pend = self.solo_pendientes_chk.isChecked()
        limit = self.limit_spin.value() or None
        nombre = self._selected_conv_name()

        confirm = QMessageBox.question(
            self, "Confirmar",
            f"Encolar etapa '{tipo}' para '{nombre}'\n"
            f"{'(solo pendientes)' if solo_pend else '(RE-PROCESAR todos — sobrescribe lo existente)'}\n"
            f"Tope: {limit or 'sin tope'}",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.enqueue_btn.setEnabled(False)
        self.feedback_label.setText("encolando…")
        job = BackgroundJob(
            self.api.panel_conversation_enqueue,
            cid, [tipo], solo_pendientes=solo_pend, limit=limit,
        )
        job.signals.finished.connect(self._on_enqueue_done)
        job.signals.failed.connect(self._on_enqueue_error)
        self.pool.start(job)

    def _on_enqueue_done(self, r: dict) -> None:
        self.enqueue_btn.setEnabled(True)
        encolados = r.get("encolados") or {}
        total = r.get("total", 0)
        partes = ", ".join(f"{k}={v}" for k, v in encolados.items())
        self.feedback_label.setText(f"encolados: {total}  ({partes})")
        self.refresh()

    def _on_enqueue_error(self, msg: str) -> None:
        self.enqueue_btn.setEnabled(True)
        self.feedback_label.setText(f"error: {msg[:200]}")
        QMessageBox.warning(self, "Encolar", msg)
