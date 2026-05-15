"""sprint 2.5: personas.seguir default false (opt-in)

Revision ID: 6cdb5bcf75e8
Revises: 580e06d66386
Create Date: 2026-05-11 20:22:22.930125

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6cdb5bcf75e8'
down_revision: str | None = '580e06d66386'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Opt-in: el default pasa a FALSE. Damian elige a quién seguir.
    op.alter_column("personas", "seguir", server_default=sa.text("false"), schema="core")
    # Flip de los contactos ya cargados (excepto la Persona "yo")
    op.execute("UPDATE core.personas SET seguir = false WHERE tipo <> 'yo'")
    op.execute("UPDATE core.personas SET seguir = true WHERE tipo = 'yo'")


def downgrade() -> None:
    op.alter_column("personas", "seguir", server_default=sa.text("true"), schema="core")
    op.execute("UPDATE core.personas SET seguir = true")
