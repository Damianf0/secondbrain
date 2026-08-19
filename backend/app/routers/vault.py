"""Router del Vault (storage de archivos crudos).

Un solo endpoint: sirve los archivos guardados en disco por VaultStorage
(ver app/services/vault_storage.py). Reemplaza las URLs firmadas de MinIO --
como el backend ya es accesible desde el browser (mismo puerto que el resto
de la API), no hace falta firmar nada ni reescribir hosts internos de Docker.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.vault_storage import VaultStorage, VaultStorageError

router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.get("/file/{bucket}/{key:path}")
def servir_archivo(bucket: str, key: str) -> FileResponse:
    """Sirve un archivo del Vault por bucket/key (equivalente al presigned URL de MinIO)."""
    vault = VaultStorage()
    try:
        path, content_type = vault.resolve_for_serving(bucket, key)
    except VaultStorageError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(path, media_type=content_type)
