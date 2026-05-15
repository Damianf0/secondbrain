/**
 * Punto de entrada del bridge WhatsApp.
 *
 * Levanta el server HTTP y el cliente de WhatsApp. El cliente intenta
 * restaurar la sesión del volumen; si no hay sesión válida, emite un QR
 * (visible en /qr y en el panel de Streamlit).
 */

import fs from "node:fs";
import path from "node:path";

import { config } from "./config.js";
import { startServer } from "./server.js";
import { createClient, state } from "./whatsapp.js";

console.log("[bridge] SecondBrain · WhatsApp bridge — arrancando");

// Si el container anterior murió sin cerrar Chromium, quedan locks en el perfil
// (SingletonLock/Socket/Cookie) que impiden arrancar. Los limpiamos al iniciar.
function limpiarLocksChromium(dir) {
  let removed = 0;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return removed;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      removed += limpiarLocksChromium(full);
    } else if (/^Singleton(Lock|Socket|Cookie)$/.test(e.name)) {
      try {
        fs.unlinkSync(full);
        removed++;
      } catch {
        /* noop */
      }
    }
  }
  return removed;
}

const locksBorrados = limpiarLocksChromium(config.sessionPath);
if (locksBorrados > 0) console.log(`[bridge] limpié ${locksBorrados} lock(s) de Chromium de una corrida anterior`);

await startServer();

const client = createClient();

client.initialize().catch((err) => {
  console.error(`[bridge] fallo inicializando el cliente de WhatsApp: ${err && err.stack ? err.stack : err}`);
  state.status = "error";
  state.detail = err && err.message ? err.message : String(err);
});

async function shutdown(signal) {
  console.log(`[bridge] ${signal} recibido — cerrando…`);
  try {
    await client.destroy();
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
