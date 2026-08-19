# Hallazgos de `secondbrain-v2` (recuperados el 19 de agosto de 2026)

> **Por qué existe este documento**: la carpeta `secondbrain-v2/` (una segunda iteración de este
> proyecto, trabajada entre el 25 de mayo y fines de julio de 2026) se perdió del disco sin haber
> llegado a subirse a git en ningún momento. Se recuperó código parcial extrayéndolo de imágenes
> Docker que habían quedado en el equipo (`docker create` + `docker cp`, sin ejecutar nada). Este
> documento existe para que ese trabajo no se vuelva a perder — el resto del contexto completo
> (cómo se armó el diagnóstico, qué se auditó, timeline) está en el documento de rediseño de esta
> sesión, no reproducido acá por extensión.

## Qué se recuperó y de dónde

Docker Desktop conservaba 9 contenedores exited de un proyecto compose `secondbrain-v2` (mismos
8 servicios que este repo) y 3 imágenes propias (`secondbrain-v2-backend`, `-frontend`, `-bridge`)
construidas el 25 de mayo. Los datos (Postgres/Qdrant/MinIO) estaban en bind-mounts a
`C:\Dev\secondbrain\secondbrain-v2\data_v2\`, carpeta que sí está borrada — esos datos no se
recuperan. El **código** de `backend` y `frontend` estaba parcialmente recuperable (la imagen
guarda una foto del 25 de mayo; los ~2 meses de edición posterior en vivo sobre el bind-mount se
perdieron). El código del **bridge** sí quedó 100% intacto (no usaba bind-mount).

La extracción completa (con `.venv`/`node_modules` incluidos) vive en `C:\WIP\sb\v2-recovered\`
en este equipo — **fuera de este repo, a propósito**: todavía tiene PII real sin ofuscar (nombres
de terceros, un teléfono real, nombre de empresa y de clínica en los system prompts). No copiarlo
tal cual a ningún repo. Este documento es la versión curada y limpia de lo que vale la pena
preservar.

## 1. Lock de VRAM — ✅ ya portado

`backend/app/services/ollama_client.py` en v2 agregaba un `threading.Lock()` (`_VRAM_LOCK`) que
serializa todas las llamadas a Ollama que usan GPU (`generate`, `embed`, `embed_many`, `vision`),
con telemetría de cuánto esperó cada una (`wait_vram_ms`). Reemplaza los parches puntuales que
tenía v1 (`force_cpu` para el embed del chat, ventana nocturna para el caption) por una solución
de fondo.

**Estado**: portado a este repo el 19 de agosto de 2026, validado con contención real del worker
en background (`wait_vram_ms` de 737ms y 264ms observados bajo carga real). Ver
`backend/app/services/ollama_client.py` actual.

## 2. Filtro de fechas nativo en Qdrant — ✅ ya portado

`backend/app/services/retriever.py` en v2 armaba un filtro `range` nativo de Qdrant sobre el
payload `fecha` (string ISO 8601 / RFC3339), en vez del approach de v1: pedir 4x de más
(`overfetch`) y descartar en Python lo que caía fuera de rango.

**Estado**: portado el 19 de agosto de 2026, validado con 3 rangos de fecha distintos contra
datos reales (`/api/chat/retrieve`). Ver `backend/app/services/retriever.py` actual.

## 3. Prompt del tagger mejorado — ⏳ pendiente de portar

`backend/app/services/tagger.py` en v2 tenía reglas de clasificación financiera mucho más
detalladas, con ejemplos concretos para `ingreso`/`egreso`/`presupuesto`/`deuda`, y un JSON mejor
especificado. Mejora de calidad de extracción, más "blanda" (no hay forma de medirla sin
correrla contra datos reales) que las dos anteriores. Texto **ya limpio de PII** (los originales
mencionaban la empresa, la clínica y el partido de un tercero real — acá reemplazado siguiendo el
mismo criterio que v1 aplicó en su día: se cae el contexto de empresa/ubicación, "Esteban" → "Juan"
como nombre de ejemplo):

```
SYSTEM_PROMPT = """Sos un analista de datos de elite encargado de extraer información estructurada y precisa de mensajes de WhatsApp para el sistema de memoria personal privado de Damian.

Para el mensaje provisto, debés generar ÚNICAMENTE un objeto JSON bien formateado (sin explicaciones previas ni posteriores) con el siguiente esquema estricto:

{
  "resumen": "una sola frase corta, concisa y fáctica de lo que dice el mensaje",
  "personas_mencionadas": ["nombres de personas nombradas LITERALMENTE en el texto"],
  "empresas_mencionadas": ["empresas u organizaciones nombradas LITERALMENTE en el texto"],
  "promesas": [
    {
      "quien": "quién asume el compromiso (nombre literal, o 'Damian' si es el propio Damian)",
      "que": "acción o entregable concreto comprometido",
      "cuando": "plazo o fecha límite si la menciona, o null"
    }
  ],
  "transacciones": [
    {
      "monto": "número o valor mencionado tal cual (ej. '15000', '15 lucas', '500 usd')",
      "moneda": "ARS|USD|otro (identificar según el contexto o símbolo; default ARS)",
      "concepto": "detalle conciso de qué se está pagando, cobrando o cotizando",
      "tipo": "ingreso|egreso|presupuesto|deuda"
    }
  ],
  "tareas": ["acciones concretas de tipo TODO que Damian debería agendar o realizar de forma obligatoria"],
  "hechos": ["datos, eventos o hitos concretos del pasado o del presente que valga la pena retener, uno por string"],
  "tono": "uno de: cordial, formal, urgente, tenso, agresivo, pasivo-agresivo, afectuoso, informativo, humoristico, neutral",
  "sentimiento": {
    "polaridad": "positivo|neutro|negativo",
    "intensidad": 0.0
  },
  "relevancia": 0.0,
  "confianza": 0.0
}

Reglas estrictas de clasificación financiera ("transacciones"):
- "egreso": transferencias realizadas, pagos ejecutados, compras hechas, plata gastada (ej: "pagué el hosting", "ya te transferí las 20 lucas", "compré el repuesto").
- "ingreso": cobros acreditados, dinero recibido, transferencias entrantes (ej: "me entró el pago de Juan", "me pagaron la factura").
- "presupuesto": presupuestos enviados o recibidos, cotizaciones de productos/servicios, estimaciones de costos sin compromiso de pago aún (ej: "el NAS te sale 800 usd", "te paso la cotización por 45.000 pesos", "ese software sale 10 lucas al mes").
- "deuda": saldos pendientes, montos que Damian debe pagar o montos que a Damian le deben (ej: "te debo 5000", "me debés la cuota", "te pago la semana que viene", "quedó un remanente de 15 lucas sin pagar").
"""
```

**Para portarlo**: reemplazar el `SYSTEM_PROMPT` de `backend/app/services/tagger.py` por este
texto. No se hizo en esta sesión porque es un cambio de comportamiento del tagger (no solo de
infraestructura) y conviene decidirlo con intención, no de paso.

## 4. Restyle visual de Streamlit — copiado, no activado

`frontend/lib/ui.py` (nuevo en v2, sin PII — es CSS puro) copiado tal cual a
`frontend/lib/ui.py` en este repo. Gradientes neón + glassmorphism vía `apply_premium_style()`.
**No está importado en ningún lado todavía** — copiarlo no cambia el look actual del frontend. Si
se quiere activar: `from lib.ui import apply_premium_style` + llamarlo al principio de
`app.py`/cada página (así lo hacía v2 en `app.py` y `pages/1_Dashboard.py`).

## 5. Qué NO cambió en v2 (para no asumir de más)

- El bridge de v2 seguía en **whatsapp-web.js/Puppeteer** — la migración a Baileys de este repo
  es de esta sesión, v2 no la había resuelto.
- El stack seguía siendo los mismos 8 servicios (Postgres+pgvector, Qdrant, MinIO, Ollama,
  Whisper, backend, frontend, bridge) — ninguna simplificación de infra.
- No se recuperó ningún `docker-compose.yml` de v2 (vive solo en la carpeta borrada, nunca se
  bakea en una imagen) — no hay forma de saber si tenían cambios de infra planeados ahí.
- `_YO_ALIASES` en v2 tenía un alias de nombre ("damian fagundez") que v1 había sacado
  explícitamente por ser incorrecto (commit `74980b0`) — no portar ese cambio puntual, es un
  retroceso, no una mejora.
