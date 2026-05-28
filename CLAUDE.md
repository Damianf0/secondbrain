# SecondBrain — Contexto del proyecto

> **Para retomar desde Claude Code CLI**: pegar este documento al inicio de la sesión, o guardarlo como `CLAUDE.md` en la raíz del repo (Claude Code lo lee automáticamente).

---

## Quién soy y qué proyecto es este

Soy **Damian Orozco**, una persona técnica que trabaja con PHP/Laravel, MySQL, Node.js, Docker y bots de WhatsApp. Tengo otro proyecto previo (un bot de WhatsApp con stack PHP/Laravel + Node + MySQL + Ollama + Whisper) del que reuso patrones e infraestructura para este.

**SecondBrain** es un sistema personal de **memoria aumentada privada** — un Vault que indexa, procesa y permite consultar mis conversaciones de WhatsApp, emails de Gmail, y eventualmente más fuentes (Calendar, Drive, Telegram, etc.). Todo corre **100% local**, sin enviar datos a ninguna nube.

### Por qué lo hago
Quiero una "segunda memoria" que me ayude a recordar y consultar sobre mis propias actividades y relaciones, con queries del tipo:

- *¿Cuándo fue la última vez que hablé con Juan Pérez y de qué?*
- *¿Qué le prometí entregar al cliente Acme Clínica la semana pasada?*
- *¿Cuánto gasté en herramientas/software este mes?*
- *¿Qué tengo que hacer hoy?* (briefing proactivo)

(Lista completa de 18 queries de referencia en `docs/sprints.md`.)

---

## Pilares fundacionales (no negociables)

1. **Modelo local** — LLMs, embeddings, transcripción, todo en mi equipo. Costo recurrente $0.
2. **Vault** — los archivos crudos (audios, imágenes, PDFs) se **guardan**, no solo se procesan. Re-procesables a futuro.
3. **Privacidad por diseño** — nada sale del equipo.
4. **Calidad de procesamiento como diferencial** — la magia está en cómo se interpreta y estructura la información, no en el LLM final.
5. **Modular** — cada componente puede crecer o reemplazarse.
6. **Vault blindado** — endurecimiento del equipo se posterga a fase posterior; en POC el equipo es el de desarrollo.
7. **UI mínima en POC** — Streamlit para validar; UI bonita en fase 2.

---

## Hardware

- **Equipo**: i7 de 10ma generación, 32GB RAM, **NVIDIA RTX 3080 8GB VRAM**, Windows 11 con Docker Desktop + WSL2
- **Es un equipo separado del servidor de la clínica** — sin contaminación cruzada

Implicaciones del límite de 8GB VRAM:
- Modelos 7B-8B Q4 entran cómodos (~5-6GB)
- Modelo 12B Q4 entra apretado (~7-8GB), va a hacer offload parcial
- 32B no entra en GPU pura

---

## Stack final consolidado

| Componente | Tecnología | Modelo / versión |
|---|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2 + uv | — |
| Frontend (POC) | Streamlit | latest |
| Base relacional | PostgreSQL 16 + pgvector | `pgvector/pgvector:pg16` |
| Vector DB | Qdrant | `qdrant/qdrant:latest` |
| Object storage (Vault) | MinIO (S3-compatible) | `minio/minio:latest` |
| LLM principal | Ollama + Gemma 4 12B | `gemma4:12b` |
| LLM alternativo | Qwen3-VL 8B (texto+visión) | `qwen3-vl:8b` |
| Embeddings | qwen3-embedding 4B | `qwen3-embedding:4b` |
| Transcripción | Whisper Large V3 Turbo (faster-whisper) | `onerahmet/openai-whisper-asr-webservice:latest-gpu` |
| Bridge WhatsApp (Sprint 2) | Node.js + whatsapp-web.js | — |
| Containerización | Docker Compose | — |

### Nota importante sobre los modelos

Los modelos `gemma4:12b`, `qwen3-vl:8b` y `qwen3-embedding:4b` son las opciones más actuales (2026). En el plan original había sugerido `qwen2.5-vl:7b` y `bge-m3` pero verificamos versiones actuales y se cambiaron por estos.

**Decisión pendiente: benchmark Gemma 4 12B vs Qwen3-VL 8B**. En Sprint 0 los descargamos los dos y elegiremos el principal por velocidad/calidad real con datos míos.

---

## Arquitectura

### Almacenamiento separado por dominio

**Postgres** organizado en **5 schemas**:
- `core`: items, personas, empresas, proyectos, hechos
- `media`: metadata de archivos (binarios viven en MinIO)
- `processing`: cola de jobs, history
- `analytics`: dinámicas conversacionales, salud relacional
- `audit`: logs sensibles

**Qdrant** para vectores (embeddings). Múltiples collections previstas (una por tipo: messages, facts, captions).

**MinIO** para archivos crudos del Vault:
- Bucket `raw`: originales (audios .opus, imágenes, PDFs)
- Bucket `derived`: thumbnails, transcripciones, OCR results
- Bucket `exports`: exports manuales (.txt de WhatsApp)
- Estructura: `{source}/{año}/{mes}/{tipo}/{hash}.{ext}` con SHA-256 → deduplicación automática

### Procesamiento en niveles (tiered)

Acordamos pipeline en 4-5 niveles:
- **Nivel 0** — Inmediato (<1s): hash, metadata, EXIF
- **Nivel 1** — Online (segundos): OCR rápido, clasificación básica
- **Nivel 2** — Diferido (minutos en idle): captioning VLM, extracción profunda
- **Nivel 3** — Nocturno (horas): embeddings visuales, reconocimiento facial
- **Nivel 4** — Batch semanal: re-procesado con mejores modelos

Scheduler activa workers según uso de GPU y hora del día. Modos: `hot` / `warm` / `cold` / `deep cold`.

### Modelo de entidades (Sprint 1+)

Entidades core: Persona, Empresa, Proyecto, Lugar, Evento.
Específicas: Activo, Documento, **Promesa/Compromiso**, Incidente, Tema, **Transacción financiera**, Tarea.

**Entity resolution canónico**: que "Juan", "Juan P", "+54 9 XXX...", "jp@..." sean la misma persona. Esto es la columna vertebral del sistema.

### Análisis de tono y dinámica conversacional

Cada mensaje tiene:
- Tono individual: cordial / formal / urgente / tenso / agresivo / pasivo-agresivo / afectuoso / informativo / humorístico
- Sentimiento: polaridad + intensidad
- Marcadores específicos: contiene_reclamo, contiene_disculpa, contiene_promesa_bajo_presion, etc.
- Confianza del análisis (0-1)

Cada hilo tiene una **dinámica conversacional** (analizada en diferido, 10 min después del último mensaje del hilo).

Cada persona/empresa tiene **salud relacional** agregada (recalculada nocturnamente).

**Decisiones tomadas sobre tono**:
- Sí métricas para todas las personas
- Briefings emocionales solo cuando sea muy notorio
- El sistema aprende del contexto (no whitelist manual inicial)
- Visualización cualitativa (tendencias, etiquetas), no scores numéricos crudos
- Procesamiento híbrido por niveles, todo local

---

## Plan de Sprints

### Sprint 0 — Setup base ⚡ (TERMINADO, falta validación)

**Objetivo**: equipo configurado, servicios corriendo, modelos descargados, todo verde end-to-end.

**Estado**: archivos creados, falta:
1. Levantar `docker compose up -d` por primera vez
2. Esperar descarga de modelos (~25 min)
3. Validar dashboard verde en http://localhost:8501
4. Hacer benchmark Gemma 4 vs Qwen3-VL con datos míos

### Sprint 1 — Importación histórica WhatsApp 📥

Schema mínimo (`core.items`, `core.personas`, `media.attachments`, `processing.jobs`), parser de exports `.txt` de WhatsApp con filtro/mapeo de participantes a contactos canónicos, ingesta + almacenamiento en MinIO.

### Sprint 2 — Bridge WhatsApp en vivo 📲

Container Node.js con whatsapp-web.js (reusar patrones del proyecto previo), captura de mensajes nuevos en tiempo real (entrantes y salientes vía multi-device sync), QR en panel admin.

### Sprint 3 — Pipeline de tagging 🧠

Prompt del tagger (extracción de hechos + tono + entidades), entity resolution, almacenamiento en `core.facts`, `core.promesas`, `core.transacciones`.

### Sprint 4 — Embeddings y Q&A 💬

Embeddings en Qdrant, retriever híbrido (semantic + estructurado), chat funcional, validación con las 18 queries.

### Sprints futuros

5: Imágenes con tiered processing (OCR + captioning) | 6: Documentos (PDFs, Word, Excel) | 7: Audios (Whisper en pipeline) | 8: Conector Gmail | 9: Memoria estructurada (Memori/MemPalace) | 10: Briefings proactivos | 11+: Knowledge graph, salud relacional, etc.

**Camino acordado**: Sprint 1 prioriza WhatsApp (no Gmail) porque es donde está el flujo de trabajo real.

---

## Manejo de imágenes (decisión tomada)

Estrategia: **clasificación dirigida + tiered processing**.

Clasificación inicial (heurísticas + VLM si hace falta):
- **Trivial** (stickers, memes, GIFs) → solo metadata
- **Texto-céntrica** (capturas de chat, screenshots, documentos, recibos) → OCR + extracción estructurada
- **Mixta** (pizarras, diagramas, slides) → OCR + caption combinados
- **Visual pura** (fotos personales, paisajes) → caption + entidades visibles

Niveles 0-2 en POC. CLIP embeddings y reconocimiento facial → fase 2.

Decisión: **guardar TODOS los binarios** (es un Vault, no un índice). Hash SHA-256 para deduplicación.

---

## Decisiones técnicas tomadas

✅ **uv** como gestor de paquetes Python (no poetry/pip)
✅ **Postgres 16** con pgvector (no MySQL — MySQL es para clínica, esto es proyecto separado)
✅ **MinIO** para storage (no filesystem directo) — más profesional, mejor escalabilidad
✅ **Schemas en Postgres** desde día 1 (modularidad lógica)
✅ **Múltiples collections** en Qdrant
✅ **No backups** en POC (Damian se encarga manualmente)
✅ **Python desde cero** como única lógica de pipeline (no PHP, aunque el proyecto previo use PHP)
✅ **Streamlit** en POC; eventualmente migrar a panel propio (Reflex o Laravel forkeado del proyecto previo)
✅ **whatsapp-web.js** (no Baileys) — Damian ya lo está probando
✅ **Audios .opus tal cual** (sin conversión) — Whisper los lee directo
✅ **Sin cifrado** de archivos individuales en POC (confiar en BitLocker/LUKS del disco)
✅ **Capa de tono y dinámica conversacional** desde el inicio (campo en items + nivel 2 para hilos)
✅ **Equipo separado del servidor de la clínica**

---

## Estado actual (en qué estoy parado)

**Sprint 0 — archivos creados**, listos para levantar. Estructura:

```
secondbrain/
├── docker-compose.yml         ← 7 servicios + 2 init jobs
├── .env.example               ← variables a completar
├── .gitignore
├── README.md
│
├── backend/                   ← FastAPI Python con uv
│   ├── pyproject.toml
│   ├── alembic.ini + alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py          ← pydantic-settings
│   │   ├── core/logging.py    ← structlog
│   │   ├── db/session.py
│   │   ├── routers/
│   │   │   ├── health.py      ← /api/health, /api/health/live, ready
│   │   │   └── test.py        ← /api/test/llm, embed, vault, qdrant
│   │   └── services/
│   │       ├── ollama_client.py    ← wrapper completo
│   │       ├── qdrant_client.py    ← wrapper con ensure_collection
│   │       ├── minio_client.py     ← VaultStorage completo (raw + derived + presigned URLs)
│   │       └── whisper_client.py
│   └── tests/test_smoke.py
│
├── frontend/                  ← Streamlit
│   ├── pyproject.toml
│   ├── app.py                 ← home con overview
│   ├── pages/
│   │   ├── 1_Dashboard.py     ← detalle por servicio
│   │   ├── 2_Benchmark.py     ← comparar Gemma 4 vs Qwen3-VL lado a lado
│   │   └── 3_Vault.py         ← upload + preview
│   └── lib/api_client.py
│
├── docker/
│   ├── backend/Dockerfile     ← Python 3.12 + uv
│   ├── frontend/Dockerfile
│   └── postgres/init.sql      ← extensiones + 5 schemas + spanish_unaccent
│
├── docs/
│   ├── sprints.md             ← plan completo + 18 queries de referencia
│   ├── architecture.md
│   └── setup-windows.md
│
└── scripts/
    ├── check-requirements.ps1 ← verifica Docker, GPU, RAM
    └── warmup-models.sh
```

**Lo que falta validar antes de Sprint 1**:
1. `cp .env.example .env` y editar credenciales
2. `docker compose up -d`
3. Esperar descarga de modelos (~25 min primera vez): `docker compose logs -f ollama-init`
4. Verificar dashboard verde: http://localhost:8501
5. Probar LLM: ir a Benchmark → "Generar"
6. Comparar modelos lado a lado en pestaña "Comparar"
7. Subir un archivo de prueba al Vault

---

## Repositorio relacionado (proyecto previo)

Proyecto privado en producción con stack PHP Laravel + Node + MySQL + Ollama + Whisper. Reusable de ahí para Sprint 2:

- Patrón del bridge whatsapp-web.js (manejo de QR, sesión persistente vía volumen Docker, webhook al backend)
- Ventana de mensajes consecutivos (acumular mensajes que llegan rápido en una sola unidad: 8s espera, 45s máxima, reset por inactividad 30 min)
- Patrón de medias compartidas (bot descarga, panel lee read-only)
- Whisper como servicio HTTP con `faster_whisper` engine
- Estructura modular del bot: `index.js`, `whatsapp.js`, `mensajes.js`, `ollama.js`, `respuestas.js`, `cola.js`, `horario.js`

**NO reusar**: la lógica vertical específica del proyecto previo, ni el panel Laravel (acá vamos con Streamlit/Python).

---

## Preferencias de trabajo

- Soy técnico, no me asusta línea de comandos
- **Verificar versiones actuales** antes de proponer cualquier librería/modelo (lección aprendida: Qwen 2.5 vs Qwen 3, bge-m3 vs qwen3-embedding, Mem0 vs alternativas más nuevas)
- Commits de git granulares y autónomos (no preguntar antes de commitear pasos validados)
- Honestidad sobre trade-offs y limitaciones reales del hardware
- Prefiero entender el "por qué" antes que copiar y pegar
- Español argentino con modismos OK ("dale", "che", "boludo" entre confianza)

---

## Lo que sigue (para Claude Code CLI)

Próximos pasos inmediatos:
1. Crear el repo en GitHub y hacer push del Sprint 0
2. Levantar el stack en mi equipo Windows con la 3080
3. Ejecutar el benchmark Gemma 4 vs Qwen3-VL con prompts reales
4. Decidir modelo principal con datos en mano
5. Arrancar Sprint 1 — Importación histórica de WhatsApp

Cuando arranques con Sprint 1, los pasos serán:
1. Crear modelos SQLAlchemy: `core.items`, `core.personas`, `media.attachments`, `processing.jobs`
2. Generar primera migration con Alembic: `alembic revision --autogenerate -m "sprint 1: schema base"`
3. Aplicar: `alembic upgrade head`
4. Crear parser de exports `.txt` de WhatsApp en `backend/app/services/whatsapp_parser.py`
5. Endpoint `/api/import/whatsapp/upload` para recibir el .txt
6. Vista en Streamlit para subir y mapear participantes a contactos canónicos
7. Tagger básico que llame a Ollama y guarde el resultado

---

## Cómo invocarme en Claude Code

Cuando arranques una sesión nueva en Claude Code CLI, podés:

```
He retomado este proyecto. Leé CLAUDE.md (o este contexto) y decime:
1. Qué entendiste del estado actual
2. Qué validamos del Sprint 0 ya
3. Qué tendríamos que hacer ahora
```

O directamente:

```
Vamos al Sprint 1. Empecemos por los modelos SQLAlchemy del schema base.
```

O para validar Sprint 0:

```
Levantemos el Sprint 0 y validemos que todo está verde antes de avanzar.
```
