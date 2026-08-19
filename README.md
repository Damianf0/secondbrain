# SecondBrain

> Sistema personal de memoria aumentada — Vault privado con LLMs locales

Un sistema de "segunda memoria" privada para indexar, procesar y consultar conversaciones de WhatsApp, emails de Gmail, y eventualmente más fuentes. Todo corre **100% local**, sin enviar datos a la nube.

## Filosofía

- **Modelo local**: LLMs, embeddings, transcripción — todo en tu equipo
- **Vault**: archivos crudos guardados, no solo procesados
- **Privacidad por diseño**: nada sale del equipo
- **Modular**: cada componente puede crecer o reemplazarse independientemente
- **Calidad de procesamiento como diferencial**: la magia está en cómo se interpreta y estructura la información

## Stack

| Componente | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2 |
| Gestor de paquetes | uv |
| Frontend (POC) | Streamlit |
| Base relacional | PostgreSQL 16 + pgvector |
| Vector DB | Qdrant |
| Vault (archivos crudos) | Filesystem local (volumen Docker, servido por el propio backend) |
| LLMs | Ollama + qwen3:8b (chat/tagger) + qwen3-vl:8b (visión) |
| Embeddings | bge-m3 |
| Transcripción | Whisper (faster-whisper) |
| Bridge WhatsApp | Node.js + [Baileys](https://github.com/WhiskeySockets/Baileys) |
| Conector Gmail | Python (sprint posterior) |
| Containerización | Docker + Docker Compose |

## Estado

🟢 **En uso activo** (última actualización: 19 de agosto de 2026)

El bridge de WhatsApp está conectado y capturando mensajes en vivo (texto, audio con
transcripción, media). Hay un histórico importado de exports `.txt` corriendo su backfill de
embeddings/tagging en background — mirá el estado real en `http://localhost:8501` → página
**Worker** (o `GET /api/panel/queues`, que consulta la base en vivo, no un contador en memoria).

> ⚠️ `docs/sprints.md`, `docs/architecture.md` y `docs/setup-windows.md` describen el diseño
> original de Sprint 0 (mayo 2026) y **no siguieron el ritmo del código** — no son confiables
> para saber "dónde estamos". Este README y `docs/v2-hallazgos.md` sí están al día.

## Hardware recomendado

- CPU: i7 10ma generación o superior
- RAM: 32 GB
- GPU: NVIDIA RTX 3080 8GB VRAM o superior (con CUDA)
- Storage: 200 GB libres en SSD NVMe
- OS: Windows 11 con Docker Desktop + WSL2

## Quick start

```bash
# 1. Clonar repo
git clone <tu-repo> secondbrain
cd secondbrain

# 2. Copiar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# 3. Levantar servicios
docker compose up -d

# 4. Esperar que se descarguen modelos (primer arranque, ~25 min)
docker compose logs -f ollama-init

# 5. Aplicar migraciones de DB (necesario en una base nueva -- si te salta un
#    error "relation core.items does not exist" al usar el bridge, es esto)
docker compose exec backend alembic upgrade head

# 6. Vincular el bridge de WhatsApp: abrí http://localhost:8501 → página
#    "Bridge WhatsApp" y escaneá el QR (o configurá BRIDGE_PAIR_NUMBER en .env
#    para emparejar por código en vez de QR). Usá un número secundario, no el
#    principal -- es protocolo no oficial, mismo riesgo que cualquier bot de WA.

# 7. Abrir el panel
# http://localhost:8501
```

## Documentación

- [Hallazgos recuperados de una v2 archivada](docs/v2-hallazgos.md) — lo más al día, además de este README
- [Sprints y plan de desarrollo](docs/sprints.md) *(desactualizado, ver aviso en "Estado")*
- [Arquitectura](docs/architecture.md) *(desactualizado)*
- [Setup en Windows](docs/setup-windows.md) *(desactualizado)*

## Licencia

Privado / Personal — Damian Orozco
