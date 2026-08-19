# SecondBrain — Contexto del proyecto

> **Para retomar desde Claude Code CLI**: pegar este documento al inicio de la sesión, o guardarlo como `CLAUDE.md` en la raíz del repo (Claude Code lo lee automáticamente).
>
> **Actualizado 19 de agosto de 2026** — este documento estuvo congelado en el estado de Sprint 0
> (9 de mayo) durante 3 meses mientras el código avanzaba muy por delante. Si estás retomando
> después de otra pausa larga, no confíes en las secciones "Plan de Sprints" / "Arquitectura" de
> abajo para saber qué existe de verdad — son el diseño original, con partes nunca construidas
> (dinámica conversacional, salud relacional, scheduler de niveles). Las secciones "Stack final
> consolidado", "Estado actual" y "Decisiones técnicas tomadas" sí están al día. Para el detalle
> completo de auditoría y de qué se recuperó de una v2 que se llegó a perder, ver
> `docs/v2-hallazgos.md`.

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
| Vault (archivos crudos) | Filesystem local (volumen Docker, servido por `GET /api/vault/file`) | — |
| LLM principal (chat + tagger) | Ollama + Qwen3 8B | `qwen3:8b` |
| LLM visión | Qwen3-VL 8B | `qwen3-vl:8b` |
| Embeddings | BGE-M3 | `bge-m3` |
| Transcripción | Whisper Large V3 Turbo (faster-whisper), **CPU** (decisión, ver §10 de `docs/pipeline.md`) | `onerahmet/openai-whisper-asr-webservice:latest` |
| Bridge WhatsApp | Node.js + [Baileys](https://github.com/WhiskeySockets/Baileys) | `@whiskeysockets/baileys` |
| Containerización | Docker Compose | — |

### Nota sobre los modelos (resuelto, ya no es una decisión pendiente)

`qwen3:8b` ganó el benchmark inicial contra Gemma 4 12B (que ni entra cómodo en 8GB VRAM junto a
lo demás). `bge-m3` reemplazó a `qwen3-embedding:4b` tras un A/B con datos reales (mejor recall en
español rioplatense, y a diferencia del embedding anterior convive con `qwen3:8b` en VRAM sin
hacer swap). MinIO se reemplazó por filesystem local — para un vault de un solo usuario, un
object storage S3 completo era más infraestructura de la que hacía falta. whatsapp-web.js se
reemplazó por Baileys — más liviano (sin Chromium), y el motivo original de elegir whatsapp-web.js
("ya lo estaba probando") no era una comparación técnica real.

Se re-evaluaron tres candidatos para reemplazar `qwen3:8b`, todos con hardware real, no por
intuición ni por lo que dice un blog:

- `qwen3.5:9b` — no entra en 8GB (72% GPU / 28% CPU offload), 49.5s de latencia para una
  respuesta de una oración (vs 586ms de `qwen3:8b`). Descartado.
- `gemma4:e4b` — directamente crashea (`llama runner process has terminated`), necesita 10GB.
  Descartado.
- `gemma3:4b` — este sí es interesante: más liviano (4.3GB vs 6.0GB) y más rápido (130 vs 90
  tok/s). Probado contra el prompt real del tagger (8 casos cubriendo cada rama del schema):
  empató en cantidad de errores con `qwen3:8b` pero **inventó una persona mencionada que no
  estaba en el mensaje, dos veces**, y devolvió un JSON completamente vacío una vez. Para un
  sistema que depende de no inventar hechos, no se adoptó — pero queda como candidato si el
  tagger algún día necesita correr más liviano y se le encuentra una forma de controlar mejor
  las alucinaciones (few-shot más estricto, temperature más baja, etc.).

---

## Arquitectura

### Almacenamiento separado por dominio

**Postgres** organizado en **5 schemas**:
- `core`: items, personas, empresas, proyectos, hechos
- `media`: metadata de archivos (binarios viven en el Vault, filesystem local)
- `processing`: cola de jobs, history
- `analytics`: dinámicas conversacionales, salud relacional (reservado, no implementado todavía)
- `audit`: logs sensibles

**Qdrant** para vectores (embeddings). Collections en uso: `messages`, `facts`.

**Vault** (filesystem local, `backend/app/services/vault_storage.py`, antes era MinIO) para
archivos crudos:
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

**Estado real (19 de agosto de 2026): Sprints 0-7 construidos y en uso, no solo "planeados".**
Ver "Estado actual" más abajo para la foto completa. Dejo la descripción original de cada sprint
porque sigue siendo válida como resumen de alcance, con el estado real anotado:

### Sprint 0 — Setup base ⚡ ✅ hecho y validado

### Sprint 1 — Importación histórica WhatsApp 📥 ✅ hecho

Parser de exports `.txt` de WhatsApp con mapeo de participantes a contactos canónicos. En uso:
histórico real importado (decenas de miles de mensajes entre 2 conversaciones, ver "Estado
actual"). *Ojo*: importar por sí solo NO encola procesamiento (embed/tagger) — es opt-in, hay que
marcar la conversación `seguir=true` y encolar a mano (`POST /api/panel/conversations/{id}/enqueue`
o `POST /api/embeddings/run`).

### Sprint 2 — Bridge WhatsApp en vivo 📲 ✅ hecho (con Baileys, no whatsapp-web.js)

Captura de mensajes en tiempo real (entrantes y salientes), con descarga de media. Migrado de
whatsapp-web.js a Baileys en agosto 2026 — ver nota de stack arriba.

### Sprint 3 — Pipeline de tagging 🧠 ✅ hecho (parcial respecto del diseño original)

Prompt del tagger extrae resumen, personas/empresas mencionadas, promesas, transacciones
(ingreso/egreso/presupuesto/deuda), tareas, hechos, tono (un campo, no la "dinámica
conversacional" completa de la sección Arquitectura), sentimiento, relevancia, confianza.
**No construido**: entity resolution sofisticada (hoy es match por teléfono/nombre exacto, no el
sistema de aliases inteligente descripto en Arquitectura), salud relacional, dinámica
conversacional por hilo.

### Sprint 4 — Embeddings y Q&A 💬 ✅ hecho

Embeddings en Qdrant (`bge-m3`), retriever híbrido con filtro de fechas nativo (portado de una v2
que se llegó a perder, ver `docs/v2-hallazgos.md`), chat funcional con citas de fuente. No se
validó formalmente contra las 18 queries de referencia, pero el patrón funciona (caso real:
resolver una consulta ambigua sobre un problema técnico contra miles de mensajes).

### Sprint 5 (imágenes), 6 (documentos), 7 (audios) — ✅ construidos

Captioning de imágenes (`qwen3-vl:8b`, ventana nocturna 02-06h para no competir por VRAM),
extracción de documentos (PDF/DOCX/XLSX/texto plano), transcripción de audio (Whisper en CPU).

### Sprints futuros (sin cambios respecto del plan original)

8: Conector Gmail | 9: Memoria estructurada | 10: Briefings proactivos | 11+: Knowledge graph, salud relacional, dinámica conversacional completa, etc.

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
🔄 ~~MinIO para storage~~ — **revertido en agosto 2026**: filesystem local. Para un vault de un
solo usuario, un object storage S3 completo era más infraestructura de la que hacía falta.
✅ **Schemas en Postgres** desde día 1 (modularidad lógica)
✅ **Múltiples collections** en Qdrant
✅ **No backups** en POC (Damian se encarga manualmente)
✅ **Python desde cero** como única lógica de pipeline (no PHP, aunque el proyecto previo use PHP)
✅ **Streamlit** en POC; eventualmente migrar a panel propio (Reflex o Laravel forkeado del proyecto previo)
🔄 ~~whatsapp-web.js~~ — **revertido en agosto 2026**: Baileys. Más liviano (sin Chromium), y el
motivo original para elegir whatsapp-web.js no era técnico ("ya lo estaba probando").
✅ **Audios .opus tal cual** (sin conversión) — Whisper los lee directo
✅ **Sin cifrado** de archivos individuales en POC (confiar en BitLocker/LUKS del disco)
✅ **Capa de tono y dinámica conversacional** desde el inicio (campo en items + nivel 2 para hilos)
✅ **Equipo separado del servidor de la clínica**

---

## Estado actual (en qué estoy parado)

**En uso activo, no un POC sin probar.** El stack completo corre (`docker compose up -d`), el
bridge de WhatsApp está vinculado y capturando en vivo, y hay un histórico real importado
corriendo su backfill de embeddings/tagging en background. Estructura real (no exhaustiva, la
carpeta `app/` creció mucho desde Sprint 0):

```
secondbrain/
├── docker-compose.yml         ← 8 servicios (sin MinIO — vault es filesystem)
├── .env.example
├── CLAUDE.md, README.md
│
├── backend/                   ← FastAPI Python con uv
│   ├── app/
│   │   ├── routers/           ← health, test, imports, bridge, contacts, conversations,
│   │   │                         tagger, embeddings, chat, transcribe, extract, images,
│   │   │                         worker, panel, vault
│   │   └── services/          ← ollama_client (con lock de VRAM), qdrant_client,
│   │                             vault_storage (filesystem, reemplazó minio_client),
│   │                             whisper_client, embedder, tagger, retriever (filtro de
│   │                             fechas nativo), chat, queue_worker, whatsapp_parser,
│   │                             extractor, imager, transcriber, phones, vcard_parser
│   └── tests/test_smoke.py    ← sigue siendo solo smoke tests de Sprint 0, sin cobertura
│                                  del resto (deuda técnica conocida)
│
├── frontend/                  ← Streamlit, 13 páginas (Dashboard, Vault, Import WhatsApp,
│                                  Bridge WhatsApp, Contactos, Conversaciones, Tagger, Chat,
│                                  Audios, Documentos, Imagenes, Worker, Benchmark)
│
├── panel/                     ← App de escritorio PySide6 (control/monitoreo, alternativa
│                                  al browser) — no corre en Docker, se levanta aparte
│
├── bridge/                    ← Node.js + Baileys (antes whatsapp-web.js)
│
├── docs/
│   ├── v2-hallazgos.md        ← al día — qué se recuperó de una v2 archivada y qué se portó
│   ├── pipeline.md            ← estado real medido al 2026-05-17, útil como referencia de
│   │                             throughput aunque los números de hoy sean otros
│   └── sprints.md, architecture.md, setup-windows.md ← desactualizados, no confiar
│
└── scripts/
```

**Deuda técnica conocida** (no inventada, encontrada auditando):
- Sin tests para casi nada de lo construido después de Sprint 0.
- El import histórico (`/api/import/whatsapp/import`) no encola procesamiento automáticamente —
  hay que marcar `seguir=true` y encolar a mano.
- Entity resolution es básica (match exacto por teléfono/nombre), no el sistema de aliases
  inteligente que describe la sección Arquitectura.

**Para ver el estado real de las colas en cualquier momento** (no confiar en contadores en
memoria que se resetean con cada restart del backend): `GET /api/panel/queues`, o el frontend →
página **Worker** → sección "Colas — estado real". Da pendiente/en_proceso/completado/fallido +
ritmo real + ETA por etapa, calculado con una query directa a la base.

---

## Repositorio relacionado (proyecto previo)

Proyecto privado en producción (`workbench-reforma-2026`) con stack PHP Laravel + Node + MySQL +
Ollama + Whisper. **Sigue en producción, con el mismo número de WhatsApp que usa el bridge de
SecondBrain** (decisión aceptada: dos automatizaciones no oficiales sobre la misma cuenta,
duplica riesgo de baneo — si da problemas, mover SecondBrain a un número secundario). Tiene un
sidecar en Baileys (`wa-avatars-baileys`) para bajar fotos de perfil — el bot principal de ese
proyecto sigue en whatsapp-web.js, no confundir con la migración de SecondBrain. Reusable de ahí
para Sprint 2:

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

Pendientes reales, actualizados al 19 de agosto de 2026 (no los de Sprint 0, esos ya se hicieron):

1. **Prompt del tagger mejorado** (recuperado de una v2 archivada, texto limpio de PII en
   `docs/v2-hallazgos.md` §3) — evaluar si vale la pena portarlo también a `chat.py`.
2. **Re-correr `backend/ab_embedding.py`** con `qwen3-embedding:0.6b` como challenger nuevo, una
   vez que el backfill del histórico importado termine y haya volumen real para comparar calidad
   (no solo velocidad, que ya se comparó y dio empate).
3. **Cobertura de tests** — sigue en cero para casi todo lo construido después de Sprint 0.
4. **Sprint 8 (Gmail)** y sprints posteriores — sin arrancar.
5. Si el backfill nocturno del histórico ya terminó (chequear `GET /api/panel/queues`), decidir
   si vale la pena taggear más allá de los últimos 3 meses que se encolaron, o dejarlo así y
   taggear a demanda.

---

## Cómo invocarme en Claude Code

Cuando arranques una sesión nueva en Claude Code CLI, podés:

```
He retomado este proyecto. Leé CLAUDE.md (o este contexto) y decime:
1. Qué entendiste del estado actual
2. Qué está corriendo ahora mismo (docker compose ps, GET /api/panel/queues)
3. Qué tendríamos que hacer ahora
```

O directamente sobre alguno de los pendientes de la sección "Lo que sigue".
