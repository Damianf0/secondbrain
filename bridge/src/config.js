/**
 * Configuración del bridge — todo vía variables de entorno (con defaults sanos).
 */

function envBool(name, def) {
  const v = process.env[name];
  if (v === undefined || v === "") return def;
  return !["0", "false", "no", "off"].includes(String(v).toLowerCase());
}

export const config = {
  // Puerto HTTP del bridge (sirve /status, /qr, /health)
  port: parseInt(process.env.BRIDGE_PORT || "3001", 10),

  // Backend FastAPI al que se reenvían los mensajes
  backendUrl: (process.env.BACKEND_URL || "http://backend:8000").replace(/\/$/, ""),
  ingestPath: process.env.BRIDGE_INGEST_PATH || "/api/bridge/whatsapp/ingest",

  // Dónde persiste la sesión de WhatsApp (debe ser un volumen Docker).
  // Baileys guarda las credenciales multi-file en un subdirectorio de acá
  // (ver whatsapp.js) para no mezclarse con el dead-letter.jsonl de backend.js.
  sessionPath: process.env.BRIDGE_SESSION_PATH || "/app/session",

  // Nivel de log de Baileys (pino). "warn" por default: en "info"/"debug" es muy verboso.
  logLevel: process.env.BRIDGE_LOG_LEVEL || "warn",

  // Emparejamiento por código en vez de QR: si viene un número acá, en el primer
  // arranque el bridge pide un código de 8 caracteres (se ve en los logs) para
  // escribir en el cel — WhatsApp > Dispositivos vinculados > Vincular con
  // número de teléfono — en vez de escanear el QR. Formato: solo dígitos, con
  // código de país (ej. 5492234567890). Patrón tomado de wa-avatars-baileys
  // (workbench-reforma-2026), que ya lo usa en producción.
  pairNumber: (process.env.BRIDGE_PAIR_NUMBER || "").replace(/\D/g, ""),

  // Si true, también captura mensajes salientes (los que mando yo)
  captureOutgoing: envBool("BRIDGE_CAPTURE_OUTGOING", true),

  // Si true, captura listas de difusión (@broadcast). Los Estados (status@broadcast)
  // se filtran SIEMPRE, sin importar este flag.
  includeBroadcasts: envBool("BRIDGE_INCLUDE_BROADCASTS", false),

  // Reintentos al postear al backend
  backendRetries: parseInt(process.env.BRIDGE_BACKEND_RETRIES || "4", 10),
  backendRetryBaseMs: parseInt(process.env.BRIDGE_BACKEND_RETRY_MS || "1500", 10),

  // Tipos de media para descargar y reenviar al backend (los tipos vienen del map
  // MEDIA_TYPE_MAP de whatsapp.js: audio, imagen, video, documento, sticker, gif).
  // Default: audio (Sprint 7) + documento (Sprint 6) + imagen (Sprint 5). Video y
  // sticker quedan afuera por default (videos pesan mucho, stickers no aportan).
  downloadMediaTypes: (process.env.BRIDGE_DOWNLOAD_MEDIA_TYPES || "audio,documento,imagen")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean),

  // Tamaño máximo (MB) a descargar. Audios suelen ser <1MB, fotos ~1-5MB, videos
  // pueden ser 16MB. Si excede, se ignora el binario y queda solo metadata.
  maxMediaMB: parseInt(process.env.BRIDGE_MAX_MEDIA_MB || "20", 10),
};
