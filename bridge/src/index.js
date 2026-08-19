/**
 * Punto de entrada del bridge WhatsApp.
 *
 * Levanta el server HTTP y la conexión a WhatsApp (Baileys). El cliente
 * intenta restaurar la sesión del volumen; si no hay sesión válida, emite
 * un QR (visible en /qr y en el panel).
 */

import { startServer } from "./server.js";
import { start, stop, state } from "./whatsapp.js";

console.log("[bridge] SecondBrain · WhatsApp bridge (Baileys) — arrancando");

await startServer();

start().catch((err) => {
  console.error(`[bridge] fallo inicializando la conexión de WhatsApp: ${err && err.stack ? err.stack : err}`);
  state.status = "error";
  state.detail = err && err.message ? err.message : String(err);
});

async function shutdown(signal) {
  console.log(`[bridge] ${signal} recibido — cerrando…`);
  try {
    await stop();
  } catch {
    /* noop */
  }
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("unhandledRejection", (reason) => {
  console.error(`[bridge] unhandledRejection: ${reason && reason.stack ? reason.stack : reason}`);
});
