# =====================================================
# SecondBrain Bridge WhatsApp - Dockerfile
# Node.js 22 + whatsapp-web.js + Chromium (puppeteer)
# =====================================================

FROM node:22-bookworm-slim AS base

# Usar el Chromium del sistema, no el que descarga puppeteer
ENV PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    NODE_ENV=production

# Chromium + fuentes + tini (init liviano para reapear procesos zombie de chromium)
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium \
      ca-certificates \
      fonts-liberation \
      tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencias primero (mejor cacheo de capas)
COPY package.json package-lock.json* ./
RUN npm install --omit=dev --no-audit --no-fund

# Código
COPY . .

EXPOSE 3001

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "src/index.js"]
