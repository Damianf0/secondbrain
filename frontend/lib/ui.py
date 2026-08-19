"""
UI Helpers (V2 Premium).

Inyecta hojas de estilo personalizadas (CSS) y tipografías modernas de Google Fonts
en Streamlit para dar un salto visual premium, moderno y vivo.
"""

import streamlit as st


def apply_premium_style() -> None:
    """Aplica tipografía, colores HSL neón, gradientes, bordes suavizados y transacciones CSS."""
    st.markdown(
        """
        <style>
            /* 1. Importación de tipografías premium */
            @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Inter:wght@300;400;500;600;700&display=swap');
            
            /* 2. Reseteo de tipografía global */
            html, body, [class*="css"], [class*="st-"] {
                font-family: 'Outfit', 'Inter', sans-serif !important;
            }
            
            /* 3. Título Principal con Gradiente Neón */
            h1 {
                background: linear-gradient(135deg, #00F0FF 0%, #7F00FF 50%, #FF007F 100%) !important;
                -webkit-background-clip: text !important;
                -webkit-text-fill-color: transparent !important;
                font-weight: 800 !important;
                font-size: 2.8rem !important;
                letter-spacing: -0.5px !important;
                margin-bottom: 5px !important;
                text-shadow: 0px 5px 25px rgba(127, 0, 255, 0.15) !important;
            }
            
            /* Subtítulos estilizados */
            h2, h3, h4 {
                font-family: 'Outfit', sans-serif !important;
                font-weight: 600 !important;
                letter-spacing: -0.2px !important;
                color: #E2E8F0 !important;
            }
            
            /* 4. Glassmorphism en contenedores, métricas y bloques de alerta */
            div.stAlert, div[data-testid="stMetricValue"], div[data-testid="metric-container"], .element-container div.row-widget {
                border-radius: 14px !important;
                background: rgba(255, 255, 255, 0.02) !important;
                border: 1px solid rgba(255, 255, 255, 0.07) !important;
                backdrop-filter: blur(12px) !important;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.25) !important;
                transition: all 0.3s ease !important;
            }
            
            div.stAlert:hover, div[data-testid="metric-container"]:hover {
                background: rgba(255, 255, 255, 0.04) !important;
                border-color: rgba(0, 240, 255, 0.2) !important;
                box-shadow: 0 8px 32px 0 rgba(0, 240, 255, 0.08) !important;
            }
            
            /* 5. Estilo personalizado para botones (Botones Neón Premium) */
            div.stButton > button {
                background: linear-gradient(135deg, #7F00FF 0%, #FF007F 100%) !important;
                color: #FFFFFF !important;
                border: none !important;
                border-radius: 24px !important;
                padding: 0.6rem 2rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.2px !important;
                text-transform: uppercase !important;
                font-size: 0.85rem !important;
                transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
                box-shadow: 0 5px 18px rgba(127, 0, 255, 0.35) !important;
                cursor: pointer !important;
            }
            
            div.stButton > button:hover {
                transform: translateY(-3px) !important;
                box-shadow: 0 8px 25px rgba(127, 0, 255, 0.55), 0 0 15px rgba(255, 0, 127, 0.35) !important;
                color: #FFFFFF !important;
            }
            
            div.stButton > button:active {
                transform: translateY(-1px) !important;
                box-shadow: 0 4px 10px rgba(127, 0, 255, 0.3) !important;
            }

            /* 6. Mejoras en la barra lateral */
            section[data-testid="stSidebar"] {
                background-color: #0E0F19 !important;
                border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
            }
            
            /* 7. Inputs de texto y Selectores */
            div[data-baseweb="input"], div[data-baseweb="select"] {
                border-radius: 10px !important;
                background-color: rgba(255, 255, 255, 0.01) !important;
                border: 1px solid rgba(255, 255, 255, 0.1) !important;
                transition: all 0.2s ease !important;
            }
            div[data-baseweb="input"]:focus-within, div[data-baseweb="select"]:focus-within {
                border-color: #00F0FF !important;
                box-shadow: 0 0 8px rgba(0, 240, 255, 0.25) !important;
            }
            
            /* 8. Estilo de tablas o dataframes */
            div[data-testid="stTable"] {
                border-radius: 12px !important;
                overflow: hidden !important;
                border: 1px solid rgba(255, 255, 255, 0.05) !important;
            }

            /* 9. Efecto sutil de gradiente arriba de la página */
            .main .block-container::before {
                content: "";
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 4px;
                background: linear-gradient(90deg, #00F0FF 0%, #7F00FF 50%, #FF007F 100%);
                z-index: 9999;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
