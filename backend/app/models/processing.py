"""
Models del schema `processing`: cola de jobs y su historial.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Job(Base):
    """
    Job de procesamiento en la cola.

    Estados: pendiente → en_proceso → completado / fallido
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_processing_jobs_estado", "estado"),
        Index("ix_processing_jobs_tipo", "tipo"),
        Index("ix_processing_jobs_item_id", "item_id"),
        {"schema": "processing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # whatsapp_import / embed / transcribe / tag / ocr / caption
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    # pendiente / en_proceso / completado / fallido
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente")
    # Item asociado (opcional — algunos jobs son de batch sin item específico)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.items.id"), nullable=True
    )
    # Parámetros del job (modelo a usar, opciones, etc.)
    parametros: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Resultado cuando completa
    resultado: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Mensaje de error si falla
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Job {self.tipo} [{self.estado}]>"
