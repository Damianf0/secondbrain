/**
 * Servidor HTTP minimalista del bridge.
 *
 *   GET /health   -> { ok, status }
 *   GET /status   -> estado de conexión + contadores
 *   GET /qr.json  -> { data_url, raw }  (404 si no hay QR pendiente)
 *   GET /qr.png   -> imagen PNG del QR    (404 si no hay QR pendiente)
 *   GET /qr       -> página HTML con auto-refresh para escanear desde el navegador
 */

import express from "express";

import { config } from "./config.js";
import { state } from "./whatsapp.js";

export function startServer() {
  const app = express();

  app.get("/health", (_req, res) => {
    res.json({ ok: true, status: state.status });
  });

  app.get("/status", (_req, res) => {
    res.json({
      status: state.status,
      detail: state.detail,
      account_phone: state.accountPhone,
      account_name: state.accountName,
      has_qr: Boolean(state.qrDataUrl),
      started_at: state.startedAt,
      last_event: state.lastEvent,
      capture_outgoing: config.captureOutgoing,
      include_broadcasts: config.includeBroadcasts,
      messages_seen: state.messagesSeen,
      messages_forwarded: state.messagesForwarded,
      messages_duplicated: state.messagesDuplicated,
      messages_skipped: state.messagesSkipped,
      messages_failed: state.messagesFailed,
    });
  });

  app.get("/qr.json", (_req, res) => {
    if (!state.qrDataUrl) {
      return res.status(404).json({ error: "no_qr", status: state.status });
    }
    res.json({ data_url: state.qrDataUrl, raw: state.qrString });
  });

  app.get("/qr.png", (_req, res) => {
    if (!state.qrDataUrl) {
      return res.status(404).type("text/plain").send("no hay QR pendiente");
    }
    const b64 = state.qrDataUrl.split(",")[1];
    res.type("image/png").send(Buffer.from(b64, "base64"));
  });

  app.get("/qr", (_req, res) => {
    const body = state.qrDataUrl
      ? `<img src="/qr.png?ts=${Date.now()}" width="320" alt="QR WhatsApp">`
      : `<p style="font-size:1.2rem">${
          state.status === "ready"
            ? "✅ Conectado" + (state.accountName ? " como " + state.accountName : "")
            : "Esperando QR… (estado: " + state.status + ")"
        }</p>`;
    res
      .type("text/html")
      .send(
        `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">` +
          `<title>SecondBrain · WhatsApp Bridge</title></head>` +
          `<body style="font-family:system-ui,sans-serif;text-align:center;padding:2rem">` +
          `<h2>SecondBrain · WhatsApp Bridge</h2><p>Estado: <b>${state.status}</b></p>${body}` +
          `<p style="color:#888;font-size:.8rem;margin-top:2rem">Se actualiza solo cada 5s</p>` +
          `</body></html>`,
      );
  });

  return new Promise((resolve) => {
    const server = app.listen(config.port, () => {
      console.log(`[bridge] HTTP escuchando en :${config.port}`);
      resolve(server);
    });
  });
}
