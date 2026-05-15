"""
Página de Audios / Transcripción (Sprint 7).

- Estado: cuántos audios hay, cuántos tienen binario (en MinIO), cuántos están
  transcritos, cuántos jobs pendientes.
- Lista de audios pendientes con botón "Transcribir uno" y "Drenar cola".
- Listado de audios ya transcritos para ojear las transcripciones.
"""

import streamlit as st

from lib.api_client import APIClient

st.set_page_config(page_title="Audios", page_icon="🎙️", layout="wide")
st.title("🎙️ Audios y transcripciones")
st.caption("Sprint 7 — Whisper Large V3 Turbo local. Solo audios capturados en vivo (los históricos no tienen binario).")

api = APIClient()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
try:
    s = api.transcribe_stats()
except Exception as e:  # noqa: BLE001
    st.error(f"No pude traer stats: {e}")
    st.stop()

cols = st.columns(5)
cols[0].metric("Audios totales", f"{s['audios_total']:,}", help="items con media_tipo=audio")
cols[1].metric("Con binario", f"{s['audios_con_attachment']:,}", help="en MinIO (vía bridge en vivo)")
cols[2].metric("Sin binario", f"{s['audios_sin_binario']:,}", help="históricos sin .opus disponible")
cols[3].metric("Transcritos", f"{s['audios_transcritos']:,}")
cols[4].metric(
    "Jobs pendientes",
    f"{s['jobs_transcribe_pendientes']:,}",
    delta=f"{s['jobs_transcribe_fallidos']} fallidos" if s["jobs_transcribe_fallidos"] else None,
    delta_color="inverse" if s["jobs_transcribe_fallidos"] else "normal",
)

# Drenar cola
if s["jobs_transcribe_pendientes"]:
    if st.button(f"🛠️ Drenar cola ({s['jobs_transcribe_pendientes']} jobs)", use_container_width=False):
        with st.spinner("Transcribiendo audios encolados…"):
            try:
                r = api.transcribe_work(limit=min(50, s["jobs_transcribe_pendientes"]))
                st.success(
                    f"Procesados {r['procesados']} · ok {r['exitosos']} · "
                    f"fallidos {r['fallidos']} · quedan {r['pendientes_restantes']}"
                )
                if r.get("errores"):
                    with st.expander(f"Errores ({len(r['errores'])})"):
                        for err in r["errores"]:
                            st.code(err)
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Upload manual (test / fallback sin bridge)
# ---------------------------------------------------------------------------
with st.expander("⬆️ Subir audio manual (test / sin bridge)", expanded=False):
    st.caption("Sube un .opus/.mp3/.m4a directo al Vault para validar el pipeline. Quedan en conversation_id='manual_upload'.")
    up = st.file_uploader("Audio", type=["opus", "ogg", "mp3", "m4a", "aac", "wav", "flac"], key="audio_upload")
    cols = st.columns([2, 1, 1])
    conv_manual = cols[0].text_input("conversation_id", value="manual_upload")
    transcribir_ahora = cols[1].checkbox("Transcribir ya", value=True)
    if cols[2].button("Subir", type="primary", use_container_width=True, disabled=up is None):
        with st.spinner("Subiendo y transcribiendo…" if transcribir_ahora else "Subiendo…"):
            try:
                r = api.transcribe_upload(
                    up.name,
                    up.getvalue(),
                    up.type or "application/octet-stream",
                    conversation_id=conv_manual or "manual_upload",
                    transcribir_ahora=transcribir_ahora,
                )
                msg = f"OK · item {r['item_id']} · {r['size_bytes']/1024:.1f}KB"
                if r.get("duplicate_in_vault"):
                    msg += " (binario ya estaba en el Vault)"
                st.success(msg)
                if r.get("transcripcion"):
                    t = r["transcripcion"]
                    st.markdown(f"**Transcripción** ({t.get('chars')} chars, {t.get('duracion_s', '?')}s, idioma {t.get('idioma')}):")
                    item_id = r["item_id"]
                    # Buscar el texto recién guardado (no viene en la respuesta)
                    found = next((a for a in api.transcribe_pendientes(solo_pendientes=False, limit=20) if a["item_id"] == item_id), None)
                    if found and found.get("texto"):
                        st.info(f"> {found['texto']}")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Tabs: pendientes vs transcritos
# ---------------------------------------------------------------------------
tab_pend, tab_trans = st.tabs([f"⏳ Pendientes ({s['audios_con_attachment'] - s['audios_transcritos']})", f"✅ Transcritos ({s['audios_transcritos']})"])


def _fmt_dur(seg: float | None) -> str:
    if not seg:
        return "?"
    m, s2 = divmod(int(seg), 60)
    return f"{m}:{s2:02d}"


def _fmt_size(b: int | None) -> str:
    if not b:
        return "?"
    if b < 1024:
        return f"{b}B"
    if b < 1024 * 1024:
        return f"{b/1024:.1f}KB"
    return f"{b/(1024*1024):.1f}MB"


with tab_pend:
    audios = []
    try:
        audios = api.transcribe_pendientes(solo_pendientes=True, limit=100)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude listar: {e}")

    if not audios:
        st.info("No hay audios pendientes de transcribir.")
    else:
        st.caption(f"Mostrando {len(audios)} audios. La transcripción usa Whisper Large V3 Turbo en GPU (~10x tiempo real).")
        for a in audios:
            fecha = a["fecha"][:16].replace("T", " ")
            quien = a["persona_nombre"] or "?"
            chat = a["conversation_nombre"] or a["conversation_id"]
            with st.container(border=True):
                c1, c2, c3 = st.columns([5, 2, 2])
                c1.markdown(f"**{chat}** · {quien} · _{fecha}_ · {a['direccion']}")
                c1.caption(f"size {_fmt_size(a['tamanio_bytes'])} · {a['mime_type'] or '?'}")
                if c2.button("▶️ Transcribir", key=f"t_{a['item_id']}", use_container_width=True):
                    with st.spinner("Transcribiendo…"):
                        try:
                            r = api.transcribe_item(a["item_id"])
                            st.success(f"OK · {r.get('chars')} caracteres · {r.get('duracion_s', '?')}s · idioma {r.get('idioma')}")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error: {e}")
                    st.rerun()


with tab_trans:
    transcritos = []
    try:
        transcritos = api.transcribe_pendientes(solo_pendientes=False, limit=50)
        transcritos = [a for a in transcritos if a["transcribed"]]
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude listar: {e}")

    if not transcritos:
        st.info("Todavía no transcribimos ningún audio.")
    else:
        for a in transcritos:
            fecha = a["fecha"][:16].replace("T", " ")
            quien = a["persona_nombre"] or "?"
            chat = a["conversation_nombre"] or a["conversation_id"]
            with st.container(border=True):
                st.markdown(f"**{chat}** · {quien} · _{fecha}_ · {a['direccion']} · {_fmt_dur(a['duracion_s'])}")
                st.markdown(f"> {a['texto'] or '(vacío)'}")
                if st.button("🔁 Re-transcribir", key=f"r_{a['item_id']}"):
                    with st.spinner("Re-transcribiendo…"):
                        try:
                            r = api.transcribe_item(a["item_id"])
                            st.success(f"OK · {r.get('chars')} caracteres")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error: {e}")
                    st.rerun()
