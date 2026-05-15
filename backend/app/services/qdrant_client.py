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
                status = getattr(info, "status", None)
                details.append(
                    {
                        "name": name,
                        "points_count": getattr(info, "points_count", None),
                        "status": status.value if hasattr(status, "value") else str(status) if status else "unknown",
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
        """Crea la collection si no existe. Devuelve True si la creó, False si ya estaba."""
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
            logger.info("qdrant_collection_created", name=name, size=vector_size, distance=distance)
            return True
        except Exception as e:
            logger.error("qdrant_ensure_collection_failed", name=name, error=str(e))
            raise

    def upsert_points(
        self,
        collection: str,
        points: list[dict[str, Any]],
    ) -> int:
        """
        Inserta/actualiza puntos. Cada point: {"id": str|int, "vector": list[float], "payload": dict}.
        Devuelve la cantidad de puntos enviados.
        """
        from qdrant_client.models import PointStruct

        if not points:
            return 0
        structs = [
            PointStruct(id=p["id"], vector=p["vector"], payload=p.get("payload") or {})
            for p in points
        ]
        self.client.upsert(collection_name=collection, points=structs, wait=True)
        return len(structs)

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 10,
        query_filter: dict[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Búsqueda por similitud. `query_filter` admite la forma simple
        {"must": [{"key": ..., "match": {"value": ...}}, ...]} de qdrant.
        Devuelve [{"id", "score", "payload"}].
        """
        from qdrant_client.models import Filter

        flt = Filter(**query_filter) if query_filter else None
        res = self.client.query_points(
            collection_name=collection,
            query=vector,
            limit=limit,
            query_filter=flt,
            score_threshold=score_threshold,
            with_payload=True,
        )
        return [{"id": str(p.id), "score": p.score, "payload": p.payload or {}} for p in res.points]

    def count(self, collection: str) -> int:
        """Cantidad de puntos en la collection (0 si no existe)."""
        try:
            return self.client.count(collection_name=collection, exact=True).count
        except Exception:  # noqa: BLE001
            return 0

    def collection_exists(self, name: str) -> bool:
        try:
            return name in [c.name for c in self.client.get_collections().collections]
        except Exception:  # noqa: BLE001
            return False

    def delete_by_item(self, collection: str, item_id: str) -> None:
        """Borra todos los puntos cuyo payload.item_id == item_id."""
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        if not self.collection_exists(collection):
            return
        self.client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="item_id", match=MatchValue(value=str(item_id)))])
            ),
            wait=True,
        )
