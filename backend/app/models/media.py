"""
Models del schema `media`: metadata de archivos del Vault.

Los binarios viven en MinIO. Acá guardamos dónde están y qué son.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Attachment(Base):
    """
    Metadata de un archivo adjunto del Vault.

    El binario real está en MinIO en `minio_path`.
    SHA-256 permite deduplicación automática.
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_media_attachments_item_id", "item_id"),
        Index("ix_media_attachments_sha256", "sha256"),
        {"schema": "media"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.items.id"), nullable=False
    )
    # audio / imagen / video / documento / sticker / gif
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    filename_original: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Path completo en MinIO: {bucket}/{source}/{año}/{mes}/{tipo}/{sha256}.{ext}
    minio_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    # SHA-256 del archivo para deduplicación
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tamanio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # False = pendiente de procesar
    procesado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nivel_procesamiento: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    item: Mapped["Item"] = relationship("Item", back_populates="attachments")  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<Attachment {self.tipo} sha256={self.sha256[:8]}...>"
