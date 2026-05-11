"""
Models de SQLAlchemy para SecondBrain.

Importar desde acá para que Alembic detecte todos los models.
"""

from app.models.core import Item, Persona
from app.models.media import Attachment
from app.models.processing import Job

__all__ = ["Persona", "Item", "Attachment", "Job"]
