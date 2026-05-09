"""
Configuración de logging con structlog.

Logs estructurados en JSON para que sean parseable por
herramientas de observabilidad. En modo dev, salida humana.
"""

import logging
import sys

import structlog

from app.config import get_settings


def configure_logging() -> None:
    """Configura logging para toda la aplicación."""
    settings = get_settings()

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Configuración base de logging stdlib
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Procesadores comunes para todos los logs
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Obtener un logger estructurado."""
    return structlog.get_logger(name)
