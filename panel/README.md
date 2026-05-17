# SecondBrain Panel

Panel de control de escritorio para SecondBrain. PySide6 (Qt6) + httpx.

## Para qué

Orquestar el stack y monitorear procesamiento **sin tener que abrir el browser**:

- **Servicios**: estado de cada container del stack, restart/stop/start/logs por container
- **Worker**: pausar/reanudar el worker continuo, ver último tick, acumulado de la sesión, ventana horaria de caption
- **Colas**: contadores de `processing.jobs` por tipo y estado, vista única de toda la cola
- **Tagger**: procesar items ahora (sync) o encolar lotes para que el worker los drene

## Correr

Desde la raíz del repo:

```powershell
cd panel
uv sync
uv run secondbrain-panel
```

Primer arranque tarda un minuto (descarga PySide6, ~70 MB).

## Config

Variables de entorno opcionales:

| Variable | Default | Para qué |
|---|---|---|
| `SECONDBRAIN_BACKEND_URL` | `http://localhost:8000` | URL del FastAPI |
| `SECONDBRAIN_STREAMLIT_URL` | `http://localhost:8501` | Botón "Streamlit ↗" |
| `SECONDBRAIN_COMPOSE_DIR` | (auto-detect) | Donde está `docker-compose.yml` |
| `SECONDBRAIN_REFRESH_MS` | `5000` | Auto-refresh del tab activo |
| `SECONDBRAIN_HTTP_TIMEOUT` | `8` | Timeout HTTP en segundos |

El panel busca `docker-compose.yml` subiendo desde el cwd. Si lo lanzás desde la raíz del repo, lo encuentra solo.

## Lo que NO hace (todavía)

- Logs streaming en vivo (los logs se piden on-demand con un botón)
- Edición de batch sizes / ventana horaria en runtime (hay que tocar `.env` y reiniciar)
- Ver QR del bridge embebido (hay botón pero abre el endpoint en navegador)
- Backups, restores, migraciones

Si querés alguno de estos, son ediciones puntuales — el código está organizado por tab para que sea fácil extender.
