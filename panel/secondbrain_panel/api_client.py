"""Cliente HTTP al backend FastAPI.

Una clase delgada con métodos por endpoint. Mantenemos sync (no async) porque
las llamadas se hacen en threads vía QThreadPool — el event loop de Qt no
juega bien con asyncio sin gymnastics.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import config


class BackendError(Exception):
    """Error genérico al hablar con el backend."""


class BackendClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or config.BACKEND_URL).rstrip("/")
        self.timeout = timeout or config.HTTP_TIMEOUT

    # ------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> Any:
        return self._req("GET", path, **kwargs)

    def _post(self, path: str, **kwargs) -> Any:
        return self._req("POST", path, **kwargs)

    def _req(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.request(method, url, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException as e:
            raise BackendError(f"timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.json().get("detail") or ""
            except Exception:
                detail = e.response.text[:200] if e.response else ""
            raise BackendError(f"HTTP {e.response.status_code}: {detail}") from e
        except httpx.HTTPError as e:
            raise BackendError(f"error de red: {e}") from e

    # ------------------------------------------------------------
    # Health & general
    # ------------------------------------------------------------

    def health(self) -> dict:
        return self._get("/api/health")

    # ------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------

    def worker_status(self) -> dict:
        return self._get("/api/worker/status")

    def worker_pause(self) -> dict:
        return self._post("/api/worker/pause")

    def worker_resume(self) -> dict:
        return self._post("/api/worker/resume")

    def worker_tick_now(self) -> dict:
        return self._post("/api/worker/tick")

    # ------------------------------------------------------------
    # Tagger
    # ------------------------------------------------------------

    def tagger_stats(self) -> dict:
        return self._get("/api/tagger/stats")

    def tagger_run(self, limit: int = 10, solo_seguidos: bool = True) -> dict:
        """Procesa N items pendientes sincrónicamente. Bloquea hasta terminar.
        Para batches > 5 usar `enqueue_tagger_jobs` y dejar que el worker drene.
        """
        return self._post(
            "/api/tagger/run",
            params={"limit": int(limit), "solo_seguidos": str(solo_seguidos).lower()},
        )

    def tagger_results(self, limit: int = 30) -> dict:
        return self._get("/api/tagger/results", params={"limit": int(limit)})

    def tagger_item(self, item_id: str) -> dict:
        return self._post(f"/api/tagger/item/{item_id}")

    # ------------------------------------------------------------
    # Tagger queue (panel-specific)
    # ------------------------------------------------------------

    def panel_enqueue_tagger(self, days: int = 2, limit: int | None = None) -> dict:
        """Encola jobs de tagger para items recientes sin tagged_at.
        Endpoint provisto por el panel (ver routers/panel.py).
        """
        params: dict[str, Any] = {"days": int(days)}
        if limit is not None:
            params["limit"] = int(limit)
        return self._post("/api/panel/tagger/enqueue", params=params)

    def panel_queue_counts(self) -> dict:
        """Contadores por tipo y estado para todas las colas."""
        return self._get("/api/panel/queues")

    # ------------------------------------------------------------
    # Panel: conversaciones
    # ------------------------------------------------------------

    def panel_conversations(self, seguidas_solo: bool = False, limit: int = 200) -> dict:
        return self._get(
            "/api/panel/conversations",
            params={"seguidas_solo": str(seguidas_solo).lower(), "limit": int(limit)},
        )

    def panel_conversation_enqueue(
        self,
        conversation_id: str,
        tipos: list[str],
        *,
        solo_pendientes: bool = True,
        limit: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "tipos": tipos,
            "solo_pendientes": solo_pendientes,
        }
        if limit is not None:
            body["limit"] = int(limit)
        return self._post(f"/api/panel/conversations/{conversation_id}/enqueue", json=body)

    # ------------------------------------------------------------
    # Panel: config runtime
    # ------------------------------------------------------------

    def panel_config(self) -> dict:
        return self._get("/api/panel/config")

    def panel_update_worker_config(
        self,
        *,
        interval_s: int | None = None,
        batch: dict[str, int] | None = None,
        caption_hour_start: int | None = None,
        caption_hour_end: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if interval_s is not None:
            body["interval_s"] = int(interval_s)
        if batch:
            body["batch"] = {k: int(v) for k, v in batch.items()}
        if caption_hour_start is not None:
            body["caption_hour_start"] = int(caption_hour_start)
        if caption_hour_end is not None:
            body["caption_hour_end"] = int(caption_hour_end)
        return self._req("PATCH", "/api/panel/config/worker", json=body)

    def panel_update_tagger_config(
        self,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if model is not None:
            body["model"] = model
        if temperature is not None:
            body["temperature"] = float(temperature)
        return self._req("PATCH", "/api/panel/config/tagger", json=body)

    def panel_ollama_models(self) -> dict:
        return self._get("/api/panel/ollama/models")
