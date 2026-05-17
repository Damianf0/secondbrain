"""
A/B test entre qwen3-embedding:4b y bge-m3.

Crea collections paralelas en Qdrant para no romper las que están en producción.
Toma 1000 items recientes + 100 facts, embebe con bge-m3, e itera un set de
queries comparando rankings y scores entre los dos modelos.

Pensado para correr DENTRO del container backend (que ya tiene los wrappers):

  docker compose exec backend python /app/scripts/ab_embedding.py

NO toca las collections `messages` ni `facts` originales.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.core import Conversacion, Item, Persona
from app.models.tagging import Fact
from app.services.ollama_client import OllamaService
from app.services.qdrant_client import QdrantService


CHALLENGER_MODEL = "bge-m3"
CHALLENGER_DIM = 1024
INCUMBENT_MODEL = "qwen3-embedding:4b"
INCUMBENT_DIM = 2560

COL_MESSAGES_V2 = "messages_v2_bgem3"
COL_FACTS_V2 = "facts_v2_bgem3"

SAMPLE_ITEMS = 1000
SAMPLE_FACTS = 200

# Queries de prueba: una mezcla de argentinismos coloquiales, queries de las 18
# de referencia y términos concretos.
TEST_QUERIES = [
    "que no le anda a Hernan",
    "que tengo que hacer esta semana",
    "que me prometieron entregar",
    "cuanto le debo a alguien",
    "problemas con el servidor",
    "facturas pendientes de pagar",
    "alguien me transfirio plata",
    "que paso con el SIAP",
    "reuniones para el lunes",
    "que necesita arreglar Mariela",
]

TOP_K = 8


def fmt_score(s: float) -> str:
    return f"{s:.4f}"


def banner(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def main() -> None:
    qd = QdrantService()
    oll = OllamaService()
    db = SessionLocal()

    # ----------------------------------------------------------
    # 1. Crear collections paralelas
    # ----------------------------------------------------------
    banner(f"[1/4] Creando collections {COL_MESSAGES_V2!r} y {COL_FACTS_V2!r}")
    qd.ensure_collection(COL_MESSAGES_V2, CHALLENGER_DIM, "Cosine")
    qd.ensure_collection(COL_FACTS_V2, CHALLENGER_DIM, "Cosine")
    print("  OK.")

    # ----------------------------------------------------------
    # 2. Embeber items
    # ----------------------------------------------------------
    banner(f"[2/4] Embebiendo {SAMPLE_ITEMS} items más recientes con {CHALLENGER_MODEL}")

    items = db.execute(
        select(Item)
        .where(Item.contenido.isnot(None))
        .order_by(Item.fecha.desc())
        .limit(SAMPLE_ITEMS)
    ).scalars().all()

    print(f"  Cargados {len(items)} items.")

    t0 = time.time()
    puntos = []
    errores = 0
    for i, item in enumerate(items, 1):
        cuerpo = (item.contenido or "").strip()
        if not cuerpo:
            continue
        # Mismo prefijo que el embedder de producción para comparar justo
        sender = db.get(Persona, item.persona_id) if item.persona_id else None
        sender_name = (sender.nombre_canonico if sender else (item.datos or {}).get("sender_name")) or "alguien"
        prefijo = "Damian" if item.direccion == "saliente" else sender_name
        text = f"{prefijo}: {cuerpo[:8000]}"
        try:
            vec = oll.embed(text, model=CHALLENGER_MODEL)["embedding"]
        except Exception as e:  # noqa: BLE001
            errores += 1
            if errores <= 3:
                print(f"  ERROR embed item {item.id}: {e}")
            continue

        conv = db.execute(
            select(Conversacion).where(Conversacion.conversation_id == item.conversation_id)
        ).scalars().first()
        chat_name = (conv.nombre_display if conv else None) or item.conversation_id

        puntos.append({
            "id": str(item.id),
            "vector": vec,
            "payload": {
                "kind": "message",
                "item_id": str(item.id),
                "conversation_id": item.conversation_id,
                "conversation_nombre": chat_name,
                "persona_nombre": sender_name,
                "direccion": item.direccion,
                "fecha": item.fecha.isoformat() if item.fecha else None,
                "texto": cuerpo[:2000],
            },
        })

        if i % 100 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            print(f"  [{i}/{len(items)}] {rate:.1f} items/s · {elapsed:.0f}s transcurridos")

    if puntos:
        qd.upsert_points(COL_MESSAGES_V2, puntos)
    total_time = time.time() - t0
    print(f"  Embebidos {len(puntos)} en {total_time:.0f}s ({len(puntos)/total_time:.1f} items/s)  errores={errores}")

    # ----------------------------------------------------------
    # 3. Embeber facts
    # ----------------------------------------------------------
    banner(f"[3/4] Embebiendo facts (top {SAMPLE_FACTS}) con {CHALLENGER_MODEL}")

    facts = db.execute(
        select(Fact)
        .order_by(Fact.created_at.desc())
        .limit(SAMPLE_FACTS)
    ).scalars().all()

    print(f"  Cargados {len(facts)} facts.")

    t0 = time.time()
    fact_puntos = []
    for f in facts:
        texto = (f.texto or "").strip()
        if not texto:
            continue
        try:
            vec = oll.embed(texto, model=CHALLENGER_MODEL)["embedding"]
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR embed fact {f.id}: {e}")
            continue

        item = db.get(Item, f.item_id) if f.item_id else None
        conv_name = "?"
        if item:
            conv = db.execute(
                select(Conversacion).where(Conversacion.conversation_id == item.conversation_id)
            ).scalars().first()
            conv_name = (conv.nombre_display if conv else None) or item.conversation_id

        fact_puntos.append({
            "id": str(f.id),
            "vector": vec,
            "payload": {
                "kind": "fact",
                "fact_id": str(f.id),
                "item_id": str(f.item_id) if f.item_id else None,
                "conversation_nombre": conv_name,
                "tipo": f.tipo,
                "texto": texto[:2000],
            },
        })

    if fact_puntos:
        qd.upsert_points(COL_FACTS_V2, fact_puntos)
    print(f"  Embebidos {len(fact_puntos)} facts en {time.time()-t0:.0f}s")

    # ----------------------------------------------------------
    # 4. Comparar queries
    # ----------------------------------------------------------
    banner(f"[4/4] Comparando {len(TEST_QUERIES)} queries entre {INCUMBENT_MODEL} y {CHALLENGER_MODEL}")

    # IMPORTANTE: como `messages_v2_bgem3` solo tiene 1000 items vs los 75K
    # de la collection original, no es 100% manzana-vs-manzana. Pero los
    # 1000 son los más recientes (incluyen los items del backfill del tagger),
    # que son los más relevantes para validar las queries del POC.
    # Hacemos overfetch=50 en la original y filtramos a los del sample.
    ids_sample = set(p["id"] for p in puntos)

    print(f"\n  (Búsqueda comparada: {len(ids_sample)} items en sample paralelo. Score = cosine.)\n")

    for q in TEST_QUERIES:
        print(f"\n  ── QUERY: {q!r}")

        # Embed con ambos modelos
        try:
            vec_inc = oll.embed(q, model=INCUMBENT_MODEL, force_cpu=False)["embedding"]
        except Exception as e:  # noqa: BLE001
            print(f"     incumbent embed FAIL: {e}")
            continue
        try:
            vec_ch = oll.embed(q, model=CHALLENGER_MODEL)["embedding"]
        except Exception as e:  # noqa: BLE001
            print(f"     challenger embed FAIL: {e}")
            continue

        # Búsqueda amplia en incumbent (75K items) y filtro a los IDs del sample en Python.
        raw_inc = qd.search("messages", vec_inc, limit=200)
        hits_inc = [h for h in raw_inc if h["id"] in ids_sample][:TOP_K]
        hits_ch = qd.search(COL_MESSAGES_V2, vec_ch, limit=TOP_K)

        # Render lado a lado
        print(f"     {INCUMBENT_MODEL} (qwen3-embedding:4b, 2560 dim):")
        for i, h in enumerate(hits_inc[:5], 1):
            p = h["payload"]
            texto = (p.get("texto") or "")[:80].replace("\n", " ")
            who = p.get("persona_nombre") or "?"
            chat = (p.get("conversation_nombre") or "?")[:25]
            print(f"       {i}. {fmt_score(h['score'])}  {who[:18]:18} | {chat:25} | {texto}")
        print(f"     {CHALLENGER_MODEL} (1024 dim):")
        for i, h in enumerate(hits_ch[:5], 1):
            p = h["payload"]
            texto = (p.get("texto") or "")[:80].replace("\n", " ")
            who = p.get("persona_nombre") or "?"
            chat = (p.get("conversation_nombre") or "?")[:25]
            print(f"       {i}. {fmt_score(h['score'])}  {who[:18]:18} | {chat:25} | {texto}")

    db.close()
    banner("DONE")
    print(f"Collections temporales en Qdrant: {COL_MESSAGES_V2}, {COL_FACTS_V2}")
    print("(Borrarlas después de decidir: qd.delete_collection(...))")


if __name__ == "__main__":
    main()
