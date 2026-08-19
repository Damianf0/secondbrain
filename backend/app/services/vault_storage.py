"""
Storage del Vault — filesystem local.

Reemplaza el backend anterior (MinIO/S3) por un volumen Docker simple.
Para un vault de un solo usuario, un object storage S3-compatible completo
era más infraestructura de la que hacía falta — un directorio con la misma
convención de paths alcanza, y saca un contenedor entero del stack.

Convenciones (idénticas a las de MinIO, para no tocar el modelo de datos):
    - "raw"     : archivos originales (audios .opus, imágenes, PDFs, etc.)
    - "derived" : derivados (thumbnails, transcripciones, OCR results)
    - "exports" : exports manuales (.txt de WhatsApp)

Estructura de keys dentro de "raw":
    {fuente}/{año}/{mes}/{tipo}/{hash}.{ext}

Ejemplo:
    raw/whatsapp/2026/05/audios/a3f2b8c1...opus

`Attachment.minio_path` en la base sigue guardando "{bucket}/{key}" (el
nombre del campo quedó como está para no requerir una migración de Alembo
solo por un rename — lo que cambió es dónde vive el archivo, no el modelo).
"""

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_BUCKETS = ("raw", "derived", "exports")


class VaultStorageError(Exception):
    """Error genérico del Vault (archivo no encontrado, etc.) — análogo a S3Error."""


class VaultStorage:
    """Servicio del Vault: guarda y recupera archivos en disco local."""

    def __init__(self) -> None:
        self.root = Path(settings.vault_path)
        self.bucket_raw = "raw"
        self.bucket_derived = "derived"

    def _path(self, bucket: str, key: str) -> Path:
        # normpath implícito de Path evita que un key con ".." se escape del bucket
        p = (self.root / bucket / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise VaultStorageError(f"key fuera del vault: {bucket}/{key}")
        return p

    # ---------------------------------------------------------------
    # Health
    # ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Verifica que el directorio del Vault existe y es escribible."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".health_probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            buckets = [b for b in _BUCKETS if (self.root / b).is_dir()]
            return {
                "ok": True,
                "path": str(self.root),
                "buckets": buckets,
                "bucket_raw_exists": self.bucket_raw in buckets,
                "bucket_derived_exists": self.bucket_derived in buckets,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("vault_health_check_failed", error=str(e))
            return {"ok": False, "path": str(self.root), "error": str(e)}

    def ensure_buckets(self) -> dict[str, bool]:
        """Crea las carpetas del Vault si no existen. Idempotente."""
        result = {}
        for bucket in _BUCKETS:
            path = self.root / bucket
            existia = path.is_dir()
            path.mkdir(parents=True, exist_ok=True)
            result[bucket] = not existia
            if not existia:
                logger.info("vault_bucket_created", bucket=bucket)
        return result

    # ---------------------------------------------------------------
    # Storage de archivos crudos
    # ---------------------------------------------------------------

    @staticmethod
    def _hash_bytes(content: bytes) -> str:
        import hashlib

        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _build_raw_key(
        source: str,
        media_type: str,
        content_hash: str,
        extension: str,
        ts: datetime | None = None,
    ) -> str:
        """Formato: {source}/{año}/{mes}/{media_type}/{hash}.{ext}"""
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
        Guarda un archivo crudo en "raw", con deduplicación por hash.

        Returns:
            dict con 'hash', 'key', 'bucket', 'size_bytes', 'mime_type', 'duplicate'
        """
        content_hash = self._hash_bytes(content)
        key = self._build_raw_key(source, media_type, content_hash, extension, ts)
        path = self._path(self.bucket_raw, key)

        is_duplicate = path.exists()
        if is_duplicate:
            logger.info("vault_duplicate_detected", key=key, hash=content_hash)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
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
        """Guarda un derivado (thumbnail, transcripción, OCR result, etc)."""
        ext = extension.lstrip(".")
        key = f"{derived_type}/{parent_hash}.{ext}"
        path = self._path(self.bucket_derived, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        return {"key": key, "bucket": self.bucket_derived, "size_bytes": len(content)}

    # ---------------------------------------------------------------
    # Recuperación
    # ---------------------------------------------------------------

    def get(self, bucket: str, key: str) -> bytes:
        """Recupera el contenido de un archivo."""
        path = self._path(bucket, key)
        if not path.is_file():
            raise VaultStorageError(f"no existe: {bucket}/{key}")
        return path.read_bytes()

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).is_file()

    def get_presigned_url(
        self,
        bucket: str,
        key: str,
        expires_seconds: int = 3600,  # sin efecto -- no hay expiración real en filesystem
    ) -> str:
        """
        URL para mostrar el archivo (e.g. en frontend).

        A diferencia de MinIO, no es una URL firmada con expiración real —
        es un link directo al endpoint /api/vault/file del propio backend,
        que ya es accesible desde el browser (mismo puerto que el resto de
        la API). `expires_seconds` queda solo por compatibilidad de firma.
        """
        return f"{settings.vault_public_base_url}/api/vault/file/{bucket}/{quote(key)}"

    def delete(self, bucket: str, key: str) -> None:
        """Elimina un archivo. Cuidado: irreversible."""
        path = self._path(bucket, key)
        path.unlink(missing_ok=True)
        logger.warning("vault_object_deleted", bucket=bucket, key=key)

    # ---------------------------------------------------------------
    # Listado y stats
    # ---------------------------------------------------------------

    def list_objects(self, bucket: str, prefix: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Lista archivos en un bucket con un prefix dado (relativo al bucket)."""
        base = self._path(bucket, prefix) if prefix else self.root / bucket
        result: list[dict[str, Any]] = []
        if not base.exists():
            return result
        search_root = base if base.is_dir() else base.parent
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            if len(result) >= limit:
                break
            stat = path.stat()
            result.append(
                {
                    "key": str(path.relative_to(self.root / bucket)).replace("\\", "/"),
                    "size_bytes": stat.st_size,
                    "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "etag": None,
                }
            )
        return result

    def stats(self) -> dict[str, Any]:
        """Stats globales del Vault: cantidad y tamaño por bucket."""
        result = {}
        for bucket in _BUCKETS:
            path = self.root / bucket
            if not path.is_dir():
                result[bucket] = {"exists": False}
                continue
            files = [p for p in path.rglob("*") if p.is_file()]
            total_size = sum(p.stat().st_size for p in files)
            result[bucket] = {
                "exists": True,
                "object_count": len(files),
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / 1024**2, 2),
            }
        return result

    # ---------------------------------------------------------------
    # Servir un archivo (usado por el router /api/vault/file)
    # ---------------------------------------------------------------

    def resolve_for_serving(self, bucket: str, key: str) -> tuple[Path, str]:
        """Devuelve (path, content_type) para que el router lo sirva con FileResponse."""
        if bucket not in _BUCKETS:
            raise VaultStorageError(f"bucket inválido: {bucket}")
        path = self._path(bucket, key)
        if not path.is_file():
            raise VaultStorageError(f"no existe: {bucket}/{key}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return path, content_type


def get_vault() -> VaultStorage:
    """Factory para usar como dependency en FastAPI."""
    return VaultStorage()
