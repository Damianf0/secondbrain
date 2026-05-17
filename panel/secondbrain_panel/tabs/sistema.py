"""Tab "Sistema": CPU, RAM, GPU, modelos cargados en VRAM."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config, system_stats
from ..worker_thread import BackgroundJob


def _bar(value: float | None, label: str, *, danger: float = 90, warn: float = 75) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    v = int(value or 0)
    bar.setValue(v)
    bar.setFormat(f"{label}: {v}%")
    if v >= danger:
        bar.setStyleSheet("QProgressBar::chunk { background: #d93025; }")
    elif v >= warn:
        bar.setStyleSheet("QProgressBar::chunk { background: #f9ab00; }")
    else:
        bar.setStyleSheet("QProgressBar::chunk { background: #1e8e3e; }")
    return bar


class SistemaTab(QWidget):
    def __init__(self, pool: QThreadPool) -> None:
        super().__init__()
        self.pool = pool

        layout = QVBoxLayout(self)

        # ---------- Host (CPU/RAM) ----------
        host_box = QGroupBox("Host (CPU / RAM)")
        hv = QVBoxLayout(host_box)
        self.cpu_bar = _bar(0, "CPU")
        self.ram_bar = _bar(0, "RAM")
        self.swap_bar = _bar(0, "Swap")
        hv.addWidget(self.cpu_bar)
        hv.addWidget(self.ram_bar)
        hv.addWidget(self.swap_bar)
        self.host_info = QLabel("")
        self.host_info.setStyleSheet("color: #5f6368; font-family: Consolas; font-size: 11px;")
        hv.addWidget(self.host_info)
        layout.addWidget(host_box)

        # ---------- GPU ----------
        gpu_box = QGroupBox("GPU")
        gv = QVBoxLayout(gpu_box)
        self.gpu_title = QLabel("…")
        self.gpu_title.setStyleSheet("font-weight: 600;")
        gv.addWidget(self.gpu_title)
        self.gpu_util_bar = _bar(0, "Util GPU")
        self.gpu_vram_bar = _bar(0, "VRAM")
        gv.addWidget(self.gpu_util_bar)
        gv.addWidget(self.gpu_vram_bar)
        self.gpu_info = QLabel("")
        self.gpu_info.setStyleSheet("color: #5f6368; font-family: Consolas; font-size: 11px;")
        gv.addWidget(self.gpu_info)
        layout.addWidget(gpu_box)

        # ---------- Ollama loaded ----------
        oll_box = QGroupBox("Modelos cargados en Ollama (VRAM)")
        ov = QVBoxLayout(oll_box)
        self.oll_table = QTableWidget(0, 4)
        self.oll_table.setHorizontalHeaderLabels(["Modelo", "Tamaño", "En VRAM", "% en GPU"])
        self.oll_table.verticalHeader().setVisible(False)
        self.oll_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        h = self.oll_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.oll_table.setMaximumHeight(140)
        ov.addWidget(self.oll_table)
        self.oll_empty = QLabel("(ningún modelo caliente)")
        self.oll_empty.setStyleSheet("color: #5f6368; font-style: italic;")
        ov.addWidget(self.oll_empty)
        layout.addWidget(oll_box)

        # ---------- Disco ----------
        disk_box = QGroupBox("Disco")
        dv = QHBoxLayout(disk_box)
        self.disk_bar = _bar(0, "Disco C:")
        dv.addWidget(self.disk_bar)
        self.disk_info = QLabel("")
        self.disk_info.setStyleSheet("color: #5f6368;")
        dv.addWidget(self.disk_info)
        layout.addWidget(disk_box)

        layout.addStretch()

        # Tickeo agresivo de este tab (2s) — útil para ver picos
        self._fast_timer = QTimer(self)
        self._fast_timer.setInterval(2000)
        self._fast_timer.timeout.connect(self.refresh)
        # Lo arrancamos solo cuando el tab está visible (ver showEvent/hideEvent)

        self.refresh()

    # ------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._fast_timer.start()

    def hideEvent(self, event):
        self._fast_timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        # Ollama vive en el host (puerto publicado 11434). El panel también es
        # local, así que apuntamos directo ahí — no pasamos por backend.
        job = BackgroundJob(system_stats.all_stats, "http://localhost:11434")
        job.signals.finished.connect(self._render)
        job.signals.failed.connect(lambda m: self.host_info.setText(f"error: {m}"))
        self.pool.start(job)

    def _render(self, d: dict) -> None:
        # ---- Host ----
        host = d.get("host") or {}
        self.cpu_bar.setValue(int(host.get("cpu_pct", 0)))
        self.cpu_bar.setFormat(f"CPU: {host.get('cpu_pct', 0):.1f}%  ({host.get('cpu_count_logical', '?')} threads)")
        ram = host.get("ram") or {}
        self.ram_bar.setValue(int(ram.get("pct", 0)))
        self.ram_bar.setFormat(f"RAM: {ram.get('used_gb', 0):.1f} / {ram.get('total_gb', 0):.1f} GB  ({ram.get('pct', 0):.0f}%)")
        swap = host.get("swap") or {}
        self.swap_bar.setValue(int(swap.get("pct", 0)))
        self.swap_bar.setFormat(f"Swap: {swap.get('used_gb', 0):.1f} / {swap.get('total_gb', 0):.1f} GB")

        # ---- GPU ----
        gpus = d.get("gpus")
        if not gpus:
            self.gpu_title.setText("(sin GPU NVIDIA detectada o nvidia-smi no disponible)")
            self.gpu_info.setText("")
            self.gpu_util_bar.setValue(0)
            self.gpu_vram_bar.setValue(0)
        else:
            g = gpus[0]
            temp = g.get("temp_c")
            temp_emoji = "🔥" if (temp or 0) >= 80 else ("🌡️" if (temp or 0) >= 70 else "❄️")
            self.gpu_title.setText(f"{g.get('name')} — {temp_emoji} {temp:.0f}°C")
            self.gpu_util_bar.setValue(int(g.get("util_gpu_pct") or 0))
            self.gpu_util_bar.setFormat(f"Util GPU: {g.get('util_gpu_pct', 0):.0f}%")
            self.gpu_vram_bar.setValue(int(g.get("vram_pct") or 0))
            self.gpu_vram_bar.setFormat(
                f"VRAM: {g.get('vram_used_mb', 0)/1024:.2f} / {g.get('vram_total_mb', 0)/1024:.2f} GB  "
                f"({g.get('vram_pct', 0):.1f}%)"
            )
            self.gpu_info.setText(
                f"Potencia: {g.get('power_w', 0):.0f} W / {g.get('power_limit_w', 0):.0f} W límite  "
                f"·  Fan: {g.get('fan_pct') if g.get('fan_pct') is not None else '?'}%  "
                f"·  Util memoria bus: {g.get('util_mem_pct', 0):.0f}%"
            )

        # ---- Ollama ----
        loaded = d.get("ollama_loaded")
        if not loaded:
            self.oll_table.setRowCount(0)
            self.oll_empty.setVisible(True)
        else:
            self.oll_empty.setVisible(False)
            self.oll_table.setRowCount(len(loaded))
            for i, m in enumerate(loaded):
                self.oll_table.setItem(i, 0, QTableWidgetItem(m.get("name", "?")))
                self.oll_table.setItem(i, 1, QTableWidgetItem(f"{m.get('size_gb', 0):.2f} GB"))
                self.oll_table.setItem(i, 2, QTableWidgetItem(f"{m.get('vram_gb', 0):.2f} GB"))
                pct = m.get("vram_pct_of_model")
                pct_str = f"{pct:.0f}%" if pct is not None else "—"
                cell = QTableWidgetItem(pct_str)
                if pct is not None and pct < 100:
                    cell.setForeground(Qt.GlobalColor.yellow)
                self.oll_table.setItem(i, 3, cell)

        # ---- Disco ----
        disk = host.get("disk")
        if disk:
            self.disk_bar.setValue(int(disk.get("pct", 0)))
            self.disk_bar.setFormat(f"Disco: {disk.get('used_gb', 0):.0f} / {disk.get('total_gb', 0):.0f} GB")
            self.disk_info.setText(f"Libre: {disk.get('free_gb', 0):.0f} GB")
