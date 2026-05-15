"""
Models de SQLAlchemy para SecondBrain.

Importar desde acá para que Alembic detecte todos los models.
"""

from app.models.core import Conversacion, Empresa, Item, Persona
from app.models.media import Attachment
from app.models.processing import Job
from app.models.tagging import Fact, Mencion, Promesa, Transaccion

__all__ = [
    "Persona",
    "Conversacion",
    "Empresa",
    "Item",
    "Attachment",
    "Job",
    "Fact",
    "Promesa",
    "Transaccion",
    "Mencion",
]
