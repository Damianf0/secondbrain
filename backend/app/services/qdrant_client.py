"""
Cliente Qdrant.

Wrapper sobre qdrant-client con helpers para los casos de uso del proyecto.
En Sprint 0 sólo health check. En sprints siguientes agregaremos
collections, upsert, search, etc.
"""

from typing import Any

from qdrant_client import QdrantClient

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class QdrantService:
    """Servicio para interactuar con Qdrant."""

    def __init__(self) -> None:
        self.client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30,
        )

    def health(self) -> dict[str, Any]:
        """Verifica conexión con Qdrant y devuelve info de collections."""
        try:
            collections_info = self.client.get_collections()
            collections = [c.name for c in collections_info.collections]

            # Detalle por collection si hay alguna
            details = []
            for name in collections:
                info = self.client.get_collection(name)
                details.append(
                    {
                        "name": name,
                        "vectors_count": info.vectors_count,
                        "points_count": info.points_count,
                        "status": info.status.value if info.status else "unknown",
                    }
                )

            return {
                "ok": True,
                "url": settings.qdrant_url,
                "collections": collections,
                "details": details,
            }
        except Exception as e:
            logger.error("qdrant_health_check_failed", error=str(e))
            return {"ok": False, "url": settings.qdrant_url, "error": str(e)}

    def ensure_collection(
        self,
        name: str,
        vector_size: int,
        distance: str = "Cosine",
    ) -> bool:
        """
        Crea la collection si no existe.

        Sprint 0: solo helper, en sprint siguiente lo usamos para crear
        collections de mensajes, facts, etc.
        """
        from qdrant_client.models import Distance, VectorParams

        try:
            existing = [c.name for c in self.client.get_collections().collections]
            if name in existing:
                return False

            distance_map = {
                "Cosine": Distance.COSINE,
                "Euclid": Distance.EUCLID,
                "Dot": Distance.DOT,
            }

            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=distance_map.get(distance, Distance.COSINE),
                ),
            )
            logger.info(
                "qdrant_collection_created",
                name=name,
                size=vector_size,
                distance=distance,
            )
            return True
        except Exception as e:
            logger.error("qdrant_ensure_collection_failed", name=name, error=str(e))
            raise
