# =====================================================
# SecondBrain Bridge WhatsApp - Dockerfile
# Node.js 22 + Baileys (WebSocket directo al protocolo de WhatsApp Web,
# sin Chromium/Puppeteer)
# =====================================================

FROM node:22-bookworm-slim AS base

ENV NODE_ENV=production

# tini: init liviano para forwardear señales correctamente (SIGTERM/SIGINT)
# git: alguna dependencia transitiva de Baileys se resuelve desde un repo git en npm install
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
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
