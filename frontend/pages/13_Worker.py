"""
Página del Worker continuo (procesa colas automáticamente).

El worker drena en orden transcribe → extract → caption → embed cada N segundos.
Esta página muestra su estado y permite pausar / reanudar / forzar un tick.
"""

import streamlit as st

from lib.api_client import APIClient

st.set_page_config(page_title="Worker", page_icon="⚙️", layout="wide")
st.title("⚙️ Worker continuo")
st.caption("Drena las colas automáticamente. En reposo cuando no hay trabajo.")

api = APIClient()


try:
    s = api.worker_status()
except Exception as e:  # noqa: BLE001
    st.error(f"No pude traer status: {e}")
    st.stop()


# Estado general
estado_str = (
    "🔴 deshabilitado" if not s["enabled"] else
    "⏸️ pausado" if s["paused"] else
    "🟢 corriendo" if s["running"] else
    "⚠️ detenido"
)
cols = st.columns([2, 1, 1, 1, 1])
cols[0].metric("Estado", estado_str)
cols[1].metric("Intervalo", f"{s['interval_s']}s")
cols[2].metric("Ticks", f"{s['ticks_total']:,}", help=f"{s['ticks_con_trabajo']} con trabajo")
cols[3].metric("Último tick", (s["last_tick_at"] or "—")[:19].replace("T", " "))
cols[4].metric("Duración", f"{s.get('last_tick_duration_ms') or 0:,} ms")

c1, c2, c3 = st.columns([1, 1, 6])
if s["running"] and not s["paused"]:
    if c1.button("⏸️ Pausar", use_container_width=True):
        api.worker_pause()
        st.rerun()
elif s["running"] and s["paused"]:
    if c1.button("▶️ Reanudar", use_container_width=True):
        api.worker_resume()
        st.rerun()
if c2.button("⚡ Tick ahora", use_container_width=True, disabled=not s["enabled"]):
    with st.spinner("Forzando tick…"):
        try:
            r = api.worker_tick()
            st.success(f"Tick OK · procesado total: {r['ultimo_resultado'].get('total_procesado', 0)}")
        except Exception as e:  # noqa: BLE001
            st.error(f"Error: {e}")
    st.rerun()

st.divider()

# Acumulados (desde el inicio del worker)
st.subheader("📊 Acumulado desde arranque")
ac = s["acumulado"]
acum_cols = st.columns(5)
acum_cols[0].metric("Transcribe", f"{ac['transcribe_procesados']:,}")
acum_cols[1].metric("Extract", f"{ac['extract_procesados']:,}")
acum_cols[2].metric("Caption", f"{ac['caption_procesados']:,}")
acum_cols[3].metric("Embed", f"{ac['embed_procesados']:,}")
acum_cols[4].metric("Errores", f"{ac['errores']:,}", delta_color="inverse")

# Batch sizes
with st.expander("🔧 Batch sizes (env: WORKER_BATCH_*)"):
    for etapa, n in s["batch"].items():
        st.markdown(f"- **{etapa}**: {n} jobs por tick")
    st.caption(f"Intervalo: {s['interval_s']}s — env: `WORKER_INTERVAL_S`")

# Último tick
if s.get("ultimo_resultado"):
    st.divider()
    st.subheader("🕐 Último tick")
    ult = s["ultimo_resultado"]
    cols = st.columns(4)
    for i, (etapa, info) in enumerate(ult.get("etapas", {}).items()):
        with cols[i % 4]:
            if isinstance(info, dict) and "error" not in info:
                st.markdown(f"**{etapa}**")
                st.caption(f"procesados: {info.get('procesados', 0)} · pendientes: {info.get('pendientes_restantes', 0)}")
            else:
                st.markdown(f"**{etapa}**")
                st.error(info.get("error", "?"))

# Auto-refresh cada 30s
st.divider()
auto = st.toggle("🔄 Auto-refresh cada 30s", value=False)
if auto:
    import time
    time.sleep(30)
    st.rerun()
