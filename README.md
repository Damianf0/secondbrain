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
| Object storage | MinIO (S3-compatible) |
| LLMs | Ollama + Gemma 4 12B / Qwen3-VL 8B |
| Embeddings | qwen3-embedding 4B |
| Transcripción | Whisper (faster-whisper) |
| Bridge WhatsApp | Node.js + whatsapp-web.js (Sprint 2) |
| Conector Gmail | Python (sprint posterior) |
| Containerización | Docker + Docker Compose |

## Estado

🟡 **Sprint 0 — Setup base** (en progreso)

Ver [docs/sprints.md](docs/sprints.md) para el plan completo.

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

# 5. Aplicar migraciones de DB
docker compose exec backend alembic upgrade head

# 6. Abrir el panel
# http://localhost:8501
```

## Documentación

- [Sprints y plan de desarrollo](docs/sprints.md)
- [Arquitectura](docs/architecture.md)
- [Setup en Windows](docs/setup-windows.md)

## Licencia

Privado / Personal — Damian Orozco
