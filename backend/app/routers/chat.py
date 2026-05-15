"""Router de chat / Q&A (Sprint 4).

  POST /api/chat          — pregunta en lenguaje natural → respuesta + fuentes
  POST /api/chat/retrieve — solo recupera fragmentos (debug del retriever)
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.services import chat as chat_service
from app.services.retriever import recuperar

logger = get_logger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    pregunta: str
    k_messages: int = 12
    k_facts: int = 8
    model: str | None = None
    persona_id: str | None = None
    conversation_id: str | None = None
    fecha_desde: str | None = None  # ISO 8601
    fecha_hasta: str | None = None  # ISO 8601


@router.post("")
def chat(req: ChatRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return chat_service.responder(
        db,
        req.pregunta,
        k_messages=max(0, min(req.k_messages, 30)),
        k_facts=max(0, min(req.k_facts, 30)),
        model=req.model,
        persona_id=req.persona_id,
        conversation_id=req.conversation_id,
        fecha_desde=req.fecha_desde,
        fecha_hasta=req.fecha_hasta,
    )


@router.post("/retrieve")
def retrieve(req: ChatRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    frags = recuperar(
        db,
        req.pregunta,
        k_messages=max(0, min(req.k_messages, 30)),
        k_facts=max(0, min(req.k_facts, 30)),
        persona_id=req.persona_id,
        conversation_id=req.conversation_id,
        fecha_desde=req.fecha_desde,
        fecha_hasta=req.fecha_hasta,
    )
    return {"pregunta": req.pregunta, "n": len(frags), "fragmentos": frags}
