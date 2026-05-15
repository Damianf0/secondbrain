"""
Models del pipeline de tagging (Sprint 3): los "artefactos" que el tagger
extrae de cada `Item`.

- Fact:        hecho / dato / evento extraído ("Esteban entrega el listado el viernes")
- Promesa:     compromiso de entregar o hacer algo
- Transaccion: mención de plata (pago, presupuesto, deuda)
- Mencion:     entidad (persona/empresa) nombrada en el texto, con su resolución canónica

Al re-taggear un Item se borran sus artefactos previos y se vuelven a crear
(dedup simple por item_id).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Fact(Base):
    """Hecho / dato extraído de un mensaje."""

    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_core_facts_item_id", "item_id"),
        Index("ix_core_facts_persona_id", "persona_id"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.items.id", ondelete="CASCADE"), nullable=False
    )
    # Persona principal a la que refiere el hecho (si aplica)
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.personas.id"), nullable=True
    )
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    # evento / estado / preferencia / dato / ... — libre
    tipo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Si el hecho refiere a una fecha concreta (no se resuelve en v1, queda como texto en datos)
    fecha_referida: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confianza: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Promesa(Base):
    """Compromiso de entregar / hacer algo (de Damian o de un tercero)."""

    __tablename__ = "promesas"
    __table_args__ = (
        Index("ix_core_promesas_item_id", "item_id"),
        Index("ix_core_promesas_persona_id", "persona_id"),
        Index("ix_core_promesas_estado", "estado"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.items.id", ondelete="CASCADE"), nullable=False
    )
    # Quién se compromete (Persona canónica si se resolvió)
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.personas.id"), nullable=True
    )
    # Si la promesa es de Damian (yo) hacia un tercero
    es_de_damian: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    plazo_texto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plazo_fecha: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # pendiente / cumplida / incumplida / cancelada
    estado: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pendiente")
    confianza: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Transaccion(Base):
    """Mención de dinero: pago, presupuesto, deuda, ingreso."""

    __tablename__ = "transacciones"
    __table_args__ = (
        Index("ix_core_transacciones_item_id", "item_id"),
        Index("ix_core_transacciones_persona_id", "persona_id"),
        Index("ix_core_transacciones_tipo", "tipo"),
        Index("ix_core_transacciones_fecha", "fecha"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.items.id", ondelete="CASCADE"), nullable=False
    )
    # Contraparte (a quién le pagué / quién me pagó), si se resolvió
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.personas.id"), nullable=True
    )
    # Monto parseado a número si se pudo; si no, queda solo `monto_raw`
    monto: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    monto_raw: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ARS / USD / ...
    concepto: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # ingreso / egreso / presupuesto / deuda
    tipo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Cuándo se mencionó (= fecha del Item)
    fecha: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confianza: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Mencion(Base):
    """Entidad (persona/empresa) nombrada en un mensaje, con su resolución canónica."""

    __tablename__ = "menciones"
    __table_args__ = (
        Index("ix_core_menciones_item_id", "item_id"),
        Index("ix_core_menciones_persona_id", "persona_id"),
        Index("ix_core_menciones_empresa_id", "empresa_id"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.items.id", ondelete="CASCADE"), nullable=False
    )
    # 'persona' | 'empresa'
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    nombre_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.personas.id"), nullable=True
    )
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.empresas.id"), nullable=True
    )
    resuelto: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
