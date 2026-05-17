"""Ventana principal del panel."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QTimer, QThreadPool
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QToolBar,
)

from . import config
from .api_client import BackendClient, BackendError
from .docker_client import DockerComposeClient
from .tabs.chats import ChatsTab
from .tabs.colas import ColasTab
from .tabs.configuracion import ConfiguracionTab
from .tabs.servicios import ServiciosTab
from .tabs.sistema import SistemaTab
from .tabs.tagger import TaggerTab
from .tabs.worker import WorkerTab
from .worker_thread import BackgroundJob


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SecondBrain — Panel de control")
        self.resize(1100, 720)

        self.pool = QThreadPool.globalInstance()
        self.api = BackendClient()
        self.docker = DockerComposeClient()

        # Toolbar
        tb = QToolBar("Acciones globales")
        tb.setMovable(False)
        self.addToolBar(tb)
        act_streamlit = QAction("Streamlit ↗", self)
        act_streamlit.triggered.connect(lambda: webbrowser.open(config.STREAMLIT_URL))
        tb.addAction(act_streamlit)
        act_backend = QAction("Backend docs ↗", self)
        act_backend.triggered.connect(lambda: webbrowser.open(f"{config.BACKEND_URL}/docs"))
        tb.addAction(act_backend)
        tb.addSeparator()
        act_refresh = QAction("↻ Refrescar todo", self)
        act_refresh.setShortcut("F5")
        act_refresh.triggered.connect(self.refresh_all)
        tb.addAction(act_refresh)

        # Tabs
        self.tabs = QTabWidget()
        self.t_sistema = SistemaTab(self.pool)
        self.t_servicios = ServiciosTab(self.docker, self.pool)
        self.t_worker = WorkerTab(self.api, self.pool)
        self.t_colas = ColasTab(self.api, self.pool)
        self.t_chats = ChatsTab(self.api, self.pool)
        self.t_tagger = TaggerTab(self.api, self.pool)
        self.t_config = ConfiguracionTab(self.api, self.pool)
        self.tabs.addTab(self.t_sistema, "Sistema")
        self.tabs.addTab(self.t_servicios, "Servicios")
        self.tabs.addTab(self.t_worker, "Worker")
        self.tabs.addTab(self.t_colas, "Colas")
        self.tabs.addTab(self.t_chats, "Chats")
        self.tabs.addTab(self.t_tagger, "Tagger")
        self.tabs.addTab(self.t_config, "Configuración")
        self.setCentralWidget(self.tabs)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.health_label = QLabel("…")
        self.compose_label = QLabel(
            f"compose: {self.docker.project_dir}" if self.docker.available else "compose: no encontrado"
        )
        self.compose_label.setStyleSheet("color: #5f6368;")
        sb.addPermanentWidget(self.health_label)
        sb.addWidget(self.compose_label)

        # Auto-refresh
        self.timer = QTimer(self)
        self.timer.setInterval(config.REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        # Health inicial
        self._tick()

    # ------------------------------------------------------------

    def refresh_all(self) -> None:
        self.t_sistema.refresh()
        self.t_servicios.refresh()
        self.t_worker.refresh()
        self.t_colas.refresh()
        self.t_chats.refresh()
        self.t_tagger.refresh()
        self.t_config.refresh()
        self._tick()

    def _tick(self) -> None:
        """Ping al backend + refresh del tab activo (no de todos para ahorrar)."""
        idx = self.tabs.currentIndex()
        current = self.tabs.widget(idx)
        if current and hasattr(current, "refresh"):
            current.refresh()

        job = BackgroundJob(self.api.health)
        job.signals.finished.connect(self._render_health)
        job.signals.failed.connect(lambda m: self.health_label.setText(f"backend: ❌ {m[:40]}"))
        self.pool.start(job)

    def _render_health(self, d: dict) -> None:
        services = d.get("services") or {}
        bad = [k for k, v in services.items() if isinstance(v, dict) and not v.get("ok", True)]
        if not bad:
            self.health_label.setText("backend: ✓ ok")
            self.health_label.setStyleSheet("color: #1e8e3e; font-weight: 600;")
        else:
            self.health_label.setText(f"backend: ⚠ {','.join(bad)}")
            self.health_label.setStyleSheet("color: #d93025; font-weight: 600;")
