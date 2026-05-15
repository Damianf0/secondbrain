"""Router del worker continuo de colas.

  GET  /api/worker/status — estado, contadores y último tick
  POST /api/worker/pause  — pausa el loop (no procesa hasta resume)
  POST /api/worker/resume — reanuda el loop
  POST /api/worker/tick   — fuerza un tick ahora (útil para debug / on-demand)
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.queue_worker import worker

router = APIRouter(prefix="/api/worker", tags=["worker"])


@router.get("/status")
def status() -> dict[str, Any]:
    return worker.status()


@router.post("/pause")
def pause() -> dict[str, Any]:
    worker.pause()
    return {"ok": True, "paused": worker.paused}


@router.post("/resume")
def resume() -> dict[str, Any]:
    worker.resume()
    return {"ok": True, "paused": worker.paused}


@router.post("/tick")
async def tick_now() -> dict[str, Any]:
    """Dispara un tick fuera del ciclo regular. Útil para drenar al toque."""
    if not worker.enabled:
        raise HTTPException(status_code=409, detail="Worker deshabilitado (worker_enabled=False)")
    await worker._tick()  # noqa: SLF001
    return {"ok": True, "ultimo_resultado": worker.ultimo_resultado}
