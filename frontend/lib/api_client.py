"""
Cliente HTTP del frontend para comunicarse con el backend FastAPI.
"""

import os
from typing import Any

import httpx

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


class APIClient:
    """Cliente sencillo para los endpoints del backend."""

    def __init__(self, base_url: str = BACKEND_URL, timeout: float = 120.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    # -----------------------------------------------------------
    # Health
    # -----------------------------------------------------------

    def health_overview(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/health")
            r.raise_for_status()
            return r.json()

    def is_alive(self) -> bool:
        try:
            with httpx.Client(base_url=self.base_url, timeout=3.0) as client:
                r = client.get("/api/health/live")
                return r.status_code == 200
        except Exception:
            return False

    # -----------------------------------------------------------
    # LLM y embeddings
    # -----------------------------------------------------------

    def test_llm(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        with self._client() as client:
            r = client.post(
                "/api/test/llm",
                json={
                    "prompt": prompt,
                    "model": model,
                    "system": system,
                    "temperature": temperature,
                },
            )
            r.raise_for_status()
            return r.json()

    def test_embed(self, text: str, model: str | None = None) -> dict[str, Any]:
        with self._client() as client:
            r = client.post(
                "/api/test/embed",
                json={"text": text, "model": model},
            )
            r.raise_for_status()
            return r.json()

    def list_models(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/test/models")
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Vault
    # -----------------------------------------------------------

    def vault_upload(self, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        with self._client() as client:
            r = client.post(
                "/api/test/vault/upload",
                files={"file": (filename, content, content_type)},
            )
            r.raise_for_status()
            return r.json()

    def vault_stats(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/test/vault/stats")
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Qdrant
    # -----------------------------------------------------------

    def qdrant_ensure_test_collection(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.post("/api/test/qdrant/ensure-test-collection")
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Contactos y conversaciones (Sprint 2.5) — usado por el chat para filtros
    # -----------------------------------------------------------

    def listar_contactos(
        self,
        *,
        q: str = "",
        seguir: bool | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": q, "limit": int(limit)}
        if seguir is not None:
            params["seguir"] = str(seguir).lower()
        with self._client() as client:
            r = client.get("/api/contacts", params=params)
            r.raise_for_status()
            return r.json()

    def listar_conversaciones(
        self,
        *,
        q: str = "",
        tipo: str | None = None,
        seguir: bool | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": q, "limit": int(limit)}
        if tipo:
            params["tipo"] = tipo
        if seguir is not None:
            params["seguir"] = str(seguir).lower()
        with self._client() as client:
            r = client.get("/api/conversations", params=params)
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Embeddings (Sprint 4)
    # -----------------------------------------------------------

    def embeddings_stats(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/embeddings/stats")
            r.raise_for_status()
            return r.json()

    def embeddings_run(
        self,
        limit: int = 200,
        *,
        conversation_id: str | None = None,
        solo_taggeados: bool = False,
        solo_seguidos: bool = True,
        timeout: float = 1800.0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": int(limit),
            "solo_taggeados": str(solo_taggeados).lower(),
            "solo_seguidos": str(solo_seguidos).lower(),
        }
        if conversation_id:
            params["conversation_id"] = conversation_id
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/embeddings/run", params=params)
            r.raise_for_status()
            return r.json()

    def embeddings_work(self, limit: int = 50, timeout: float = 1800.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/embeddings/work", params={"limit": int(limit)})
            r.raise_for_status()
            return r.json()

    def embeddings_item(self, item_id: str) -> dict[str, Any]:
        with self._client() as client:
            r = client.post(f"/api/embeddings/item/{item_id}")
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Transcripción (Sprint 7)
    # -----------------------------------------------------------

    def transcribe_stats(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/transcribe/stats")
            r.raise_for_status()
            return r.json()

    def transcribe_pendientes(
        self,
        *,
        solo_pendientes: bool = True,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"solo_pendientes": str(solo_pendientes).lower(), "limit": int(limit)}
        if conversation_id:
            params["conversation_id"] = conversation_id
        with self._client() as client:
            r = client.get("/api/transcribe/pendientes", params=params)
            r.raise_for_status()
            return r.json()

    def transcribe_item(self, item_id: str, timeout: float = 600.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post(f"/api/transcribe/item/{item_id}")
            r.raise_for_status()
            return r.json()

    def transcribe_work(self, limit: int = 20, timeout: float = 1800.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/transcribe/work", params={"limit": int(limit)})
            r.raise_for_status()
            return r.json()

    def transcribe_upload(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        conversation_id: str = "manual_upload",
        transcribir_ahora: bool = False,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post(
                "/api/transcribe/upload",
                files={"file": (filename, content, content_type)},
                data={
                    "conversation_id": conversation_id,
                    "transcribir_ahora": str(transcribir_ahora).lower(),
                },
            )
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Documentos / Extracción (Sprint 6)
    # -----------------------------------------------------------

    def extract_stats(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/extract/stats")
            r.raise_for_status()
            return r.json()

    def extract_pendientes(
        self,
        *,
        solo_pendientes: bool = True,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"solo_pendientes": str(solo_pendientes).lower(), "limit": int(limit)}
        if conversation_id:
            params["conversation_id"] = conversation_id
        with self._client() as client:
            r = client.get("/api/extract/pendientes", params=params)
            r.raise_for_status()
            return r.json()

    def extract_item(self, item_id: str, timeout: float = 300.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post(f"/api/extract/item/{item_id}")
            r.raise_for_status()
            return r.json()

    def extract_work(self, limit: int = 20, timeout: float = 1800.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/extract/work", params={"limit": int(limit)})
            r.raise_for_status()
            return r.json()

    def extract_upload(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        conversation_id: str = "manual_upload",
        extraer_ahora: bool = False,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post(
                "/api/extract/upload",
                files={"file": (filename, content, content_type)},
                data={
                    "conversation_id": conversation_id,
                    "extraer_ahora": str(extraer_ahora).lower(),
                },
            )
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Imágenes / VLM (Sprint 5)
    # -----------------------------------------------------------

    def images_stats(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/images/stats")
            r.raise_for_status()
            return r.json()

    def images_pendientes(
        self,
        *,
        solo_pendientes: bool = True,
        limit: int = 30,
        conversation_id: str | None = None,
        incluir_triviales: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "solo_pendientes": str(solo_pendientes).lower(),
            "incluir_triviales": str(incluir_triviales).lower(),
            "limit": int(limit),
        }
        if conversation_id:
            params["conversation_id"] = conversation_id
        with self._client() as client:
            r = client.get("/api/images/pendientes", params=params)
            r.raise_for_status()
            return r.json()

    def images_item(self, item_id: str, timeout: float = 300.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post(f"/api/images/item/{item_id}")
            r.raise_for_status()
            return r.json()

    def images_work(self, limit: int = 10, timeout: float = 1800.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/images/work", params={"limit": int(limit)})
            r.raise_for_status()
            return r.json()

    def images_upload(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        conversation_id: str = "manual_upload",
        procesar_ahora: bool = False,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post(
                "/api/images/upload",
                files={"file": (filename, content, content_type)},
                data={
                    "conversation_id": conversation_id,
                    "procesar_ahora": str(procesar_ahora).lower(),
                },
            )
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Worker continuo de colas
    # -----------------------------------------------------------

    def worker_status(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.get("/api/worker/status")
            r.raise_for_status()
            return r.json()

    def worker_pause(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.post("/api/worker/pause")
            r.raise_for_status()
            return r.json()

    def worker_resume(self) -> dict[str, Any]:
        with self._client() as client:
            r = client.post("/api/worker/resume")
            r.raise_for_status()
            return r.json()

    def worker_tick(self, timeout: float = 1800.0) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/worker/tick")
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------
    # Chat / Q&A (Sprint 4)
    # -----------------------------------------------------------

    def chat(
        self,
        pregunta: str,
        *,
        k_messages: int = 12,
        k_facts: int = 8,
        model: str | None = None,
        persona_id: str | None = None,
        conversation_id: str | None = None,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        payload = {
            "pregunta": pregunta,
            "k_messages": int(k_messages),
            "k_facts": int(k_facts),
            "model": model,
            "persona_id": persona_id,
            "conversation_id": conversation_id,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        }
        with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
            r = client.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json()

    def chat_retrieve(
        self,
        pregunta: str,
        *,
        k_messages: int = 12,
        k_facts: int = 8,
        persona_id: str | None = None,
        conversation_id: str | None = None,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "pregunta": pregunta,
            "k_messages": int(k_messages),
            "k_facts": int(k_facts),
            "persona_id": persona_id,
            "conversation_id": conversation_id,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        }
        with self._client() as client:
            r = client.post("/api/chat/retrieve", json=payload)
            r.raise_for_status()
            return r.json()
