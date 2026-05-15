"""
Página de Contactos (Sprint 2.5).

- Importa un export vCard (.vcf) de Google Contacts a `core.personas`
- Lista contactos con búsqueda + checkbox de "seguir" para excluir del pipeline
"""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Contactos", page_icon="👥", layout="wide")
st.title("👥 Contactos")
st.caption("Sprint 2.5 — Importar vCard de Google + control de qué seguir")

# ---------------------------------------------------------------------------
# Estadísticas arriba
# ---------------------------------------------------------------------------
try:
    stats = httpx.get(f"{BACKEND_URL}/api/contacts/stats", timeout=10).json()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", stats["total"])
    c2.metric("Siguiendo", stats["siguiendo"])
    c3.metric("Ignorados", stats["ignorados"])
    por_tipo = stats.get("por_tipo", {})
    c4.metric("Tipos", " · ".join([f"{k}:{v}" for k, v in por_tipo.items()]) or "—")
except Exception as e:  # noqa: BLE001
    st.warning(f"No pude traer stats: {e}")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_importar, tab_lista = st.tabs(["📥 Importar vCard", "📋 Lista de contactos"])

# ===========================================================================
# Tab: Importar
# ===========================================================================
with tab_importar:
    st.markdown(
        """
**Cómo exportar de Google Contacts:**
1. Entrá a https://contacts.google.com
2. Sidebar izquierdo → **Exportar**
3. *Todos los contactos* → formato **vCard (para iOS Contacts)**
4. Subí el `.vcf` acá abajo

El backend normaliza los teléfonos a E.164 (`+54...`) y matchea contra las Personas
ya existentes en la DB por **teléfono primero**, después por nombre canónico.
"""
    )

    region = st.selectbox(
        "Región default para normalizar teléfonos sin código de país",
        options=["AR", "UY", "CL", "ES", "MX", "US"],
        index=0,
    )
    importar_sin_tel = st.checkbox(
        "Importar también contactos sin teléfono normalizable",
        value=True,
        help="Si lo destildás, los contactos sin teléfono válido se saltean.",
    )
    archivo = st.file_uploader("Seleccioná el archivo .vcf", type=["vcf"])

    if "vcard_preview" not in st.session_state:
        st.session_state.vcard_preview = None
    if "vcard_bytes" not in st.session_state:
        st.session_state.vcard_bytes = None
    if "vcard_filename" not in st.session_state:
        st.session_state.vcard_filename = None
    if "import_result" not in st.session_state:
        st.session_state.import_result = None

    if archivo is not None and (
        st.session_state.vcard_filename != archivo.name
        or st.session_state.vcard_preview is None
    ):
        with st.spinner("Parseando vCard..."):
            try:
                resp = httpx.post(
                    f"{BACKEND_URL}/api/contacts/preview",
                    files={"archivo": (archivo.name, archivo.getvalue(), "text/vcard")},
                    data={"region": region},
                    timeout=120,
                )
                resp.raise_for_status()
                st.session_state.vcard_preview = resp.json()
                st.session_state.vcard_bytes = archivo.getvalue()
                st.session_state.vcard_filename = archivo.name
                st.session_state.import_result = None
            except httpx.HTTPStatusError as e:
                st.error(f"Error del servidor: {e.response.text}")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")

    if st.session_state.vcard_preview:
        prev = st.session_state.vcard_preview
        st.subheader("Resumen del archivo")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tarjetas", prev["total_vcards"])
        c2.metric("Sin nombre", prev["sin_nombre"])
        c3.metric("Sin teléfono", prev["sin_telefono"])
        c4.metric("Errores", prev["errores"])

        with st.expander("👀 Muestra de los primeros 10 contactos parseados"):
            for m in prev["muestra"]:
                tels = ", ".join(
                    [t["e164"] or f"⚠️ {t['raw']}" for t in m["telefonos"]]
                ) or "—"
                cats = ", ".join(m["categorias"]) if m["categorias"] else ""
                st.markdown(
                    f"- **{m['nombre']}** · 📞 {tels}"
                    + (f" · 📧 {m['emails'][0]}" if m["emails"] else "")
                    + (f" · 🏢 {m['organizacion']}" if m["organizacion"] else "")
                    + (f" · 🏷️ {cats}" if cats else "")
                )

        st.divider()
        if st.button("✅ Confirmar e importar", type="primary"):
            with st.spinner(f"Importando {prev['total_vcards']} contactos..."):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/api/contacts/import",
                        files={
                            "archivo": (
                                st.session_state.vcard_filename,
                                st.session_state.vcard_bytes,
                                "text/vcard",
                            )
                        },
                        data={
                            "region": region,
                            "importar_sin_telefono": str(importar_sin_tel).lower(),
                        },
                        timeout=300,
                    )
                    resp.raise_for_status()
                    st.session_state.import_result = resp.json()
                except httpx.HTTPStatusError as e:
                    st.error(f"Error: {e.response.text}")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error: {e}")

    if st.session_state.import_result:
        r = st.session_state.import_result
        st.success("🎉 Importación completada")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Parseados", r["total_parseados"])
        c2.metric("Creados", r["creados"])
        c3.metric("Actualizados (match)", r["actualizados"])
        c4.metric("Saltados / errores", r["saltados"] + r["errores"])
        if r["sin_telefono_creados"]:
            st.caption(f"De los creados, {r['sin_telefono_creados']} no tenían teléfono normalizable.")
        if st.button("Importar otro"):
            st.session_state.vcard_preview = None
            st.session_state.vcard_bytes = None
            st.session_state.vcard_filename = None
            st.session_state.import_result = None
            st.rerun()


# ===========================================================================
# Tab: Lista
# ===========================================================================
with tab_lista:
    st.markdown(
        "Por defecto **no se sigue a nadie** (opt-in). Buscá / filtrá y marcá a quién querés que el sistema indexe."
    )

    try:
        categorias = httpx.get(f"{BACKEND_URL}/api/contacts/categorias", timeout=10).json()
    except Exception:  # noqa: BLE001
        categorias = []

    col_q, col_tipo, col_seg, col_cat = st.columns([2, 1, 1, 2])
    q = col_q.text_input("🔍 Buscar (nombre, teléfono, email, alias)")
    tipo_filtro = col_tipo.selectbox("Tipo", options=["(todos)", "yo", "contacto", "desconocido"])
    seg_filtro = col_seg.selectbox("Seguir", options=["(todos)", "sí", "no"])
    cat_filtro = col_cat.selectbox("Etiqueta de Google", options=["(todas)", *categorias])

    params: dict = {"q": q, "limit": 1000}
    if tipo_filtro != "(todos)":
        params["tipo"] = tipo_filtro
    if seg_filtro == "sí":
        params["seguir"] = True
    elif seg_filtro == "no":
        params["seguir"] = False
    if cat_filtro != "(todas)":
        params["categoria"] = cat_filtro

    try:
        resp = httpx.get(f"{BACKEND_URL}/api/contacts", params=params, timeout=30)
        resp.raise_for_status()
        personas = resp.json()
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude traer la lista: {e}")
        personas = []

    cab1, cab2, cab3 = st.columns([2, 1, 1])
    cab1.caption(f"{len(personas)} personas")
    if personas:
        if cab2.button("✅ Marcar visibles: seguir"):
            try:
                r = httpx.post(
                    f"{BACKEND_URL}/api/contacts/bulk-seguir",
                    json={"ids": [p["id"] for p in personas], "seguir": True},
                    timeout=30,
                )
                r.raise_for_status()
                st.success(f"{r.json()['actualizados']} marcadas como seguir")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
        if cab3.button("🚫 Marcar visibles: no seguir"):
            try:
                r = httpx.post(
                    f"{BACKEND_URL}/api/contacts/bulk-seguir",
                    json={"ids": [p["id"] for p in personas], "seguir": False},
                    timeout=30,
                )
                r.raise_for_status()
                st.success(f"{r.json()['actualizados']} marcadas como no seguir")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")

    MAX_FILAS = 200
    if personas:
        if len(personas) > MAX_FILAS:
            st.info(
                f"Mostrando las primeras {MAX_FILAS} de {len(personas)}. "
                "Afiná la búsqueda/filtro para ver el resto — los botones de **marcar visibles** "
                "aplican a TODAS las que matchean el filtro, no solo a las mostradas."
            )
        # Header
        h1, h2, h3, h4, h5, h6 = st.columns([3, 2, 3, 1, 2, 1])
        h1.markdown("**Nombre**")
        h2.markdown("**Teléfono**")
        h3.markdown("**Email / Org**")
        h4.markdown("**Tipo**")
        h5.markdown("**Aliases**")
        h6.markdown("**Seguir**")

        for p in personas[:MAX_FILAS]:
            cols = st.columns([3, 2, 3, 1, 2, 1])
            cols[0].markdown(f"**{p['nombre_canonico']}**")
            cols[1].write(p["telefono"] or "—")
            email_org = p["email"] or ""
            org = (p.get("datos") or {}).get("organizacion")
            if org:
                email_org = f"{email_org}  \n🏢 {org}" if email_org else f"🏢 {org}"
            cols[2].write(email_org or "—")
            cols[3].write(p["tipo"])
            cols[4].write(", ".join(p["aliases"][:3]) + ("…" if len(p["aliases"]) > 3 else ""))
            nuevo = cols[5].toggle(
                "seguir",
                value=p["seguir"],
                key=f"seguir_{p['id']}",
                label_visibility="collapsed",
            )
            if nuevo != p["seguir"]:
                try:
                    httpx.patch(
                        f"{BACKEND_URL}/api/contacts/{p['id']}",
                        json={"seguir": nuevo},
                        timeout=10,
                    ).raise_for_status()
                except Exception as e:  # noqa: BLE001
                    st.warning(f"No pude actualizar {p['nombre_canonico']}: {e}")
    else:
        st.info("Sin resultados.")
