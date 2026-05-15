# Bridge WhatsApp — Sprint 2

Container Node.js que se conecta a WhatsApp Web vía [whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js)
y reenvía cada mensaje (entrante y saliente) al backend de SecondBrain para que lo persista como `Item`.

## Cómo funciona

1. Al arrancar intenta restaurar la sesión guardada en el volumen (`./data/whatsapp-session`).
2. Si no hay sesión válida, emite un QR. Se ve en:
   - El panel de Streamlit → página **Bridge WhatsApp**
   - `http://localhost:3001/qr` (página con auto-refresh)
   - Los logs del container (`docker compose logs -f bridge`) como QR ASCII
3. Escaneás el QR desde WhatsApp del teléfono (Dispositivos vinculados → Vincular un dispositivo).
4. A partir de ahí, cada mensaje nuevo se POSTea a `backend:8000/api/bridge/whatsapp/ingest`.

La sesión queda persistida; en reinicios no hace falta re-escanear (salvo que WhatsApp expire el link).

## Endpoints HTTP del bridge (puerto `BRIDGE_PORT`, default 3001)

| Ruta        | Qué devuelve |
|-------------|--------------|
| `/health`   | `{ ok, status }` |
| `/status`   | estado de conexión, número/nombre de la cuenta, contadores de mensajes |
| `/qr.json`  | `{ data_url, raw }` del QR pendiente (404 si no hay) |
| `/qr.png`   | el QR como PNG |
| `/qr`       | página HTML con auto-refresh para escanear desde el navegador |

## Variables de entorno

| Var | Default | Descripción |
|-----|---------|-------------|
| `BRIDGE_PORT` | `3001` | Puerto HTTP del bridge |
| `BACKEND_URL` | `http://backend:8000` | Backend FastAPI |
| `BRIDGE_INGEST_PATH` | `/api/bridge/whatsapp/ingest` | Endpoint de ingest |
| `BRIDGE_SESSION_PATH` | `/app/session` | Dónde persiste la sesión (volumen) |
| `PUPPETEER_EXECUTABLE_PATH` | `/usr/bin/chromium` | Chromium del sistema |
| `BRIDGE_CAPTURE_OUTGOING` | `true` | Capturar también mensajes que envío yo |
| `BRIDGE_BACKEND_RETRIES` | `4` | Reintentos al postear al backend |

## Limitaciones conocidas (POC)

- **Solo metadata de media**: si llega una imagen/audio/etc. se registra `es_media=true` + `media_tipo`,
  pero el binario **no** se descarga todavía (eso es Sprint 5 — descarga a MinIO).
- **No hay backfill**: solo captura mensajes nuevos desde que está conectado. El histórico se importa
  con el export `.txt` (Sprint 1).
- Si el backend está caído, los mensajes que no se pudieron entregar van a `session/dead-letter.jsonl`.

## Desarrollo

El código se copia a la imagen en build (no hay bind-mount), así que tras cambiarlo:

```bash
docker compose build bridge && docker compose up -d bridge
```
