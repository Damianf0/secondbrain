"""
Sesión de SQLAlchemy.

Engine y SessionLocal para usar en endpoints como dependency.
Sprint 0: setup mínimo funcional.
Sprints siguientes: agregar models y migrations reales.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# Engine síncrono. En sprints futuros podemos sumar async si hace falta.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,  # poner True para debuggear queries SQL
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base declarativa para todos los models de SQLAlchemy."""

    pass


def get_db() -> Generator[Session, None, None]:
    """
    Dependency de FastAPI: provee una sesión de DB y la cierra al terminar.

    Uso:
        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
