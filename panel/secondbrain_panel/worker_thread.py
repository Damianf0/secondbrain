"""Helper para correr funciones en background sin trabar la UI.

Usamos QRunnable + QThreadPool con un signal-emitter intermediario para
devolver el resultado al thread principal de Qt.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class _Signals(QObject):
    finished = Signal(object)   # resultado
    failed = Signal(str)        # mensaje de error


class BackgroundJob(QRunnable):
    """Corre fn(*args, **kwargs) en un thread; al terminar emite signals."""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.signals.finished.emit(result)
