"""
Cliente MinIO - abstracción del Vault.

Wrapper sobre minio-py con interfaz simple para guardar/recuperar
archivos crudos y derivados del Vault.

Convenciones:
    - Bucket "raw"     : archivos originales (audios .opus, imágenes, PDFs, etc.)
    - Bucket "derived" : derivados (thumbnails, transcripciones, OCR results)
    - Bucket "exports" : exports manuales (.txt de WhatsApp)

Estructura de keys dentro de "raw":
    {fuente}/{año}/{mes}/{tipo}/{hash}.{ext}

Ejemplo:
    raw/whatsapp/2026/05/audios/a3f2b8c1...opus
    raw/gmail/2026/05/attachments/b4d5e9f2...pdf
"""

import hashlib
import io
from datetime import datetime
from typing import Any, BinaryIO

from minio import Minio
from minio.error import S3Error

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class VaultStorage:
    """Servicio del Vault: guarda y recupera archivos en MinIO."""

    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket_raw = settings.minio_bucket_raw
        self.bucket_derived = settings.minio_bucket_derived

    # ---------------------------------------------------------------
    # Health
    # ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Verifica que MinIO está accesible y los buckets existen."""
        try:
            buckets = [b.name for b in self.client.list_buckets()]
            return {
                "ok": True,
                "endpoint": settings.minio_endpoint,
                "buckets": buckets,
                "bucket_raw_exists": self.bucket_raw in buckets,
                "bucket_derived_exists": self.bucket_derived in buckets,
            }
        except Exception as e:
            logger.error("minio_health_check_failed", error=str(e))
            return {"ok": False, "endpoint": settings.minio_endpoint, "error": str(e)}

    def ensure_buckets(self) -> dict[str, bool]:
        """Crea los buckets si no existen. Idempotente."""
        result = {}
        for bucket in [self.bucket_raw, self.bucket_derived, "exports"]:
            try:
                if not self.client.bucket_exists(bucket):
                    self.client.make_bucket(bucket)
                    result[bucket] = True
                    logger.info("minio_bucket_created", bucket=bucket)
                else:
                    result[bucket] = False
            except Exception as e:
                logger.error("minio_ensure_bucket_failed", bucket=bucket, error=str(e))
                result[bucket] = False
        return result

    # ---------------------------------------------------------------
    # Storage de archivos crudos
    # ---------------------------------------------------------------

    @staticmethod
    def _hash_bytes(content: bytes) -> str:
        """SHA-256 hexadecimal del contenido."""
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _build_raw_key(
        source: str,
        media_type: str,
        content_hash: str,
        extension: str,
        ts: datetime | None = None,
    ) -> str:
        """
        Construye la key de almacenamiento para un archivo raw.

        Formato: {source}/{año}/{mes}/{media_type}/{hash}.{ext}
        """
        ts = ts or datetime.now()
        ext = extension.lstrip(".")
        return f"{source}/{ts.year}/{ts.month:02d}/{media_type}/{content_hash}.{ext}"

    def store_raw(
        self,
        source: str,
        media_type: str,
        content: bytes,
        extension: str,
        mime_type: str,
        metadata: dict[str, str] | None = None,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Guarda un archivo crudo en el bucket raw, con deduplicación por hash.

        Args:
            source: 'whatsapp', 'gmail', 'manual', etc.
            media_type: 'audios', 'images', 'docs', 'videos'
            content: bytes del archivo
            extension: extensión sin punto ('opus', 'jpg', 'pdf')
            mime_type: tipo MIME ('audio/opus', 'image/jpeg', etc.)
            metadata: metadata adicional para guardar como tags S3
            ts: timestamp lógico del archivo (default: ahora)

        Returns:
            dict con 'hash', 'key', 'size', 'duplicate' (bool)
        """
        content_hash = self._hash_bytes(content)
        key = self._build_raw_key(source, media_type, content_hash, extension, ts)

        # Verificar deduplicación
        is_duplicate = False
        try:
            self.client.stat_object(self.bucket_raw, key)
            is_duplicate = True
            logger.info("vault_duplicate_detected", key=key, hash=content_hash)
        except S3Error as e:
            if e.code != "NoSuchKey":
                raise

        if not is_duplicate:
            stream = io.BytesIO(content)
            tags = {
                "source": source,
                "media_type": media_type,
                "hash_sha256": content_hash,
            }
            if metadata:
                tags.update({k: str(v) for k, v in metadata.items()})

            self.client.put_object(
                bucket_name=self.bucket_raw,
                object_name=key,
                data=stream,
                length=len(content),
                content_type=mime_type,
                metadata={f"x-amz-meta-{k}": v for k, v in tags.items()},
            )
            logger.info(
                "vault_raw_stored",
                source=source,
                media_type=media_type,
                key=key,
                size_bytes=len(content),
            )

        return {
            "hash": content_hash,
            "key": key,
            "bucket": self.bucket_raw,
            "size_bytes": len(content),
            "mime_type": mime_type,
            "duplicate": is_duplicate,
        }

    def store_derived(
        self,
        derived_type: str,
        parent_hash: str,
        content: bytes,
        extension: str,
        mime_type: str,
    ) -> dict[str, Any]:
        """
        Guarda un derivado (thumbnail, transcripción, OCR result, etc).

        Args:
            derived_type: 'thumbnails', 'transcriptions', 'ocr', 'captions'
            parent_hash: hash del archivo original al que pertenece
            content: bytes del derivado
            extension: extensión
            mime_type: tipo MIME
        """
        ext = extension.lstrip(".")
        key = f"{derived_type}/{parent_hash}.{ext}"
        stream = io.BytesIO(content)

        self.client.put_object(
            bucket_name=self.bucket_derived,
            object_name=key,
            data=stream,
            length=len(content),
            content_type=mime_type,
        )

        return {
            "key": key,
            "bucket": self.bucket_derived,
            "size_bytes": len(content),
        }

    # ---------------------------------------------------------------
    # Recuperación
    # ---------------------------------------------------------------

    def get(self, bucket: str, key: str) -> bytes:
        """Recupera el contenido de un archivo."""
        response = self.client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def exists(self, bucket: str, key: str) -> bool:
        """Verifica si una key existe."""
        try:
            self.client.stat_object(bucket, key)
            return True
        except S3Error:
            return False

    def get_presigned_url(
        self,
        bucket: str,
        key: str,
        expires_seconds: int = 3600,
    ) -> str:
        """
        URL temporal para mostrar el archivo (e.g. en frontend).

        Útil para que el panel muestre imágenes/audios sin proxy.
        """
        from datetime import timedelta

        return self.client.presigned_get_object(
            bucket_name=bucket,
            object_name=key,
            expires=timedelta(seconds=expires_seconds),
        )

    def delete(self, bucket: str, key: str) -> None:
        """Elimina un objeto. Cuidado: irreversible."""
        self.client.remove_object(bucket, key)
        logger.warning("vault_object_deleted", bucket=bucket, key=key)

    # ---------------------------------------------------------------
    # Listado y stats
    # ---------------------------------------------------------------

    def list_objects(self, bucket: str, prefix: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Lista objetos en un bucket con un prefix dado."""
        objects = self.client.list_objects(bucket, prefix=prefix, recursive=True)
        result = []
        for i, obj in enumerate(objects):
            if i >= limit:
                break
            result.append(
                {
                    "key": obj.object_name,
                    "size_bytes": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                    "etag": obj.etag,
                }
            )
        return result

    def stats(self) -> dict[str, Any]:
        """Stats globales del Vault: cantidad y tamaño por bucket."""
        result = {}
        for bucket in [self.bucket_raw, self.bucket_derived, "exports"]:
            try:
                if not self.client.bucket_exists(bucket):
                    result[bucket] = {"exists": False}
                    continue
                objs = list(self.client.list_objects(bucket, recursive=True))
                total_size = sum(o.size for o in objs)
                result[bucket] = {
                    "exists": True,
                    "object_count": len(objs),
                    "total_size_bytes": total_size,
                    "total_size_mb": round(total_size / 1024**2, 2),
                }
            except Exception as e:
                result[bucket] = {"exists": False, "error": str(e)}
        return result


def get_vault() -> VaultStorage:
    """Factory para usar como dependency en FastAPI."""
    return VaultStorage()
