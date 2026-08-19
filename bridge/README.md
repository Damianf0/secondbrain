# Bridge WhatsApp — Sprint 2

Container Node.js que se conecta a WhatsApp Web vía [Baileys](https://github.com/WhiskeySockets/Baileys)
(WebSocket directo al protocolo multi-device, sin browser/Chromium de por medio) y reenvía cada
mensaje (entrante y saliente) al backend de SecondBrain para que lo persista como `Item`.

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
| `BRIDGE_SESSION_PATH` | `/app/session` | Dónde persiste la sesión (volumen). Baileys guarda sus credenciales en `<session>/baileys/` |
| `BRIDGE_LOG_LEVEL` | `warn` | Nivel de log de Baileys (pino) — `info`/`debug` es muy verboso |
| `BRIDGE_PAIR_NUMBER` | *(vacío)* | Si se setea (solo dígitos, con código de país), vincula por código de 8 caracteres en vez de QR — se pide una vez en el primer arranque, se ve en los logs |
| `BRIDGE_CAPTURE_OUTGOING` | `true` | Capturar también mensajes que envío yo |
| `BRIDGE_INCLUDE_BROADCASTS` | `false` | Capturar listas de difusión (los Estados se ignoran siempre) |
| `BRIDGE_DOWNLOAD_MEDIA_TYPES` | `audio,documento,imagen` | Tipos de media que se descargan y reenvían (video/sticker quedan afuera por default) |
| `BRIDGE_MAX_MEDIA_MB` | `20` | Tamaño máximo a descargar; si lo excede, queda solo la metadata |
| `BRIDGE_BACKEND_RETRIES` | `4` | Reintentos al postear al backend |

## Limitaciones conocidas (POC)

- **No hay backfill**: solo captura mensajes nuevos desde que está conectado. El histórico se importa
  con el export `.txt` (Sprint 1).
- Si el backend está caído, los mensajes que no se pudieron entregar van a `session/dead-letter.jsonl`.
- Baileys y whatsapp-web.js son ambos protocolo no oficial de WhatsApp Web (no la API Business de
  Meta). Usar un número secundario para el bridge, no el número principal, para acotar el riesgo de
  baneo por automatización.

## Desarrollo

El código se copia a la imagen en build (no hay bind-mount), así que tras cambiarlo:

```bash
docker compose build bridge && docker compose up -d bridge
```
