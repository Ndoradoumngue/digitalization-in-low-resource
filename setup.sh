#!/bin/bash
# SDAI Digitalisation — Offline Setup Script
#
# Run this ONCE on a machine WITH internet access before deploying offline.
# It downloads all required models and dependencies into ./models/ so the
# whole directory can be transferred to a server with no internet connection.
#
# Requirements (on this internet-connected machine):
#   - Docker + Docker Compose v2.20+
#   - pnpm (npm install -g pnpm)
#   - Python 3.11 with pip

set -euo pipefail

MODELS_DIR="./models"
BOLD='\033[1m'; RESET='\033[0m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'

step() { echo -e "\n${BOLD}[${1}/4]${RESET} ${2}"; }
ok()   { echo -e "      ${GREEN}✓ ${1}${RESET}"; }
warn() { echo -e "      ${YELLOW}⚠ ${1}${RESET}"; }

echo -e "${BOLD}=== SDAI Offline Setup ===${RESET}"
echo "Run on a machine with internet access."
mkdir -p "$MODELS_DIR/wheels"

# ── 1. Pull qwen2.5vl:7b and bundle the Ollama model store ───────────────────
step 1 "Pulling qwen2.5vl:7b via Docker (~5 GB)…"

# Use a temporary Ollama container so the model lands in a Docker volume
# (no dependency on a host-installed Ollama).
docker volume create sdai_ollama_export >/dev/null 2>&1 || true
docker rm -f sdai_ollama_export >/dev/null 2>&1 || true

docker run -d --name sdai_ollama_export \
  -v sdai_ollama_export:/root/.ollama \
  ollama/ollama serve >/dev/null

echo "      Waiting for Ollama to be ready…"
until docker exec sdai_ollama_export /bin/ollama list >/dev/null 2>&1; do
  sleep 2
done

docker exec sdai_ollama_export /bin/ollama pull qwen2.5vl:7b
docker stop sdai_ollama_export >/dev/null

echo "      Exporting model store to $MODELS_DIR/ollama-models.tar.gz…"
docker run --rm \
  -v sdai_ollama_export:/root/.ollama:ro \
  -v "$(pwd)/$MODELS_DIR:/backup" \
  alpine sh -c "tar czf /backup/ollama-models.tar.gz -C /root/.ollama ."

docker rm sdai_ollama_export >/dev/null
docker volume rm sdai_ollama_export >/dev/null
ok "Model bundled: $MODELS_DIR/ollama-models.tar.gz ($(du -sh "$MODELS_DIR/ollama-models.tar.gz" | cut -f1))"

# ── 2. Build and save Docker images ──────────────────────────────────────────
step 2 "Building and saving Docker images…"
docker compose build --quiet
docker compose pull --quiet

# docker compose config --images lists every image referenced by the stack
IMAGES=$(docker compose config --images 2>/dev/null | tr '\n' ' ')
if [ -z "$IMAGES" ]; then
  warn "docker compose config --images returned nothing — falling back to 'docker compose images'"
  # shellcheck disable=SC2046
  IMAGES=$(docker compose images -q | sort -u | xargs docker inspect \
    --format '{{index .RepoTags 0}}' 2>/dev/null | tr '\n' ' ')
fi

# shellcheck disable=SC2086
docker save $IMAGES | gzip > "$MODELS_DIR/docker-images.tar.gz"
ok "Images saved: $MODELS_DIR/docker-images.tar.gz ($(du -sh "$MODELS_DIR/docker-images.tar.gz" | cut -f1))"

# ── 3. Download Python wheels (matching the API container's Linux/amd64 platform) ──
step 3 "Downloading Python wheels for linux/amd64…"

# Run pip download inside the same base image as the API container so the
# downloaded wheels match the target platform exactly.
docker run --rm \
  -v "$(pwd)/apps/api/requirements.txt:/requirements.txt:ro" \
  -v "$(pwd)/$MODELS_DIR/wheels:/wheels" \
  --platform linux/amd64 \
  python:3.11-slim \
  pip download --quiet -r /requirements.txt -d /wheels/

ok "Wheels saved: $MODELS_DIR/wheels/ ($(du -sh "$MODELS_DIR/wheels" | cut -f1))"

# ── 4. Fetch Node packages into a local pnpm store ───────────────────────────
step 4 "Fetching Node packages into local pnpm store…"

# --store-dir keeps packages inside this project directory so they can be
# transferred alongside the source and used with pnpm install --offline.
pnpm --store-dir "$MODELS_DIR/pnpm-store" fetch --frozen-lockfile
ok "Node packages saved: $MODELS_DIR/pnpm-store/ ($(du -sh "$MODELS_DIR/pnpm-store" | cut -f1))"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}=== Setup complete ===${RESET}"
echo "Total bundle size: $(du -sh "$MODELS_DIR" | cut -f1)"
echo ""
echo "Next step — transfer the entire project directory to the offline server:"
echo "  rsync -av --exclude '.git' . user@ministry-server:/opt/sdai_digitalization/"
echo ""
echo "Then on the offline server, run:"
echo "  ./install.sh"
