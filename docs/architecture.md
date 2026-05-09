# Arquitectura

## Vista general

```
┌────────────────────────────────────────────────────────────┐
│  Frontend (Streamlit)                                      │
│  - Panel admin                                             │
│  - Dashboard                                               │
│  - Benchmark, Vault, Q&A                                   │
└────────────────────────┬───────────────────────────────────┘
                         │ HTTP
                         ▼
┌────────────────────────────────────────────────────────────┐
│  Backend (FastAPI Python)                                  │
│  - Pipeline core                                           │
│  - Tagger, embedder, retriever                             │
│  - API REST                                                │
└──┬─────────────┬─────────────┬──────────┬─────────────┬────┘
   │             │             │          │             │
   ▼             ▼             ▼          ▼             ▼
┌────────┐ ┌─────────┐ ┌─────────────┐ ┌──────┐ ┌────────────┐
│Postgres│ │ Qdrant  │ │   MinIO     │ │Ollama│ │  Whisper   │
│(schemas│ │(vectors)│ │ (Vault raw  │ │(LLMs)│ │(transcribe)│
│por dom)│ │         │ │  + derived) │ │ +GPU │ │            │
└────────┘ └─────────┘ └─────────────┘ └──────┘ └────────────┘
```

## Decisiones arquitectónicas

### Separación por dominio en Postgres (schemas)

Postgres está organizado en 5 schemas:

- **core**: items, personas, empresas, proyectos, hechos
- **media**: metadata de archivos (los binarios viven en MinIO)
- **processing**: cola de jobs, history de procesamiento
- **analytics**: dinámicas conversacionales, salud relacional
- **audit**: logs sensibles, history de cambios

Esto permite modularidad sin la complejidad de DBs separadas. Si un schema crece mucho en el futuro, se puede migrar a otra DB con pg_dump.

### MinIO para archivos crudos (Vault)

Los archivos binarios (audios, imágenes, PDFs, videos) se guardan en MinIO con:

- **Hash SHA-256 como nombre**: deduplicación automática
- **Organización jerárquica**: `{source}/{año}/{mes}/{tipo}/{hash}.{ext}`
- **3 buckets**: `raw` (originales), `derived` (thumbnails, transcripciones), `exports` (.txt manuales)
- **Presigned URLs**: el frontend muestra archivos sin proxy

Razones vs filesystem directo:
- Acceso uniforme via S3 API
- Replicación nativa para backup
- Versionado opcional
- Mejor experiencia con Docker

### Qdrant separado para vectores

Aunque pgvector permitiría hacer todo en Postgres, Qdrant ofrece:
- Mejor rendimiento en búsqueda vectorial (Rust, SIMD)
- Quantización avanzada
- Filtrado complejo combinado con vector search
- Menos carga sobre Postgres

### Procesamiento en niveles (tiered)

Los datos se procesan en 4 niveles:

- **Nivel 0 — Inmediato (< 1s)**: hash, metadata, EXIF
- **Nivel 1 — Online (segundos)**: OCR rápido, clasificación básica
- **Nivel 2 — Diferido (minutos en idle)**: captioning, extracción profunda
- **Nivel 3 — Nocturno (horas)**: embeddings visuales, reconocimiento facial
- **Nivel 4 — Batch semanal**: re-procesado con mejores modelos

El scheduler activa workers según uso de GPU y hora del día.

## Stack de modelos

### LLM principal: Gemma 4 12B
- Multilingüe nativo (140+ idiomas)
- Function calling nativo
- Razonamiento configurable
- ~7-8 GB VRAM con Q4

### LLM alternativo: Qwen3-VL 8B
- Más ligero, más rápido
- Texto + visión integrado
- Excelente para JSON estructurado en español
- ~5-6 GB VRAM con Q4

### Embeddings: qwen3-embedding 4B
- #1 en MTEB multilingüe (score 70.58)
- 100+ idiomas
- ~3 GB VRAM

### Transcripción: Whisper Large V3 Turbo
- 6x más rápido que Large V3 estándar
- Excelente español
- Vía `faster_whisper` engine

## Flujo de un mensaje (pipeline futuro)

```
Mensaje WhatsApp llega
    ↓
[Bot Node.js] captura evento, descarga media si hay
    ↓
POST /api/ingest al backend
    ↓
[Normalizer] crea item en core.items
    ↓
Si hay attachment:
    [VaultStorage] guarda binario en MinIO con hash
    [Attachment] crea registro en media.attachments
    ↓
[Job queue] encola Nivel 1 inmediato
    ↓
[Tagger Worker] llama a Ollama:
    - extrae tono, entidades, hechos
    - resuelve entidades canónicas
    - genera resumen
    ↓
[Embedder Worker] genera vector y guarda en Qdrant
    ↓
[Job queue] encola Nivel 2 si corresponde:
    - Audios → Whisper
    - Imágenes → OCR + caption
    - Análisis de hilo (dinámica conversacional)
    ↓
Item disponible para queries
```

## Referencias

- Modelos Ollama: https://ollama.com/library
- pgvector: https://github.com/pgvector/pgvector
- Qdrant: https://qdrant.tech
- MinIO: https://min.io
