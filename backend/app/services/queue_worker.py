"""
Worker continuo de colas.

Un único `asyncio.Task` que corre dentro del proceso del backend FastAPI.
Cada `worker_interval_s` segundos drena las 4 colas de `processing.jobs` en
orden de prioridad:

  1. transcribe (audios) — GPU lento
  2. extract    (docs)   — CPU
  3. caption    (imgs)   — GPU lento (qwen3-vl)
  4. embed      (texto)  — GPU rápido (qwen3-embedding)

Cada `procesar_jobs` es síncrono (usa SQLAlchemy session sync) y se ejecuta en
un thread con `asyncio.to_thread` para no bloquear el event loop. Una sola tx
por job dentro del procesar — un fallo no rompe los demás.

Configurable via env vars (`worker_enabled`, `worker_interval_s`,
`worker_batch_*`). Pausable en runtime con `worker.pause()` / `worker.resume()`.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.services import extractor, imager, tagger, transcriber
from app.services.embedder import procesar_jobs as embed_procesar_jobs

logger = get_logger(__name__)
settings = get_settings()


def _caption_en_ventana() -> bool:
    """¿La hora local actual cae dentro de la ventana permitida para captions?

    La ventana se define con [worker_caption_hour_start, worker_caption_hour_end).
    Soporta cruce de medianoche (start > end → ej. 22→6). Si start == end, la
    etapa queda deshabilitada (devuelve False siempre).
    """
    start = settings.worker_caption_hour_start
    end = settings.worker_caption_hour_end
    if start == end:
        return False
    hora = datetime.now(ZoneInfo(settings.tz)).hour
    if start < end:
        return start <= hora < end
    # ventana cruza medianoche (ej. 22..6)
    return hora >= start or hora < end


def _con_db(fn, *args, **kwargs):
    """Helper que abre una Session, corre fn(db, ...) y la cierra."""
    db: Session = SessionLocal()
    try:
        return fn(db, *args, **kwargs)
    finally:
        db.close()


class ContinuousWorker:
    """Worker singleton que corre en el lifespan del backend."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_evt: asyncio.Event | None = None
        self._lock = threading.Lock()
        self.enabled = settings.worker_enabled
        self.interval_s = settings.worker_interval_s
        self.batch = {
            "transcribe": settings.worker_batch_transcribe,
            "extract": settings.worker_batch_extract,
            "caption": settings.worker_batch_caption,
            "embed": settings.worker_batch_embed,
            "tagger": settings.worker_batch_tagger,
        }
        # Estado para /api/worker/status
        self.started_at: str | None = None
        self.last_tick_at: str | None = None
        self.last_tick_duration_ms: int | None = None
        self.ticks_total: int = 0
        self.ticks_con_trabajo: int = 0
        self.acumulado = {
            "transcribe_procesados": 0,
            "extract_procesados": 0,
            "caption_procesados": 0,
            "embed_procesados": 0,
            "tagger_procesados": 0,
            "errores": 0,
        }
        self.ultimo_resultado: dict[str, Any] | None = None
        self.paused = False

    # ------------------------------------------------------------
    # Ciclo de vida (lifespan)
    # ------------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        if not self.enabled:
            logger.info("worker_disabled")
            return
        self._stop_evt = asyncio.Event()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._task = asyncio.create_task(self._run(), name="queue_worker")
        logger.info("worker_started", interval_s=self.interval_s, batch=self.batch)

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_evt:
            self._stop_evt.set()
        try:
            await asyncio.wait_for(self._task, timeout=10)
        except (TimeoutError, asyncio.TimeoutError):
            self._task.cancel()
        self._task = None
        self._stop_evt = None
        logger.info("worker_stopped")

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    # ------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------

    async def _run(self) -> None:
        assert self._stop_evt is not None
        while not self._stop_evt.is_set():
            try:
                if not self.paused:
                    await self._tick()
            except Exception as e:  # noqa: BLE001
                logger.error("worker_tick_failed", error=str(e))
                self.acumulado["errores"] += 1
            # Esperar interval_s (o que nos paren)
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=self.interval_s)
                break  # se prendió stop
            except (TimeoutError, asyncio.TimeoutError):
                continue  # interval cumplido, próxima vuelta

    async def _tick(self) -> None:
        t0 = datetime.now(timezone.utc)
        resumen: dict[str, Any] = {"ts": t0.isoformat(), "etapas": {}}

        # Orden de prioridad: trabajo "lento" primero para que la GPU no quede ociosa.
        # tagger va después de embed: se encola al embeber un item, así que
        # garantiza que cuando lleguemos al tagger ya está el embedding listo
        # (no es necesario para el funcionamiento, pero da un orden natural).
        for etapa, fn, batch in (
            ("transcribe", transcriber.procesar_jobs, self.batch["transcribe"]),
            ("extract", extractor.procesar_jobs, self.batch["extract"]),
            ("caption", imager.procesar_jobs, self.batch["caption"]),
            ("embed", embed_procesar_jobs, self.batch["embed"]),
            ("tagger", tagger.procesar_jobs, self.batch["tagger"]),
        ):
            # Caption sólo corre dentro de la ventana horaria configurada
            # (default 02-06 local) — el VLM compite por VRAM con el chat.
            if etapa == "caption" and not _caption_en_ventana():
                resumen["etapas"][etapa] = {"saltado": "fuera_de_ventana"}
                continue
            try:
                res = await asyncio.to_thread(_con_db, fn, limit=batch)
                resumen["etapas"][etapa] = {
                    "procesados": res.get("procesados", 0),
                    "exitosos": res.get("exitosos", res.get("mensajes_embebidos", 0)),
                    "fallidos": res.get("fallidos", res.get("errores", 0)) if isinstance(res.get("fallidos"), int) else res.get("errores", 0),
                    "pendientes_restantes": res.get("pendientes_restantes", 0),
                }
                proc = res.get("procesados", 0)
                if proc:
                    self.acumulado[f"{etapa}_procesados"] += proc
            except Exception as e:  # noqa: BLE001
                logger.error("worker_etapa_failed", etapa=etapa, error=str(e))
                resumen["etapas"][etapa] = {"error": str(e)[:300]}
                self.acumulado["errores"] += 1

        self.ticks_total += 1
        total_procesado = sum(
            (e.get("procesados") or 0) for e in resumen["etapas"].values() if isinstance(e, dict)
        )
        if total_procesado:
            self.ticks_con_trabajo += 1

        self.last_tick_at = t0.isoformat()
        self.last_tick_duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        resumen["duration_ms"] = self.last_tick_duration_ms
        resumen["total_procesado"] = total_procesado
        self.ultimo_resultado = resumen
        if total_procesado:
            logger.info("worker_tick", **{f"{k}_procesados": v.get("procesados", 0) for k, v in resumen["etapas"].items() if isinstance(v, dict)})

    # ------------------------------------------------------------
    # Status
    # ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self._task is not None and not self._task.done(),
            "paused": self.paused,
            "interval_s": self.interval_s,
            "batch": dict(self.batch),
            "caption_window": {
                "hour_start": settings.worker_caption_hour_start,
                "hour_end": settings.worker_caption_hour_end,
                "tz": settings.tz,
                "en_ventana_ahora": _caption_en_ventana(),
            },
            "started_at": self.started_at,
            "last_tick_at": self.last_tick_at,
            "last_tick_duration_ms": self.last_tick_duration_ms,
            "ticks_total": self.ticks_total,
            "ticks_con_trabajo": self.ticks_con_trabajo,
            "acumulado": dict(self.acumulado),
            "ultimo_resultado": self.ultimo_resultado,
        }


# Singleton accesible desde main.py y el router
worker = ContinuousWorker()
