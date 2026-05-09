"""
Cliente Ollama.

Wrapper sobre la lib oficial ollama-python con helpers convenientes
para los casos de uso del proyecto: generación, embeddings, visión.
"""

import time
from typing import Any

import httpx
from ollama import Client

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class OllamaService:
    """Servicio para interactuar con Ollama local."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.ollama_url
        self.client = Client(host=self.base_url)

    def health(self) -> dict[str, Any]:
        """Verifica que Ollama está respondiendo y lista los modelos cargados."""
        try:
            models = self.client.list()
            return {
                "ok": True,
                "url": self.base_url,
                "models": [m.model for m in models.models],
            }
        except Exception as e:
            logger.error("ollama_health_check_failed", error=str(e))
            return {"ok": False, "url": self.base_url, "error": str(e)}

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        format: str | dict | None = None,
    ) -> dict[str, Any]:
        """
        Genera texto a partir de un prompt.

        Args:
            prompt: el texto del usuario
            model: nombre del modelo (default: model_primary)
            system: system prompt opcional
            temperature: 0.0 = determinístico, 1.0 = creativo
            format: 'json' para forzar output JSON, o un schema dict

        Returns:
            dict con 'response', 'model', 'duration_ms', 'tokens', etc.
        """
        model = model or settings.ollama_model_primary

        start = time.time()
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            kwargs = {
                "model": model,
                "messages": messages,
                "options": {"temperature": temperature},
            }
            if format is not None:
                kwargs["format"] = format

            response = self.client.chat(**kwargs)
            duration_ms = int((time.time() - start) * 1000)

            return {
                "response": response.message.content,
                "model": response.model,
                "duration_ms": duration_ms,
                "tokens_input": response.prompt_eval_count or 0,
                "tokens_output": response.eval_count or 0,
                "tokens_per_second": (
                    round(response.eval_count / (response.eval_duration / 1e9), 2)
                    if response.eval_count and response.eval_duration
                    else None
                ),
            }
        except Exception as e:
            logger.error("ollama_generate_failed", model=model, error=str(e))
            raise

    def embed(self, text: str, model: str | None = None) -> dict[str, Any]:
        """
        Genera un vector embedding del texto.

        Args:
            text: el texto a embebir
            model: nombre del modelo (default: model_embedding)

        Returns:
            dict con 'embedding' (list[float]), 'dimensions', 'duration_ms'
        """
        model = model or settings.ollama_model_embedding

        start = time.time()
        try:
            response = self.client.embed(model=model, input=text)
            duration_ms = int((time.time() - start) * 1000)

            embedding = response.embeddings[0] if response.embeddings else []

            return {
                "embedding": embedding,
                "dimensions": len(embedding),
                "model": model,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            logger.error("ollama_embed_failed", model=model, error=str(e))
            raise

    def vision(
        self,
        prompt: str,
        image_bytes: bytes,
        model: str | None = None,
    ) -> dict[str, Any]:
        """
        Procesa una imagen con un VLM.

        Args:
            prompt: la pregunta sobre la imagen
            image_bytes: la imagen como bytes
            model: VLM (default: model_vision)
        """
        import base64

        model = model or settings.ollama_model_vision
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        start = time.time()
        try:
            response = self.client.chat(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [image_b64],
                    }
                ],
                options={"temperature": 0.0},
            )
            duration_ms = int((time.time() - start) * 1000)

            return {
                "response": response.message.content,
                "model": response.model,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            logger.error("ollama_vision_failed", model=model, error=str(e))
            raise

    async def list_models_detailed(self) -> list[dict[str, Any]]:
        """Lista los modelos con detalles (tamaño, fecha de modificación)."""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            r = await client.get("/api/tags")
            r.raise_for_status()
            data = r.json()
            return [
                {
                    "name": m["name"],
                    "size_gb": round(m["size"] / 1024**3, 2),
                    "modified_at": m.get("modified_at"),
                }
                for m in data.get("models", [])
            ]
