"""
Cliente Ollama.

Wrapper sobre la lib oficial ollama-python con helpers convenientes
para los casos de uso del proyecto: generación, embeddings, visión.

Lock de VRAM: `_VRAM_LOCK` serializa las llamadas que van a GPU (generate,
embed, embed_many, vision). En un equipo con 8GB de VRAM, dos inferencias
simultáneas pueden competir por memoria y forzar swap entre modelos (más
lento que esperar en cola). El lock reemplaza los parches puntuales que
tenía v1 (`force_cpu` para el embed del chat, ventana nocturna para el
caption) por una solución de fondo: todo lo que toca GPU espera su turno,
con telemetría de cuánto esperó (`wait_vram_ms`) para poder ver si la cola
se vuelve un cuello de botella real. Portado de una implementación ya
probada en `workbench-reforma-2026` / v2 de este proyecto (ver
rediseno-secondbrain.md §5.2).
"""

import threading
import time
from typing import Any

import httpx
from ollama import Client

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Serializa el acceso a GPU entre todas las instancias de OllamaService.
_VRAM_LOCK = threading.Lock()

# Modelos con "thinking" / reasoning interno. Para nuestro uso (extracción JSON)
# el thinking solo gasta tokens y arruina la latencia, así que lo apagamos.
_THINKING_MODEL_HINTS = ("qwen3", "deepseek-r1", "qwq", "magistral", "phi4-reasoning")


def _is_thinking_model(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _THINKING_MODEL_HINTS)


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
        # Apagar el reasoning interno en modelos thinking (qwen3, etc.)
        if _is_thinking_model(model):
            kwargs["think"] = False

        wait_start = time.time()
        with _VRAM_LOCK:
            wait_ms = int((time.time() - wait_start) * 1000)
            if wait_ms > 50:
                logger.info("ollama_acquired_vram_lock", model=model, wait_ms=wait_ms, op="generate")

            start = time.time()
            try:
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
                    "wait_vram_ms": wait_ms,
                }
            except Exception as e:
                logger.error("ollama_generate_failed", model=model, error=str(e))
                raise

    def embed(
        self,
        text: str,
        model: str | None = None,
        *,
        force_cpu: bool = False,
    ) -> dict[str, Any]:
        """
        Genera un vector embedding del texto.

        Args:
            text: el texto a embebir
            model: nombre del modelo (default: model_embedding)
            force_cpu: si True, fuerza el embedding a CPU (num_gpu=0). Útil para
                queries únicas del chat: evita el swap caro entre el modelo de
                embedding y el modelo de generación que comparten VRAM. El
                embedding de una query corta en CPU tarda ~1-3s, mucho menos
                que los ~10-15s del swap.

        Returns:
            dict con 'embedding' (list[float]), 'dimensions', 'duration_ms'
        """
        model = model or settings.ollama_model_embedding

        # force_cpu no compite por VRAM — no necesita el lock.
        if force_cpu:
            start = time.time()
            try:
                response = self.client.embed(model=model, input=text, options={"num_gpu": 0})
                duration_ms = int((time.time() - start) * 1000)
                embedding = response.embeddings[0] if response.embeddings else []
                return {
                    "embedding": embedding,
                    "dimensions": len(embedding),
                    "model": model,
                    "duration_ms": duration_ms,
                    "wait_vram_ms": 0,
                }
            except Exception as e:
                logger.error("ollama_embed_cpu_failed", model=model, error=str(e))
                raise

        wait_start = time.time()
        with _VRAM_LOCK:
            wait_ms = int((time.time() - wait_start) * 1000)
            if wait_ms > 50:
                logger.info("ollama_acquired_vram_lock", model=model, wait_ms=wait_ms, op="embed")

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
                    "wait_vram_ms": wait_ms,
                }
            except Exception as e:
                logger.error("ollama_embed_failed", model=model, error=str(e))
                raise

    def embed_many(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Embebe una lista de textos en una sola llamada. Devuelve lista de vectores (en orden)."""
        model = model or settings.ollama_model_embedding
        if not texts:
            return []

        wait_start = time.time()
        with _VRAM_LOCK:
            wait_ms = int((time.time() - wait_start) * 1000)
            if wait_ms > 50:
                logger.info("ollama_acquired_vram_lock", model=model, wait_ms=wait_ms, op="embed_many")

            try:
                response = self.client.embed(model=model, input=texts)
                return list(response.embeddings or [])
            except Exception as e:
                logger.error("ollama_embed_many_failed", model=model, n=len(texts), error=str(e))
                raise

    def vision(
        self,
        prompt: str,
        image_bytes: bytes,
        model: str | None = None,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        format: str | dict | None = None,
    ) -> dict[str, Any]:
        """
        Procesa una imagen con un VLM.

        Args:
            prompt: la pregunta sobre la imagen
            image_bytes: la imagen como bytes
            model: VLM (default: model_vision)
            system: system prompt opcional
            temperature: 0.0 = determinístico
            format: 'json' para forzar JSON, o un schema dict
        """
        import base64

        model = model or settings.ollama_model_vision
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt, "images": [image_b64]})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "options": {"temperature": temperature},
        }
        if format is not None:
            kwargs["format"] = format
        if _is_thinking_model(model):
            kwargs["think"] = False

        wait_start = time.time()
        with _VRAM_LOCK:
            wait_ms = int((time.time() - wait_start) * 1000)
            if wait_ms > 50:
                logger.info("ollama_acquired_vram_lock", model=model, wait_ms=wait_ms, op="vision")

            start = time.time()
            try:
                response = self.client.chat(**kwargs)
                duration_ms = int((time.time() - start) * 1000)

                return {
                    "response": response.message.content,
                    "model": response.model,
                    "duration_ms": duration_ms,
                    "tokens_input": response.prompt_eval_count or 0,
                    "tokens_output": response.eval_count or 0,
                    "wait_vram_ms": wait_ms,
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
