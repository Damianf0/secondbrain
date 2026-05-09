# =====================================================
# SecondBrain Frontend - Dockerfile
# Python 3.12 + uv + Streamlit
# =====================================================

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

COPY . .

RUN uv sync --frozen --no-dev || uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8501

# Configuración Streamlit: deshabilita gather usage stats, abre en 0.0.0.0
CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--browser.gatherUsageStats=false", \
     "--server.runOnSave=true"]
