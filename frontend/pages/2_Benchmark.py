"""
Benchmark de modelos LLM y embeddings.

Para validar Sprint 0: probar Gemma 4 12B vs Qwen3-VL 8B con tus datos.
Permite comparar latencia, calidad de output, tokens/segundo.
"""

import streamlit as st

from lib import APIClient

st.set_page_config(page_title="Benchmark", page_icon="⚡", layout="wide")
st.title("⚡ Benchmark de modelos")

api = APIClient()

if not api.is_alive():
    st.error("Backend no responde")
    st.stop()

# -----------------------------------------------------------------
# Configuración
# -----------------------------------------------------------------

st.markdown(
    "Probá distintos prompts contra los modelos descargados para comparar "
    "**calidad, velocidad y latencia** en tu hardware."
)

# Obtener lista de modelos
try:
    models_data = api.list_models()
    available_models = [m["name"] for m in models_data.get("models", [])]
except Exception as e:
    st.error(f"No se pudo obtener lista de modelos: {e}")
    available_models = []

if not available_models:
    st.warning(
        "No hay modelos descargados. Esperá a que termine `ollama-init` "
        "o ejecutá manualmente:\n\n"
        "```bash\n"
        "docker compose exec ollama ollama pull gemma4:12b\n"
        "docker compose exec ollama ollama pull qwen3-vl:8b\n"
        "docker compose exec ollama ollama pull qwen3-embedding:4b\n"
        "```"
    )
    st.stop()

# -----------------------------------------------------------------
# Tabs: LLM y Embeddings
# -----------------------------------------------------------------

tab_llm, tab_embed, tab_compare = st.tabs(["💬 LLM", "🔢 Embeddings", "🆚 Comparar"])

# -----------------------------------------------------------------
# Test LLM individual
# -----------------------------------------------------------------

with tab_llm:
    col1, col2 = st.columns([2, 1])

    with col1:
        system_prompt = st.text_area(
            "System prompt (opcional)",
            value="Sos un asistente que responde en español argentino, conciso y directo.",
            height=80,
        )
        user_prompt = st.text_area(
            "Prompt del usuario",
            value="Escribime un ejemplo de mensaje de WhatsApp pidiéndole a un cliente que confirme un turno.",
            height=120,
        )

    with col2:
        model = st.selectbox(
            "Modelo",
            options=[m for m in available_models if "embed" not in m.lower()],
            index=0,
        )
        temperature = st.slider("Temperature", 0.0, 1.5, 0.3, 0.1)

    if st.button("▶️ Generar", type="primary"):
        if not user_prompt.strip():
            st.warning("Ingresá un prompt")
        else:
            with st.spinner(f"Generando con {model}..."):
                try:
                    result = api.test_llm(
                        prompt=user_prompt,
                        model=model,
                        system=system_prompt or None,
                        temperature=temperature,
                    )
                    st.success("✅ Respuesta")
                    st.markdown(result["response"])

                    st.markdown("---")
                    st.markdown("**Métricas:**")
                    cols = st.columns(4)
                    cols[0].metric("Latencia", f"{result['duration_ms']} ms")
                    cols[1].metric("Tokens input", result["tokens_input"])
                    cols[2].metric("Tokens output", result["tokens_output"])
                    cols[3].metric(
                        "Tokens/seg",
                        f"{result['tokens_per_second']:.1f}" if result.get("tokens_per_second") else "—",
                    )
                except Exception as e:
                    st.error(f"Error: {e}")

# -----------------------------------------------------------------
# Test embeddings
# -----------------------------------------------------------------

with tab_embed:
    text_to_embed = st.text_area(
        "Texto a embebir",
        value="El cliente Esteban me pidió que renovemos el contrato del NAS la semana que viene",
        height=80,
    )

    embed_models = [m for m in available_models if "embed" in m.lower()]
    if not embed_models:
        st.warning("No hay modelos de embedding descargados")
    else:
        embed_model = st.selectbox("Modelo de embedding", embed_models, key="emb_model")

        if st.button("Generar embedding"):
            with st.spinner("Generando..."):
                try:
                    result = api.test_embed(text=text_to_embed, model=embed_model)
                    cols = st.columns(3)
                    cols[0].metric("Dimensiones", result["dimensions"])
                    cols[1].metric("Latencia", f"{result['duration_ms']} ms")
                    cols[2].metric("Modelo", result["model"])
                    st.write("**Primeros 5 valores del vector:**")
                    st.code(result["embedding_preview"])
                except Exception as e:
                    st.error(f"Error: {e}")

# -----------------------------------------------------------------
# Comparativa lado a lado
# -----------------------------------------------------------------

with tab_compare:
    st.markdown("Compará el mismo prompt en dos modelos al mismo tiempo.")

    cmp_prompt = st.text_area(
        "Prompt común",
        value=(
            "Extraé los hechos importantes de este mensaje y devolvelos en JSON:\n\n"
            "Mensaje: 'Dale Esteban, mañana a las 10 te llevo el presupuesto del NAS, "
            "cobramos 350 mil más IVA. Decime si te parece bien.'"
        ),
        height=120,
    )

    cmp_system = st.text_area(
        "System prompt común",
        value="Devolvé sólo JSON válido, nada más.",
        height=60,
    )

    cmp_temp = st.slider("Temperature", 0.0, 1.5, 0.0, 0.1, key="cmp_temp")

    text_models = [m for m in available_models if "embed" not in m.lower()]
    col_a, col_b = st.columns(2)
    with col_a:
        model_a = st.selectbox("Modelo A", text_models, key="ma", index=0)
    with col_b:
        model_b = st.selectbox(
            "Modelo B",
            text_models,
            key="mb",
            index=min(1, len(text_models) - 1),
        )

    if st.button("▶️ Comparar", type="primary"):
        if model_a == model_b:
            st.warning("Elegí dos modelos distintos")
        else:
            res_a = res_b = None
            with st.spinner("Generando con ambos modelos..."):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"### {model_a}")
                    try:
                        res_a = api.test_llm(
                            prompt=cmp_prompt,
                            model=model_a,
                            system=cmp_system or None,
                            temperature=cmp_temp,
                        )
                        st.markdown(res_a["response"])
                        st.caption(
                            f"⏱️ {res_a['duration_ms']} ms · "
                            f"📊 {res_a.get('tokens_per_second', 0):.1f} tok/s · "
                            f"🔢 {res_a['tokens_output']} tokens"
                        )
                    except Exception as e:
                        st.error(f"Error: {e}")

                with col_b:
                    st.markdown(f"### {model_b}")
                    try:
                        res_b = api.test_llm(
                            prompt=cmp_prompt,
                            model=model_b,
                            system=cmp_system or None,
                            temperature=cmp_temp,
                        )
                        st.markdown(res_b["response"])
                        st.caption(
                            f"⏱️ {res_b['duration_ms']} ms · "
                            f"📊 {res_b.get('tokens_per_second', 0):.1f} tok/s · "
                            f"🔢 {res_b['tokens_output']} tokens"
                        )
                    except Exception as e:
                        st.error(f"Error: {e}")
