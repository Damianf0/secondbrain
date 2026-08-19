/**
 * Cliente de WhatsApp basado en Baileys (WebSocket directo al protocolo de
 * WhatsApp Web multi-device, sin browser/Chromium de por medio).
 *
 * - Mantiene la sesión en disco (useMultiFileAuthState -> volumen Docker,
 *   en un subdirectorio propio para no mezclarse con el dead-letter.jsonl
 *   de backend.js)
 * - Expone un objeto `state` con el estado de conexión y el QR actual
 *   (mismo shape que la versión whatsapp-web.js, para no tocar server.js)
 * - Captura mensajes entrantes y, si está habilitado, salientes, y los
 *   reenvía al backend
 * - Reconecta solo ante cortes de red/servidor; si la sesión se cierra
 *   desde el teléfono (logout), resetea las credenciales y pide un QR nuevo
 *
 * Filtros:
 *   - Estados de WhatsApp (status@broadcast): se ignoran SIEMPRE
 *   - Listas de difusión (@broadcast): se ignoran salvo BRIDGE_INCLUDE_BROADCASTS=true
 *   - Solo `messages.upsert` de tipo "notify" (mensajes en vivo). El backfill
 *     de historial que Baileys puede traer al reconectar NO se reenvía —
 *     eso es del import de exports .txt (Sprint 1), no de este bridge.
 *
 * Identificadores:
 *   - 1:1   -> conversation_id = teléfono E.164 si se puede resolver, si no el JID
 *   - grupo -> conversation_id = JID estable del grupo (...@g.us); el nombre humano
 *              viaja aparte (group_name) porque puede cambiar
 *
 * Decisión de diseño (heredada de la versión anterior): el bridge es "tonto"
 * — reenvía cada mensaje individual con su timestamp. NO acumula mensajes
 * consecutivos (eso es del pipeline de tagging, Sprint 3).
 */

import fs from "node:fs";
import path from "node:path";

import { Boom } from "@hapi/boom";
import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  getContentType,
  jidNormalizedUser,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import pino from "pino";
import qrcode from "qrcode";

import { config } from "./config.js";
import { sendToBackend } from "./backend.js";

const logger = pino({ level: config.logLevel });

// Subdirectorio propio: useMultiFileAuthState tira varios archivos sueltos
// (creds.json, session-*.json, etc.) y no queremos que convivan con el
// dead-letter.jsonl de backend.js en la raíz del volumen.
const authDir = path.join(config.sessionPath, "baileys");

// --------------------------------------------------------------------------
// Estado compartido (lo lee el server HTTP) — mismo shape que la versión
// whatsapp-web.js para no tener que tocar server.js.
// --------------------------------------------------------------------------

export const state = {
  status: "starting", // starting | qr | authenticated | ready | disconnected | auth_failure | error
  qrDataUrl: null,
  qrString: null,
  accountPhone: null, // teléfono del usuario en formato E.164, p.ej. "+54XXXXXXXXXX"
  accountName: null,
  lastEvent: new Date().toISOString(),
  startedAt: new Date().toISOString(),
  messagesSeen: 0,
  messagesForwarded: 0,
  messagesDuplicated: 0,
  messagesSkipped: 0, // estados / difusiones / no resolubles / no-notify
  messagesFailed: 0,
  detail: null,
};

function setStatus(status, extra = {}) {
  state.status = status;
  state.lastEvent = new Date().toISOString();
  Object.assign(state, extra);
  const ex = Object.keys(extra).length ? JSON.stringify(extra) : "";
  console.log(`[wa] status=${status} ${ex}`);
}

// --------------------------------------------------------------------------
// Helpers de JIDs y tipos de contenido
// --------------------------------------------------------------------------

/**
 * "5492234567890@s.whatsapp.net" (con o sin sufijo ":device") -> "+5492234567890".
 *
 * Los JID `@lid` (identificador de privacidad que WhatsApp usa como remitente
 * en vez del teléfono real, típicamente 13+ dígitos) NO se resuelven acá a
 * propósito — no son números de teléfono aunque parezcan uno, resolverlos mal
 * generaría personas canónicas con "teléfonos" falsos. Confirmado contra
 * wa-avatars-baileys (workbench-reforma-2026), que se topa con el mismo caso.
 * Sender/conversation quedan con el JID crudo en ese caso.
 */
function jidToPhone(jid) {
  if (!jid) return null;
  const m = String(jid).match(/^(\d+)(?::\d+)?@s\.whatsapp\.net$/);
  return m ? "+" + m[1] : null;
}

function isStatusBroadcast(jid) {
  return String(jid || "") === "status@broadcast";
}

function isBroadcastList(jid) {
  return /@broadcast$/.test(String(jid || "")) && !isStatusBroadcast(jid);
}

function isGroupJid(jid) {
  return /@g\.us$/.test(String(jid || ""));
}

const CONTENT_TYPES_SOPORTADOS = new Set([
  "conversation",
  "extendedTextMessage",
  "imageMessage",
  "videoMessage",
  "audioMessage",
  "documentMessage",
  "documentWithCaptionMessage",
  "stickerMessage",
]);

/** Tipo "estilo whatsapp-web.js" (chat/image/video/gif/audio/ptt/document/sticker). */
function waTypeFromContentType(contentType, message) {
  switch (contentType) {
    case "conversation":
    case "extendedTextMessage":
      return "chat";
    case "imageMessage":
      return "image";
    case "videoMessage":
      return message.videoMessage?.gifPlayback ? "gif" : "video";
    case "audioMessage":
      return message.audioMessage?.ptt ? "ptt" : "audio";
    case "documentMessage":
    case "documentWithCaptionMessage":
      return "document";
    case "stickerMessage":
      return "sticker";
    default:
      return contentType;
  }
}

const MEDIA_TYPE_MAP = {
  image: "imagen",
  video: "video",
  gif: "gif",
  audio: "audio",
  ptt: "audio", // push-to-talk = nota de voz
  document: "documento",
  sticker: "sticker",
};

const MEDIA_WA_TYPES = new Set(Object.keys(MEDIA_TYPE_MAP));

function extractBody(message, contentType) {
  switch (contentType) {
    case "conversation":
      return message.conversation || "";
    case "extendedTextMessage":
      return message.extendedTextMessage?.text || "";
    case "imageMessage":
      return message.imageMessage?.caption || "";
    case "videoMessage":
      return message.videoMessage?.caption || "";
    case "documentMessage":
      return message.documentMessage?.caption || message.documentMessage?.fileName || "";
    case "documentWithCaptionMessage":
      return message.documentWithCaptionMessage?.message?.documentMessage?.caption || "";
    default:
      return "";
  }
}

function mediaMeta(contentType, message) {
  if (contentType === "documentWithCaptionMessage") {
    const doc = message.documentWithCaptionMessage?.message?.documentMessage;
    return { filename: doc?.fileName || null, mimetype: doc?.mimetype || null };
  }
  const node = message[contentType];
  return { filename: node?.fileName || null, mimetype: node?.mimetype || null };
}

// --------------------------------------------------------------------------
// Cache de nombres de grupo (sock.groupMetadata pega a WhatsApp cada vez)
// --------------------------------------------------------------------------

const groupNameCache = new Map(); // jid -> { subject, fetchedAt }
const GROUP_CACHE_TTL_MS = 60 * 60 * 1000; // 1h

async function resolverNombreGrupo(sock, jid) {
  const cached = groupNameCache.get(jid);
  if (cached && Date.now() - cached.fetchedAt < GROUP_CACHE_TTL_MS) return cached.subject;
  try {
    const meta = await sock.groupMetadata(jid);
    const subject = meta?.subject || null;
    groupNameCache.set(jid, { subject, fetchedAt: Date.now() });
    return subject;
  } catch {
    return cached ? cached.subject : null;
  }
}

// --------------------------------------------------------------------------
// Sesión: reset de credenciales (logout desde el teléfono)
// --------------------------------------------------------------------------

function resetAuthFolder() {
  try {
    fs.rmSync(authDir, { recursive: true, force: true });
    console.log("[wa] sesión reseteada — va a pedir un QR nuevo al reconectar");
  } catch (err) {
    console.warn(`[wa] no pude resetear la sesión: ${err && err.message ? err.message : err}`);
  }
}

// --------------------------------------------------------------------------
// Mensajes
// --------------------------------------------------------------------------

async function handleMessage(sock, msg) {
  const fromMe = msg.key?.fromMe === true;
  if (fromMe && !config.captureOutgoing) return;
  if (!msg.message) return; // mensajes de protocolo, revocados, sin contenido

  const remoteJid = msg.key?.remoteJid;

  if (isStatusBroadcast(remoteJid)) {
    state.messagesSkipped++;
    return; // Estados de WhatsApp: nunca
  }
  if (isBroadcastList(remoteJid) && !config.includeBroadcasts) {
    state.messagesSkipped++;
    return; // Listas de difusión: solo si BRIDGE_INCLUDE_BROADCASTS=true
  }

  const contentType = getContentType(msg.message);
  if (!contentType || !CONTENT_TYPES_SOPORTADOS.has(contentType)) {
    state.messagesSkipped++; // reacciones, ediciones, encuestas, etc. — no son items
    return;
  }

  state.messagesSeen++;

  try {
    const isGroup = isGroupJid(remoteJid);

    // --- conversation_id ---
    let conversationId;
    let groupName = null;
    if (isGroup) {
      conversationId = remoteJid; // JID estable @g.us
      groupName = await resolverNombreGrupo(sock, remoteJid);
    } else {
      conversationId = jidToPhone(remoteJid) || remoteJid;
    }

    // --- remitente ---
    let senderJid = null;
    let senderPhone = null;
    let senderName = null;
    if (fromMe) {
      senderJid = sock.user?.id ? jidNormalizedUser(sock.user.id) : null;
      senderPhone = state.accountPhone || jidToPhone(senderJid);
      senderName = state.accountName;
    } else if (isGroup) {
      // En un grupo, el autor real viaja en key.participant (NO remoteJid, que es el grupo)
      senderJid = msg.key?.participant || null;
      senderPhone = jidToPhone(senderJid);
      senderName = msg.pushName || null;
    } else {
      senderJid = remoteJid;
      senderPhone = jidToPhone(remoteJid);
      senderName = msg.pushName || null;
    }

    const waType = waTypeFromContentType(contentType, msg.message);
    const hasMedia = MEDIA_WA_TYPES.has(waType);
    const mediaTipo = hasMedia ? MEDIA_TYPE_MAP[waType] || "desconocido" : null;

    // Descargar binario si configuramos descargar este tipo (audio/documento/imagen
    // por default — ver config.js). No tirar la ingesta entera si la descarga falla:
    // queda solo metadata.
    let mediaB64 = null;
    let mediaFilename = null;
    let mediaMimetype = null;
    if (hasMedia) {
      const wantDownload = mediaTipo && config.downloadMediaTypes.includes(mediaTipo);
      console.log(
        `[wa] media content_type=${contentType} → tipo=${mediaTipo} download=${wantDownload} fromMe=${fromMe}`,
      );
      if (wantDownload) {
        try {
          const buffer = await downloadMediaMessage(
            msg,
            "buffer",
            {},
            { logger, reuploadRequest: sock.updateMediaMessage },
          );
          const sizeMB = buffer.length / (1024 * 1024);
          if (sizeMB > config.maxMediaMB) {
            console.warn(
              `[wa] media descartado por tamaño (${sizeMB.toFixed(1)}MB > ${config.maxMediaMB}MB) — ${mediaTipo}`,
            );
          } else {
            const meta = mediaMeta(contentType, msg.message);
            mediaB64 = buffer.toString("base64");
            mediaFilename = meta.filename;
            mediaMimetype = meta.mimetype;
            console.log(
              `[wa] media descargado ${mediaTipo} · ${sizeMB.toFixed(2)}MB · ${mediaMimetype || "?"}`,
            );
          }
        } catch (err) {
          console.warn(
            `[wa] no pude descargar media (${mediaTipo}): ${err && err.message ? err.message : err}`,
          );
        }
      }
    }

    const tsSeconds = Number(msg.messageTimestamp) || Math.floor(Date.now() / 1000);

    const payload = {
      source_id: msg.key?.id || null,
      conversation_id: conversationId,
      chat_jid: remoteJid,
      is_group: isGroup,
      group_name: groupName,
      from_me: fromMe,
      sender_phone: senderPhone,
      sender_name: senderName,
      sender_jid: senderJid,
      account_phone: state.accountPhone,
      account_name: state.accountName,
      body: extractBody(msg.message, contentType) || "",
      timestamp: new Date(tsSeconds * 1000).toISOString(),
      wa_type: waType,
      has_media: hasMedia,
      media_type: mediaTipo,
      media_b64: mediaB64,
      media_filename: mediaFilename,
      media_mimetype: mediaMimetype,
    };

    const result = await sendToBackend(payload);
    if (result === null) {
      state.messagesFailed++;
    } else if (result && result.status === "duplicate") {
      state.messagesDuplicated++;
    } else {
      state.messagesForwarded++;
    }
  } catch (err) {
    state.messagesFailed++;
    console.error(`[wa] error procesando mensaje: ${err && err.message ? err.message : err}`);
  }
}

async function handleMessagesUpsert(sock, { messages, type }) {
  // "notify" = mensajes nuevos en vivo. "append"/"prepend" son backfill de
  // historial (por ejemplo al reconectar) — eso no es trabajo de este bridge.
  if (type !== "notify") return;
  for (const msg of messages) {
    await handleMessage(sock, msg);
  }
}

// --------------------------------------------------------------------------
// Conexión (con reconexión automática)
// --------------------------------------------------------------------------

let currentSock = null;
let reconnecting = false;

function reportFatal(err) {
  console.error(`[bridge] fallo (re)conectando: ${err && err.stack ? err.stack : err}`);
  setStatus("error", { detail: err && err.message ? err.message : String(err) });
}

async function connect() {
  const { state: authState, saveCreds } = await useMultiFileAuthState(authDir);

  // Pineamos la versión de protocolo más reciente conocida en vez de dejar que
  // Baileys use su default embebido (que puede haber quedado atrás) — WhatsApp
  // cambia el protocolo seguido y una versión vieja puede desconectar al toque.
  // Si falla la consulta (sin red al arrancar, etc.), seguimos con el default.
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));

  const usePairing = config.pairNumber.length >= 10;

  const sock = makeWASocket({
    version,
    auth: authState,
    logger,
    browser: ["SecondBrain", "Chrome", "120.0.0"],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });
  currentSock = sock;

  sock.ev.on("creds.update", saveCreds);

  // Emparejamiento por código (BRIDGE_PAIR_NUMBER): alternativa al QR — se pide
  // una sola vez, antes de que exista sesión. Igual que con el QR, una vez
  // vinculado no vuelve a pedirse mientras la sesión persista en el volumen.
  if (usePairing && !sock.authState.creds.registered) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(config.pairNumber);
        setStatus("qr", { detail: `Código de emparejamiento: ${code}` });
        console.log(
          `[wa] código de emparejamiento: ${code} — WhatsApp → Dispositivos vinculados → ` +
            `Vincular dispositivo → Vincular con número de teléfono`,
        );
      } catch (err) {
        console.error(`[wa] error pidiendo código de emparejamiento: ${err && err.message ? err.message : err}`);
      }
    }, 3000);
  }

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr && !usePairing) {
      state.qrString = qr;
      try {
        state.qrDataUrl = await qrcode.toDataURL(qr, { margin: 1, width: 320 });
      } catch {
        state.qrDataUrl = null;
      }
      setStatus("qr", { detail: "Escaneá el QR desde el panel" });
      try {
        console.log(await qrcode.toString(qr, { type: "terminal", small: true }));
      } catch {
        /* noop */
      }
    }

    if (connection === "open") {
      const ownJid = sock.user?.id ? jidNormalizedUser(sock.user.id) : null;
      setStatus("ready", {
        accountPhone: jidToPhone(ownJid),
        accountName: sock.user?.name || sock.user?.verifiedName || null,
        qrDataUrl: null,
        qrString: null,
        detail: null,
      });
    }

    if (connection === "close") {
      const err = lastDisconnect?.error;
      const statusCode = err instanceof Boom ? err.output?.statusCode : null;
      const loggedOut = statusCode === DisconnectReason.loggedOut;

      if (loggedOut) {
        setStatus("auth_failure", {
          detail: "Sesión cerrada desde el teléfono — hace falta escanear un QR nuevo",
          qrDataUrl: null,
          qrString: null,
        });
        resetAuthFolder();
        setTimeout(() => connect().catch(reportFatal), 2000);
        return;
      }

      setStatus("disconnected", { detail: err && err.message ? err.message : "conexión cerrada" });
      if (!reconnecting) {
        reconnecting = true;
        setTimeout(() => {
          reconnecting = false;
          connect().catch(reportFatal);
        }, 3000);
      }
    }
  });

  sock.ev.on("messages.upsert", (payload) => {
    handleMessagesUpsert(sock, payload).catch((err) => {
      console.error(`[wa] error en messages.upsert: ${err && err.message ? err.message : err}`);
    });
  });

  return sock;
}

export async function start() {
  await connect();
}

/** Cierra la conexión sin invalidar la sesión (no es logout). */
export async function stop() {
  try {
    currentSock?.end(undefined);
  } catch {
    /* noop */
  }
}
