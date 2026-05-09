#!/bin/bash
# Warmup de modelos Ollama
# Hace un request mínimo a cada modelo para cargarlos en VRAM
# Útil después de un restart, así el primer request real es rápido

set -euo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
MODELS=(
    "${OLLAMA_MODEL_PRIMARY:-gemma4:12b}"
    "${OLLAMA_MODEL_VISION:-qwen3-vl:8b}"
    "${OLLAMA_MODEL_EMBEDDING:-qwen3-embedding:4b}"
)

echo "Warmup de modelos Ollama..."

for model in "${MODELS[@]}"; do
    echo "  → $model"
    curl -s -X POST "$OLLAMA_URL/api/generate" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"$model\", \"prompt\": \"hi\", \"stream\": false, \"options\": {\"num_predict\": 1}}" \
        > /dev/null 2>&1 || echo "    (warmup falló para $model — quizás no es de generación)"
done

echo "✅ Modelos cargados en VRAM"
