"""
Models del schema `core`: entidades principales del sistema.

- Persona: contacto canónico (resuelve alias, teléfonos, emails)
- Conversacion: chat 1:1 o grupo, con flag para seguir/ignorar
- Item: unidad mínima de información (mensaje, email, nota, etc.)
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Persona(Base):
    """
    Contacto canónico. Resuelve el problema de entity resolution:
    "Juan", "Juan P", "+54 9 XXX..." → misma persona.
    """

    __tablename__ = "personas"
    __table_args__ = (
        Index("ix_core_personas_telefono", "telefono"),
        Index("ix_core_personas_email", "email"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nombre_canonico: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    # Lista de strings: cómo aparece en distintas fuentes
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    telefono: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # yo / contacto / desconocido (los grupos son `Conversacion`, no `Persona`)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False, default="contacto")
    # Opt-in: por defecto NO se sigue. Damian elige a quién indexar en el pipeline.
    # (la Persona `tipo=yo` se crea con seguir=True explícitamente)
    seguir: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    items: Mapped[list["Item"]] = relationship("Item", back_populates="persona")

    def __repr__(self) -> str:
        return f"<Persona {self.nombre_canonico!r}>"


class Conversacion(Base):
    """
    Hilo de conversación: un chat 1:1, un grupo de WhatsApp, un thread de Gmail.

    El `conversation_id` es el identificador estable (teléfono E.164 para 1:1,
    JID `@g.us` para grupos, thread_id de Gmail) y matchea `Item.conversation_id`.
    Cuando llega un mensaje nuevo, el bridge/importador hace upsert acá para
    tener metadata humana (nombre) y el flag `seguir`.
    """

    __tablename__ = "conversaciones"
    __table_args__ = (
        Index("ix_core_conversaciones_tipo_seguir", "tipo", "seguir"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Identificador estable que aparece en Item.conversation_id
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # 1on1 / grupo / difusion / email / desconocido
    tipo: Mapped[str] = mapped_column(String(30), nullable=False, default="1on1")
    # Nombre para mostrar (nombre del grupo, nombre canónico del contacto, asunto del thread)
    nombre_display: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Si se procesa o se ignora silenciosamente. Default true (capturamos todo, decidimos después).
    seguir: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Metadata extra (participantes conocidos del grupo, JID raw, fuente, etc.)
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Conversacion {self.tipo}:{self.conversation_id!r}>"


class Empresa(Base):
    """
    Empresa / organización canónica (cliente, proveedor, institución).

    Igual que `Persona`, resuelve aliases. Se crea/enriquece cuando el tagger
    detecta una empresa mencionada en un mensaje.
    """

    __tablename__ = "empresas"
    __table_args__ = (
        Index("ix_core_empresas_seguir", "seguir"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nombre_canonico: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # cliente / proveedor / institucion / otra — libre
    tipo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # opt-in, igual que personas
    seguir: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Empresa {self.nombre_canonico!r}>"


class Item(Base):
    """
    Unidad mínima de información del Vault.

    Puede ser un mensaje de WhatsApp, un email, una nota manual, etc.
    El contenido real de archivos (audios, imágenes) vive en MinIO;
    acá solo guardamos metadata y texto.
    """

    __tablename__ = "items"
    __table_args__ = (
        Index("ix_core_items_fecha", "fecha"),
        Index("ix_core_items_source_conversation", "source", "conversation_id"),
        Index("ix_core_items_persona_id", "persona_id"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Fuente: whatsapp / gmail / telegram / manual
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    # ID original en la fuente (message_id de Gmail, etc.). Puede ser None para WhatsApp txt.
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Identificador del hilo/chat: número de teléfono, nombre del grupo, thread_id de Gmail
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Quién envió el mensaje
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.personas.id"), nullable=True
    )
    # mensaje / email / llamada / nota / sistema
    tipo: Mapped[str] = mapped_column(String(30), nullable=False, default="mensaje")
    # Texto del mensaje. Para media sin caption puede ser vacío.
    contenido: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Cuándo se envió originalmente
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # entrante (lo recibí) / saliente (lo envié) / sistema (mensaje del sistema)
    direccion: Mapped[str] = mapped_column(String(20), nullable=False, default="entrante")
    # True si el mensaje es un adjunto (imagen, audio, video, doc, sticker)
    es_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # audio / imagen / video / documento / sticker / gif
    media_tipo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Nivel de procesamiento aplicado (0=raw, 1=básico, 2=diferido, 3=nocturno, 4=batch)
    nivel_procesamiento: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Análisis de tono (completado en nivel 2)
    tono: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Metadatos extra en JSON libre
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    persona: Mapped["Persona | None"] = relationship("Persona", back_populates="items")
    attachments: Mapped[list["Attachment"]] = relationship(  # type: ignore[name-defined]
        "Attachment", back_populates="item"
    )

    def __repr__(self) -> str:
        return f"<Item {self.source}:{self.id} fecha={self.fecha}>"
