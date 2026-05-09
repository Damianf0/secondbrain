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
