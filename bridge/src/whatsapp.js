/**
 * Cliente de WhatsApp basado en whatsapp-web.js.
 *
 * - Mantiene la sesión en disco (LocalAuth -> volumen Docker)
 * - Expone un objeto `state` con el estado de conexión y el QR actual
 * - Captura mensajes entrantes (`message`) y, si está habilitado, salientes
 *   (`message_create` filtrado por `fromMe`) y los reenvía al backend
 *
 * Filtros:
 *   - Estados de WhatsApp (status@broadcast): se ignoran SIEMPRE
 *   - Listas de difusión (@broadcast): se ignoran salvo BRIDGE_INCLUDE_BROADCASTS=true
 *
 * Identificadores:
 *   - 1:1   -> conversation_id = teléfono E.164 si se puede resolver, si no el JID
 *   - grupo -> conversation_id = JID estable del grupo (...@g.us); el nombre humano
 *              viaja aparte (group_name) porque puede cambiar
 *
 * Decisión de diseño: el bridge es "tonto" — reenvía cada mensaje individual con su
 * timestamp. NO acumula mensajes consecutivos (eso es del pipeline de tagging, Sprint 3).
 */

import qrcode from "qrcode";
import pkg from "whatsapp-web.js";

import { config } from "./config.js";
import { sendToBackend } from "./backend.js";

const { Client, LocalAuth } = pkg;

// --------------------------------------------------------------------------
// Estado compartido (lo lee el server HTTP)
// --------------------------------------------------------------------------

export const state = {
  status: "starting", // starting | qr | authenticated | ready | disconnected | auth_failure | error
  qrDataUrl: null,
  qrString: null,
  accountPhone: null, // mi número, e.g. "+5492235594007"
  accountName: null,
  lastEvent: new Date().toISOString(),
  startedAt: new Date().toISOString(),
  messagesSeen: 0,
  messagesForwarded: 0,
  messagesDuplicated: 0,
  messagesSkipped: 0, // estados / difusiones / no resolubles
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
// Helpers
// --------------------------------------------------------------------------

const MEDIA_TYPE_MAP = {
  image: "imagen",
  video: "video",
  audio: "audio",
  ptt: "audio", // push-to-talk = nota de voz
  document: "documento",
  sticker: "sticker",
  gif: "gif",
};

const MEDIA_WA_TYPES = new Set(["image", "video", "audio", "ptt", "document", "sticker"]);

/** "5492234567890@c.us" -> "+5492234567890" ; null si no es un JID de contacto clásico */
function jidToPhone(jid) {
  if (!jid) return null;
  const m = String(jid).match(/^(\d+)@c\.us$/);
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

/** Extrae nombre + teléfono de un Contact de whatsapp-web.js (tolera campos faltantes). */
function contactInfo(contact) {
  if (!contact) return { name: null, phone: null };
  const name =
    contact.name ||
    contact.pushname ||
    contact.shortName ||
    contact.verifiedName ||
    contact.formattedName ||
    null;
  let phone = null;
  // contact.number suele ser los dígitos sin "+", incluso cuando el JID es @lid
  if (contact.number) phone = "+" + String(contact.number).replace(/\D/g, "");
  else phone = jidToPhone(contact.id && contact.id._serialized);
  return { name, phone };
}

// --------------------------------------------------------------------------
// Cliente
// --------------------------------------------------------------------------

export function createClient() {
  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: config.sessionPath }),
    puppeteer: {
      headless: true,
      executablePath: config.chromiumPath,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-first-run",
        "--no-zygote",
      ],
    },
  });

  client.on("qr", async (qr) => {
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
  });

  client.on("loading_screen", (percent, message) => {
    console.log(`[wa] loading ${percent}% ${message || ""}`);
  });

  client.on("authenticated", () => {
    setStatus("authenticated", { qrDataUrl: null, qrString: null, detail: null });
  });

  client.on("auth_failure", (message) => {
    setStatus("auth_failure", { detail: String(message), qrDataUrl: null, qrString: null });
  });

  client.on("ready", () => {
    let extra = {};
    try {
      const user = client.info && client.info.wid ? client.info.wid.user : null;
      extra = {
        accountPhone: user ? "+" + user : null,
        accountName: (client.info && client.info.pushname) || null,
        detail: null,
      };
    } catch {
      /* noop */
    }
    setStatus("ready", extra);
  });

  client.on("disconnected", (reason) => {
    setStatus("disconnected", { detail: String(reason) });
  });

  client.on("change_state", (s) => {
    state.lastEvent = new Date().toISOString();
    console.log(`[wa] change_state ${s}`);
  });

  // ------------------------------------------------------------------------
  // Resolución del remitente
  // ------------------------------------------------------------------------

  async function resolverContacto(jid) {
    if (!jid) return { name: null, phone: null, jid: null };
    let info = { name: null, phone: jidToPhone(jid), jid };
    try {
      const contact = await client.getContactById(jid);
      const ci = contactInfo(contact);
      info = { name: ci.name, phone: ci.phone || info.phone, jid };
    } catch {
      /* contacto no resoluble — nos quedamos con lo que se pueda */
    }
    return info;
  }

  // ------------------------------------------------------------------------
  // Mensajes
  // ------------------------------------------------------------------------

  async function handleMessage(msg) {
    const fromMe = msg.fromMe === true;
    if (fromMe && !config.captureOutgoing) return;

    // El JID del "otro lado" del chat: para entrantes msg.from, para salientes msg.to
    const peerJid = fromMe ? msg.to : msg.from;

    // --- Filtros ---
    if (isStatusBroadcast(peerJid) || isStatusBroadcast(msg.from)) {
      state.messagesSkipped++;
      return; // Estados de WhatsApp: nunca
    }
    if (isBroadcastList(peerJid) && !config.includeBroadcasts) {
      state.messagesSkipped++;
      return; // Listas de difusión: solo si BRIDGE_INCLUDE_BROADCASTS=true
    }

    state.messagesSeen++;

    try {
      const chat = await msg.getChat().catch(() => null);
      const chatJidRaw = (chat && chat.id && chat.id._serialized) || peerJid || null;
      const isGroup = isGroupJid(chatJidRaw) || (chat && chat.isGroup === true);

      // --- conversation_id ---
      let conversationId;
      let groupName = null;
      if (isGroup) {
        conversationId = chatJidRaw; // JID estable @g.us
        groupName = (chat && chat.name) || null;
      } else {
        conversationId = jidToPhone(chatJidRaw) || chatJidRaw || "desconocido";
      }

      // --- remitente ---
      let senderPhone = null;
      let senderName = null;
      let senderJid = null;
      if (fromMe) {
        senderPhone = state.accountPhone;
        senderName = state.accountName;
        senderJid = (client.info && client.info.wid && client.info.wid._serialized) || null;
      } else if (isGroup) {
        // En un grupo, el autor real es msg.author (NO msg.from, que es el grupo)
        senderJid = msg.author || null;
        if (senderJid) {
          const info = await resolverContacto(senderJid);
          senderName = info.name;
          senderPhone = info.phone;
        }
      } else {
        senderJid = chatJidRaw;
        const info = await resolverContacto(chatJidRaw);
        senderName = info.name;
        senderPhone = info.phone || jidToPhone(chatJidRaw);
        // Para 1:1, si conseguimos el teléfono real desde el contacto (caso @lid), úsalo de conversation_id
        if (senderPhone) conversationId = senderPhone;
      }

      const hasMedia = msg.hasMedia === true || MEDIA_WA_TYPES.has(msg.type);
      const mediaTipo = hasMedia ? MEDIA_TYPE_MAP[msg.type] || "desconocido" : null;

      // Descargar binario si configuramos descargar este tipo (Sprint 7: audio).
      // No tirar la ingesta entera si la descarga falla: queda solo metadata.
      let mediaB64 = null;
      let mediaFilename = null;
      let mediaMimetype = null;
      if (hasMedia) {
        const wantDownload = mediaTipo && config.downloadMediaTypes.includes(mediaTipo);
        console.log(
          `[wa] media wa_type=${msg.type} → tipo=${mediaTipo} hasMedia=${msg.hasMedia} ` +
            `download=${wantDownload} fromMe=${fromMe}`,
        );
        if (wantDownload) {
          try {
            const media = await msg.downloadMedia();
            if (!media) {
              console.warn(`[wa] downloadMedia() devolvió null para ${mediaTipo}`);
            } else if (!media.data) {
              console.warn(`[wa] downloadMedia() sin .data — keys=${Object.keys(media).join(",")}`);
            } else {
              const sizeBytes = (media.data.length * 3) / 4; // base64 → bytes aprox
              const sizeMB = sizeBytes / (1024 * 1024);
              if (sizeMB > config.maxMediaMB) {
                console.warn(
                  `[wa] media descartado por tamaño (${sizeMB.toFixed(1)}MB > ${config.maxMediaMB}MB) — ${mediaTipo}`,
                );
              } else {
                mediaB64 = media.data;
                mediaFilename = media.filename || null;
                mediaMimetype = media.mimetype || null;
                console.log(
                  `[wa] media descargado ${mediaTipo} · ${sizeMB.toFixed(2)}MB · ${mediaMimetype || "?"}`,
                );
              }
            }
          } catch (err) {
            console.warn(
              `[wa] no pude descargar media (${mediaTipo}): ${err && err.message ? err.message : err}`,
            );
          }
        }
      }

      const payload = {
        source_id: (msg.id && (msg.id._serialized || msg.id.id)) || null,
        conversation_id: conversationId,
        chat_jid: chatJidRaw,
        is_group: isGroup,
        group_name: groupName,
        from_me: fromMe,
        sender_phone: senderPhone,
        sender_name: senderName,
        sender_jid: senderJid,
        account_phone: state.accountPhone,
        account_name: state.accountName,
        body: msg.body || "",
        timestamp: msg.timestamp
          ? new Date(msg.timestamp * 1000).toISOString()
          : new Date().toISOString(),
        wa_type: msg.type || "chat",
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

  // Entrantes (no fromMe)
  client.on("message", (msg) => {
    handleMessage(msg);
  });

  // Salientes: message_create dispara para todos; acá solo nos quedamos con fromMe
  // (los entrantes ya los toma el listener de arriba, así no duplicamos).
  if (config.captureOutgoing) {
    client.on("message_create", (msg) => {
      if (msg.fromMe) handleMessage(msg);
    });
  }

  return client;
}
