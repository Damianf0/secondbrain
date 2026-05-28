#!/usr/bin/env python3
"""
Benchmark de modelos LLM locales para el tagger de SecondBrain.

Corre un prompt de extracción estructurada sobre varios mensajes de prueba
(español argentino, casos típicos: promesa, reclamo, entidades, gasto) contra
cada modelo de Ollama y mide:
  - latencia total / tokens por segundo / tiempo de carga del modelo
  - si la salida es JSON válido (la usaríamos parseada)
  - reparto CPU/GPU (de `ollama ps`, aproximado)

Uso (dentro del container backend, que tiene httpx y ve a `ollama`):
    docker cp scripts/benchmark_tagger.py secondbrain-backend-1:/tmp/bench.py
    docker compose exec backend uv run python /tmp/bench.py

Variables de entorno:
    OLLAMA_URL  (default http://ollama:11434)
    BENCH_MODELS  (lista separada por comas; default: los 6 candidatos)
    BENCH_OUT  (archivo donde dumpear las salidas completas; default /tmp/bench_outputs.md)
"""

import json
import os
import re
import time

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MODELS = [
    m.strip()
    for m in os.getenv(
        "BENCH_MODELS",
        "aya-expanse:8b,qwen3:8b,qwen3:4b,gemma3:4b,gemma4:e2b,gemma4:e4b",
    ).split(",")
    if m.strip()
]
OUT_FILE = os.getenv("BENCH_OUT", "/tmp/bench_outputs.md")
# Si se setea (a "1"/"true"), manda `think: false` en /api/generate para
# desactivar el reasoning de los modelos thinking (qwen3, etc.).
THINK_OFF = os.getenv("BENCH_THINK_OFF", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Prompt del tagger
# ---------------------------------------------------------------------------

SYSTEM = """Sos un analista que extrae información estructurada de mensajes de WhatsApp para un sistema de memoria personal privado. El dueño del sistema es Damian (una persona técnica con varios clientes, entre ellos una clínica).

Para el mensaje que te paso, devolvé SOLAMENTE un objeto JSON (sin texto antes ni después, sin markdown) con esta estructura exacta:

{
  "resumen": "una frase corta de qué trata el mensaje",
  "personas_mencionadas": ["nombres de personas nombradas en el texto, distintas del remitente"],
  "empresas_mencionadas": ["empresas/organizaciones nombradas"],
  "promesas": [{"quien": "quién se compromete", "que": "qué entrega o hace", "cuando": "plazo si lo hay, si no null"}],
  "transacciones": [{"monto": "número", "moneda": "ARS|USD|otro", "concepto": "de qué", "tipo": "ingreso|egreso|presupuesto"}],
  "tareas": ["acciones concretas que Damian debería hacer, si surgen del mensaje"],
  "tono": "uno de: cordial, formal, urgente, tenso, agresivo, pasivo-agresivo, afectuoso, informativo, humoristico",
  "sentimiento": {"polaridad": "positivo|neutro|negativo", "intensidad": 0.0},
  "marcadores": ["lista de: contiene_reclamo, contiene_disculpa, contiene_promesa, contiene_pregunta, urgente, contiene_monto"],
  "confianza": 0.0
}

Reglas: si un campo no aplica, devolvé lista vacía []. No inventes datos que no estén en el mensaje. `intensidad` y `confianza` van entre 0 y 1. Respondé en español. SOLO el JSON."""

TEST_MESSAGES = [
    {
        "id": "promesa_y_monto",
        "sender": "Juan Pérez",
        "chat": "Juan Pérez",
        "body": "Buenas, te confirmo: el viernes a primera hora te paso el listado de turnos actualizado. Y el mantenimiento mensual del bot lo dejamos en $45000 como hablamos, te lo deposito a fin de mes.",
    },
    {
        "id": "reclamo_urgente",
        "sender": "Marcelo (cliente alarmas)",
        "chat": "Marcelo Alarcón",
        "body": "Necesito el sitio andando HOY. Ya van dos semanas que me decís que está listo y no funciona nada, los clientes me llaman. Esto no puede seguir así, mañana lo necesito sí o sí.",
    },
    {
        "id": "entidades_casual",
        "sender": "Lucho",
        "chat": "Lucho Pérez",
        "body": "Che boludo, ayer me crucé a Flor en lo de Aquaro y me comentó que el evento de Grupo Insigne se pasó para el 20. Avisale a Agustina así reagenda. Un abrazo!",
    },
    {
        "id": "gasto_propio",
        "sender": "Damian Orozco",
        "chat": "Damian Orozco",
        "body": "Anoté: pagué la suscripción de Claude, ahora son USD 200 por mes. Y renové el dominio ejemplo.com, otros $15000 hasta el año que viene.",
    },
]

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str):
    """Intenta sacar el primer objeto JSON del texto (tolera 'thinking' preambles)."""
    if not text:
        return None
    # Sacar bloques <think>...</think> si los hay (qwen3)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    # Sacar fences ```json ... ```
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    m = JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # a veces hay basura después del último }
        candidate = m.group(0)
        for end in range(len(candidate), 0, -1):
            try:
                return json.loads(candidate[:end])
            except json.JSONDecodeError:
                continue
    return None


def ollama_ps(client: httpx.Client) -> str:
    try:
        r = client.get(f"{OLLAMA_URL}/api/ps", timeout=10)
        models = r.json().get("models", [])
        parts = []
        for m in models:
            size_vram = m.get("size_vram", 0)
            size = m.get("size", 1) or 1
            pct_gpu = round(100 * size_vram / size)
            parts.append(f"{m.get('name')}: {pct_gpu}% GPU")
        return "; ".join(parts) or "(nada cargado)"
    except Exception as e:  # noqa: BLE001
        return f"(error ps: {e})"


def run_one(client: httpx.Client, model: str, msg: dict) -> dict:
    prompt = f"Remitente: {msg['sender']}\nChat: {msg['chat']}\nMensaje: \"{msg['body']}\""
    body = {
        "model": model,
        "system": SYSTEM,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1024},
    }
    if THINK_OFF:
        body["think"] = False
    t0 = time.time()
    try:
        r = client.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=600)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "wall_s": round(time.time() - t0, 1)}
    wall = time.time() - t0
    resp = data.get("response", "")
    parsed = extract_json(resp)
    eval_count = data.get("eval_count", 0) or 0
    eval_dur = (data.get("eval_duration", 0) or 0) / 1e9
    return {
        "wall_s": round(wall, 1),
        "load_s": round((data.get("load_duration", 0) or 0) / 1e9, 1),
        "eval_count": eval_count,
        "tok_s": round(eval_count / eval_dur, 1) if eval_dur > 0 else 0,
        "json_ok": parsed is not None,
        "parsed": parsed,
        "raw_len": len(resp),
        "raw": resp,
    }


def main() -> None:
    print(f"Ollama: {OLLAMA_URL}")
    print(f"Modelos: {MODELS}")
    print(f"think: {'OFF (think=false)' if THINK_OFF else 'default'}\n")
    client = httpx.Client()

    results: dict[str, list] = {}
    outputs_md = ["# Benchmark tagger — salidas completas\n"]

    for model in MODELS:
        print(f"\n{'='*70}\n  {model}\n{'='*70}")
        outputs_md.append(f"\n\n## {model}\n")
        results[model] = []
        for msg in TEST_MESSAGES:
            print(f"  → {msg['id']} ... ", end="", flush=True)
            res = run_one(client, model, msg)
            results[model].append({"msg": msg["id"], **{k: v for k, v in res.items() if k not in ("parsed", "raw")}})
            if "error" in res:
                print(f"ERROR ({res['error'][:80]})")
                outputs_md.append(f"\n### {msg['id']}\n\nERROR: {res['error']}\n")
                continue
            print(
                f"{res['wall_s']}s | load {res['load_s']}s | {res['eval_count']} tok @ {res['tok_s']} tok/s | "
                f"JSON {'OK' if res['json_ok'] else 'FALLÓ'}"
            )
            outputs_md.append(f"\n### {msg['id']}  ·  {res['wall_s']}s · {res['tok_s']} tok/s · JSON {'OK' if res['json_ok'] else 'NO'}\n")
            if res["parsed"] is not None:
                outputs_md.append("```json\n" + json.dumps(res["parsed"], ensure_ascii=False, indent=2) + "\n```\n")
            else:
                outputs_md.append("Salida cruda (no parseó a JSON):\n```\n" + res["raw"][:2000] + "\n```\n")
        print(f"  ollama ps: {ollama_ps(client)}")

    # --- Resumen ---
    print(f"\n\n{'#'*70}\n  RESUMEN\n{'#'*70}")
    header = f"{'modelo':<20} {'avg s':>7} {'avg tok/s':>10} {'load 1ra':>9} {'JSON ok':>8} {'avg tok':>8}"
    print(header)
    print("-" * len(header))
    summary_rows = []
    for model, runs in results.items():
        ok_runs = [r for r in runs if "error" not in r]
        if not ok_runs:
            print(f"{model:<20} {'TODOS FALLARON':>40}")
            continue
        avg_wall = sum(r["wall_s"] for r in ok_runs) / len(ok_runs)
        avg_tok_s = sum(r["tok_s"] for r in ok_runs) / len(ok_runs)
        load1 = runs[0].get("load_s", 0)
        json_ok = sum(1 for r in ok_runs if r["json_ok"])
        avg_tok = sum(r["eval_count"] for r in ok_runs) / len(ok_runs)
        print(f"{model:<20} {avg_wall:>7.1f} {avg_tok_s:>10.1f} {load1:>9.1f} {json_ok:>4}/{len(runs):<3} {avg_tok:>8.0f}")
        summary_rows.append((model, avg_wall, avg_tok_s, load1, json_ok, len(runs), avg_tok))

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("".join(outputs_md))
    print(f"\nSalidas completas en {OUT_FILE}")

    # JSON machine-readable al final por si lo querés parsear
    print("\n---SUMMARY_JSON---")
    print(json.dumps([
        {"model": m, "avg_wall_s": round(w, 1), "avg_tok_s": round(t, 1), "load1_s": l, "json_ok": f"{j}/{n}", "avg_tokens": round(a)}
        for m, w, t, l, j, n, a in summary_rows
    ], ensure_ascii=False))


if __name__ == "__main__":
    main()
