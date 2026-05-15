/**
 * Cliente HTTP hacia el backend FastAPI.
 *
 * Postea cada mensaje capturado al endpoint de ingest, con reintentos.
 * Si después de los reintentos sigue fallando, lo escribe a un archivo
 * dead-letter dentro del volumen de sesión para no perder nada (pilar Vault).
 */

import fs from "node:fs";
import path from "node:path";
import { config } from "./config.js";

const ingestUrl = config.backendUrl + config.ingestPath;
const deadLetterFile = path.join(config.sessionPath, "dead-letter.jsonl");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Postea un payload al backend. Devuelve la respuesta JSON, o `null` si falló
 * definitivamente (en ese caso ya se escribió al dead-letter).
 */
export async function sendToBackend(payload) {
  const retries = Math.max(1, config.backendRetries);
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const res = await fetch(ingestUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        return await res.json().catch(() => ({}));
      }
      const text = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status} ${text.slice(0, 300)}`);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      if (attempt === retries) {
        console.error(`[backend] fallo definitivo tras ${retries} intentos: ${msg}`);
        appendDeadLetter(payload, msg);
        return null;
      }
      const delay = config.backendRetryBaseMs * attempt;
      console.warn(`[backend] intento ${attempt}/${retries} falló (${msg}); reintento en ${delay}ms`);
      await sleep(delay);
    }
  }
  return null;
}

function appendDeadLetter(payload, error) {
  try {
    fs.mkdirSync(path.dirname(deadLetterFile), { recursive: true });
    const line = JSON.stringify({ ...payload, _failed_at: new Date().toISOString(), _error: error });
    fs.appendFileSync(deadLetterFile, line + "\n");
    console.error(`[backend] mensaje guardado en dead-letter: ${deadLetterFile}`);
  } catch (e) {
    console.error(`[backend] no pude escribir el dead-letter: ${e && e.message ? e.message : e}`);
  }
}
