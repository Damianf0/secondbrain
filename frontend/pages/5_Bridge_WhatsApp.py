"""
Página del Bridge WhatsApp en vivo (Sprint 2).

- Muestra el estado de conexión del container `bridge` (whatsapp-web.js)
- Si hace falta vincular el dispositivo, muestra el QR para escanear
- Muestra contadores y los últimos mensajes capturados en tiempo real
"""

import os
import time

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
BRIDGE_URL = os.getenv("BRIDGE_URL", "http://bridge:3001")

st.set_page_config(page_title="Bridge WhatsApp", page_icon="📡", layout="wide")
st.title("📡 Bridge WhatsApp")
st.caption("Sprint 2 — Captura de mensajes en tiempo real (whatsapp-web.js)")

# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------
col_a, col_b = st.columns([1, 4])
with col_a:
    refrescar = st.button("🔄 Actualizar")
with col_b:
    auto = st.checkbox("Auto-actualizar cada 5 s", value=False)

# ---------------------------------------------------------------------------
# Estado del bridge
# ---------------------------------------------------------------------------
estado = None
error_bridge = None
try:
    r = httpx.get(f"{BRIDGE_URL}/status", timeout=5)
    r.raise_for_status()
    estado = r.json()
except Exception as e:  # noqa: BLE001
    error_bridge = str(e)

if error_bridge:
    st.error(
        f"No se pudo contactar al bridge en `{BRIDGE_URL}`.\n\n"
        f"¿Está levantado? `docker compose up -d bridge` · detalle: {error_bridge}"
    )
else:
    status = estado.get("status", "?")
    _ICONOS = {
        "ready": "✅",
        "qr": "📱",
        "authenticated": "🔐",
        "starting": "⏳",
        "disconnected": "🔌",
        "auth_failure": "❌",
        "error": "💥",
    }
    icono = _ICONOS.get(status, "❔")

    st.subheader(f"{icono} Estado: `{status}`")
    if estado.get("detail"):
        st.caption(estado["detail"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cuenta", estado.get("account_name") or "—")
    c2.metric("Número", estado.get("account_phone") or "—")
    c3.metric("Vistos", estado.get("messages_seen", 0))
    c4.metric("Reenviados", estado.get("messages_forwarded", 0))

    c5, c6, c7 = st.columns(3)
    c5.metric("Duplicados", estado.get("messages_duplicated", 0))
    c6.metric("Fallidos", estado.get("messages_failed", 0))
    c7.metric("Captura salientes", "sí" if estado.get("capture_outgoing") else "no")

    # ---------------------------------------------------------------------------
    # QR para vincular
    # ---------------------------------------------------------------------------
    if status in ("qr", "starting", "auth_failure", "disconnected") and estado.get("has_qr"):
        st.divider()
        st.subheader("📱 Vincular dispositivo")
        st.markdown(
            "En el teléfono: **WhatsApp → ⋮ → Dispositivos vinculados → "
            "Vincular un dispositivo** y escaneá este código:"
        )
        try:
            rq = httpx.get(f"{BRIDGE_URL}/qr.png", timeout=5)
            if rq.status_code == 200:
                st.image(rq.content, width=320)
            else:
                st.info("El bridge todavía no generó el QR. Esperá unos segundos y actualizá.")
        except Exception as e:  # noqa: BLE001
            st.warning(f"No se pudo traer el QR: {e}")
        st.caption("El QR se renueva solo; si expira, actualizá la página.")
    elif status == "ready":
        st.success("Dispositivo vinculado. Capturando mensajes en vivo.")
    elif status in ("starting", "authenticated"):
        st.info("El bridge está arrancando / autenticando. Actualizá en unos segundos.")
    elif status == "auth_failure":
        st.error("Falló la autenticación. Puede que haya que borrar la sesión y re-escanear.")

    st.caption(f"Último evento: {estado.get('last_event', '—')} · arrancó: {estado.get('started_at', '—')}")

# ---------------------------------------------------------------------------
# Últimos mensajes capturados
# ---------------------------------------------------------------------------
st.divider()
st.subheader("💬 Últimos mensajes capturados en vivo")

try:
    rm = httpx.get(f"{BACKEND_URL}/api/bridge/whatsapp/recent", params={"limit": 30}, timeout=10)
    rm.raise_for_status()
    mensajes = rm.json()
    if not mensajes:
        st.info("Todavía no se capturó ningún mensaje en vivo. Mandate un mensaje a vos mismo para probar.")
    else:
        filas = []
        for m in mensajes:
            flecha = "⬅️ entrante" if m["direccion"] == "entrante" else "➡️ saliente"
            cuerpo = m["contenido"] or ""
            if m["es_media"]:
                cuerpo = f"📎 [{m.get('media_tipo') or 'media'}] {cuerpo}".strip()
            filas.append(
                {
                    "fecha": (m["fecha"] or "")[:19].replace("T", " "),
                    "chat": ("👥 " if m.get("is_group") else "") + m["conversation_id"],
                    "dir": flecha,
                    "de": m.get("sender_name") or "—",
                    "mensaje": cuerpo[:160],
                }
            )
        st.dataframe(filas, use_container_width=True, hide_index=True)
except Exception as e:  # noqa: BLE001
    st.warning(f"No se pudieron traer los mensajes recientes: {e}")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
if refrescar:
    st.rerun()
if auto:
    time.sleep(5)
    st.rerun()
