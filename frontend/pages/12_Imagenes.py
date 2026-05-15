"""
Página de Imágenes (Sprint 5).

VLM unificado (qwen3-vl:8b) que devuelve OCR + descripción + categoría.
Triviales se filtran por heurística (tamaño + dims chicas) sin llamar al VLM.
"""

import streamlit as st

from lib.api_client import APIClient

st.set_page_config(page_title="Imágenes", page_icon="🖼️", layout="wide")
st.title("🖼️ Imágenes y captioning")
st.caption("Sprint 5 — qwen3-vl:8b local. Devuelve OCR + descripción + entidades en una sola llamada.")

api = APIClient()


try:
    s = api.images_stats()
except Exception as e:  # noqa: BLE001
    st.error(f"No pude traer stats: {e}")
    st.stop()


cols = st.columns(6)
cols[0].metric("Imgs totales", f"{s['imgs_total']:,}")
cols[1].metric("Con binario", f"{s['imgs_con_attachment']:,}")
cols[2].metric("Sin binario", f"{s['imgs_sin_binario']:,}")
cols[3].metric("Procesadas", f"{s['imgs_procesadas']:,}")
cols[4].metric("Triviales", f"{s['imgs_triviales']:,}", help="Stickers/memes chicos saltean el VLM")
cols[5].metric(
    "Jobs pendientes",
    f"{s['jobs_caption_pendientes']:,}",
    delta=f"{s['jobs_caption_fallidos']} fallidos" if s["jobs_caption_fallidos"] else None,
    delta_color="inverse" if s["jobs_caption_fallidos"] else "normal",
)

if s["jobs_caption_pendientes"]:
    if st.button(f"🛠️ Drenar cola ({s['jobs_caption_pendientes']} jobs)"):
        with st.spinner("Procesando imágenes con qwen3-vl…"):
            try:
                r = api.images_work(limit=min(20, s["jobs_caption_pendientes"]))
                st.success(
                    f"Procesados {r['procesados']} · ok {r['exitosos']} · triviales {r['triviales']} · "
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
with st.expander("⬆️ Subir imagen manual", expanded=False):
    st.caption("JPG/PNG/WEBP/GIF/BMP/HEIC. Quedan con conversation_id='manual_upload'.")
    up = st.file_uploader("Imagen", type=["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic"], key="img_upload")
    c1, c2, c3 = st.columns([2, 1, 1])
    conv_manual = c1.text_input("conversation_id", value="manual_upload")
    procesar_ahora = c2.checkbox("Procesar ya", value=True)
    if c3.button("Subir", type="primary", use_container_width=True, disabled=up is None):
        with st.spinner("Subiendo y procesando con VLM (puede tardar 5-15s)…" if procesar_ahora else "Subiendo…"):
            try:
                r = api.images_upload(
                    up.name,
                    up.getvalue(),
                    up.type or "image/jpeg",
                    conversation_id=conv_manual or "manual_upload",
                    procesar_ahora=procesar_ahora,
                )
                msg = f"OK · item {r['item_id']} · {r['size_bytes']/1024:.1f}KB"
                if r.get("duplicate_in_vault"):
                    msg += " (binario ya estaba en el Vault)"
                st.success(msg)
                if r.get("procesamiento"):
                    p = r["procesamiento"]
                    st.markdown(
                        f"**Resultado** · categoría `{p.get('categoria')}` · {p.get('chars')} chars · "
                        f"{p.get('duration_ms')}ms"
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
tab_pend, tab_proc = st.tabs([
    f"⏳ Pendientes ({s['imgs_con_attachment'] - s['imgs_procesadas']})",
    f"✅ Procesadas ({s['imgs_procesadas'] - s['imgs_triviales']})",
])


def _badge(cat: str | None) -> str:
    return {
        "texto-centrica": "🔤 texto",
        "mixta": "🧩 mixta",
        "visual-pura": "📷 visual",
        "trivial": "🚫 trivial",
    }.get(cat or "", cat or "?")


with tab_pend:
    imgs = []
    try:
        imgs = api.images_pendientes(solo_pendientes=True, limit=30)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude listar: {e}")

    if not imgs:
        st.info("No hay imágenes pendientes.")
    else:
        for i, img in enumerate(imgs):
            with st.container(border=True):
                cols = st.columns([1, 4])
                if img["presigned_url"]:
                    cols[0].image(img["presigned_url"], use_container_width=True)
                else:
                    cols[0].caption("(sin preview)")
                fecha = img["fecha"][:16].replace("T", " ")
                quien = img["persona_nombre"] or "?"
                chat = img["conversation_nombre"] or img["conversation_id"]
                cols[1].markdown(f"**{img['filename_original'] or '(sin nombre)'}** · {_fmt_size(img['tamanio_bytes'])} · {img['mime_type'] or '?'}")
                cols[1].caption(f"{chat} · {quien} · _{fecha}_ · {img['direccion']}")
                if cols[1].button("▶️ Procesar", key=f"p_{img['item_id']}"):
                    with st.spinner("Procesando con qwen3-vl…"):
                        try:
                            r = api.images_item(img["item_id"])
                            st.success(f"OK · categoría {r.get('categoria')} · {r.get('chars')} chars · {r.get('duration_ms')}ms")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error: {e}")
                    st.rerun()

with tab_proc:
    imgs = []
    try:
        imgs = api.images_pendientes(solo_pendientes=False, limit=30, incluir_triviales=False)
        imgs = [i for i in imgs if i["processed"]]
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude listar: {e}")

    if not imgs:
        st.info("Todavía no procesamos ninguna imagen (no trivial).")
    else:
        for img in imgs:
            with st.container(border=True):
                cols = st.columns([1, 4])
                if img["presigned_url"]:
                    cols[0].image(img["presigned_url"], use_container_width=True)
                fecha = img["fecha"][:16].replace("T", " ")
                quien = img["persona_nombre"] or "?"
                chat = img["conversation_nombre"] or img["conversation_id"]
                cols[1].markdown(f"{_badge(img['categoria'])} · **{img['filename_original'] or '(sin nombre)'}** · {_fmt_size(img['tamanio_bytes'])}")
                cols[1].caption(f"{chat} · {quien} · _{fecha}_")
                if img["descripcion"]:
                    cols[1].markdown(f"> {img['descripcion']}")
                if img["entidades"]:
                    cols[1].caption("**Visibles:** " + ", ".join(img["entidades"]))
                if img["ocr_preview"]:
                    with cols[1].expander("Texto (OCR)"):
                        st.text(img["ocr_preview"])
                if cols[1].button("🔁 Re-procesar", key=f"rp_{img['item_id']}"):
                    with st.spinner("Re-procesando…"):
                        try:
                            r = api.images_item(img["item_id"])
                            st.success(f"OK · categoría {r.get('categoria')}")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error: {e}")
                    st.rerun()
