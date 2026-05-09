"""
Cliente Whisper.

Wrapper sobre el contenedor onerahmet/openai-whisper-asr-webservice.
En Sprint 0 sólo health check. En sprints siguientes lo usamos para
transcribir audios de WhatsApp.
"""

from typing import Any

import httpx

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class WhisperService:
    """Servicio para transcripción de audio con Whisper."""

    def __init__(self) -> None:
        self.base_url = settings.whisper_url

    async def health(self) -> dict[str, Any]:
        """Health check del servicio Whisper."""
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=5.0) as client:
                r = await client.get("/docs")
                return {
                    "ok": r.status_code == 200,
                    "url": self.base_url,
                    "status_code": r.status_code,
                }
        except Exception as e:
            logger.error("whisper_health_check_failed", error=str(e))
            return {"ok": False, "url": self.base_url, "error": str(e)}

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.opus",
        language: str = "es",
    ) -> dict[str, Any]:
        """
        Transcribe un audio.

        Args:
            audio_bytes: contenido del audio
            filename: nombre del archivo (afecta detección de formato)
            language: código ISO ('es', 'en', etc.)

        Returns:
            dict con 'text', 'segments', 'language', 'duration'
        """
        async with httpx.AsyncClient(base_url=self.base_url, timeout=300.0) as client:
            files = {"audio_file": (filename, audio_bytes, "application/octet-stream")}
            params = {"language": language, "output": "json"}

            r = await client.post("/asr", files=files, params=params)
            r.raise_for_status()
            return r.json()
