"""
Página de Documentos / Extracción de texto (Sprint 6).

- Estado: cuántos docs hay, con binario, extraídos, jobs pendientes/fallidos.
- Upload manual (PDF, DOCX, XLSX, TXT, MD, CSV) para validar y para importar suelto.
- Tabs Pendientes / Extraídos con botón "Extraer uno" / "Re-extraer".
"""

import streamlit as st

from lib.api_client import APIClient

st.set_page_config(page_title="Documentos", page_icon="📄", layout="wide")
st.title("📄 Documentos y extracción")
st.caption("Sprint 6 — pdfplumber + python-docx + openpyxl. El texto extraído entra al chat (tagger + embed).")

api = APIClient()


try:
    s = api.extract_stats()
except Exception as e:  # noqa: BLE001
    st.error(f"No pude traer stats: {e}")
    st.stop()


cols = st.columns(5)
cols[0].metric("Docs totales", f"{s['docs_total']:,}", help="items con media_tipo=documento")
cols[1].metric("Con binario", f"{s['docs_con_attachment']:,}", help="en MinIO (bridge en vivo o upload manual)")
cols[2].metric("Sin binario", f"{s['docs_sin_binario']:,}", help="históricos sin binario disponible")
cols[3].metric("Extraídos", f"{s['docs_extraidos']:,}")
cols[4].metric(
    "Jobs pendientes",
    f"{s['jobs_extract_pendientes']:,}",
    delta=f"{s['jobs_extract_fallidos']} fallidos" if s["jobs_extract_fallidos"] else None,
    delta_color="inverse" if s["jobs_extract_fallidos"] else "normal",
)

if s["jobs_extract_pendientes"]:
    if st.button(f"🛠️ Drenar cola ({s['jobs_extract_pendientes']} jobs)"):
        with st.spinner("Extrayendo documentos encolados…"):
            try:
                r = api.extract_work(limit=min(50, s["jobs_extract_pendientes"]))
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
# Upload manual
# ---------------------------------------------------------------------------
with st.expander("⬆️ Subir documento manual", expanded=False):
    st.caption("PDF, DOCX, XLSX, TXT, MD, CSV. Quedan con conversation_id='manual_upload'.")
    up = st.file_uploader(
        "Documento",
        type=["pdf", "docx", "xlsx", "xlsm", "txt", "md", "csv", "log"],
        key="doc_upload",
    )
    c1, c2, c3 = st.columns([2, 1, 1])
    conv_manual = c1.text_input("conversation_id", value="manual_upload")
    extraer_ahora = c2.checkbox("Extraer ya", value=True)
    if c3.button("Subir", type="primary", use_container_width=True, disabled=up is None):
        with st.spinner("Subiendo y extrayendo…" if extraer_ahora else "Subiendo…"):
            try:
                r = api.extract_upload(
                    up.name,
                    up.getvalue(),
                    up.type or "application/octet-stream",
                    conversation_id=conv_manual or "manual_upload",
                    extraer_ahora=extraer_ahora,
                )
                msg = f"OK · item {r['item_id']} · {r['size_bytes']/1024:.1f}KB"
                if r.get("duplicate_in_vault"):
                    msg += " (binario ya estaba en el Vault)"
                st.success(msg)
                if r.get("extraccion"):
                    e = r["extraccion"]
                    st.markdown(
                        f"**Extracción** · formato `{e.get('formato')}` · {e.get('chars')} chars · "
                        f"extras: `{e.get('extras')}`"
                    )
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
        st.rerun()

st.divider()


def _fmt_size(b: int | None) -> str:
    if not b:
        return "?"
    if b < 1024:
        return f"{b}B"
    if b < 1024 * 1024:
        return f"{b/1024:.1f}KB"
    return f"{b/(1024*1024):.1f}MB"


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_pend, tab_ext = st.tabs([
    f"⏳ Pendientes ({s['docs_con_attachment'] - s['docs_extraidos']})",
    f"✅ Extraídos ({s['docs_extraidos']})",
])

with tab_pend:
    docs = []
    try:
        docs = api.extract_pendientes(solo_pendientes=True, limit=100)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude listar: {e}")

    if not docs:
        st.info("No hay documentos pendientes de extraer.")
    else:
        for d in docs:
            fecha = d["fecha"][:16].replace("T", " ")
            quien = d["persona_nombre"] or "?"
            chat = d["conversation_nombre"] or d["conversation_id"]
            with st.container(border=True):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"**{d['filename_original'] or '(sin nombre)'}** · {_fmt_size(d['tamanio_bytes'])} · {d['mime_type'] or '?'}")
                c1.caption(f"{chat} · {quien} · _{fecha}_ · {d['direccion']}")
                if c2.button("▶️ Extraer", key=f"x_{d['item_id']}", use_container_width=True):
                    with st.spinner("Extrayendo…"):
                        try:
                            r = api.extract_item(d["item_id"])
                            st.success(f"OK · {r.get('chars')} caracteres · formato {r.get('formato')}")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error: {e}")
                    st.rerun()

with tab_ext:
    docs = []
    try:
        docs = api.extract_pendientes(solo_pendientes=False, limit=50)
        docs = [d for d in docs if d["extracted"]]
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude listar: {e}")

    if not docs:
        st.info("Todavía no extrajimos ningún documento.")
    else:
        for d in docs:
            fecha = d["fecha"][:16].replace("T", " ")
            quien = d["persona_nombre"] or "?"
            chat = d["conversation_nombre"] or d["conversation_id"]
            with st.container(border=True):
                st.markdown(
                    f"**{d['filename_original'] or '(sin nombre)'}** · `{d['formato']}` · "
                    f"{d['chars']} chars · {_fmt_size(d['tamanio_bytes'])}"
                )
                st.caption(f"{chat} · {quien} · _{fecha}_ · {d['direccion']}")
                if d["texto_preview"]:
                    with st.expander("Vista previa (primeros 500 chars)"):
                        st.text(d["texto_preview"])
                if st.button("🔁 Re-extraer", key=f"r_{d['item_id']}"):
                    with st.spinner("Re-extrayendo…"):
                        try:
                            r = api.extract_item(d["item_id"])
                            st.success(f"OK · {r.get('chars')} caracteres")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error: {e}")
                    st.rerun()
