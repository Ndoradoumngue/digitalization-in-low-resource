#!/bin/bash
# SDAI Digitalisation - Offline Setup Script
#
# Run this ONCE on a machine WITH internet access before deploying offline.
# It downloads the VLM model and builds/saves every Docker image into
# ./models/ so the whole directory can be transferred to a server with no
# internet connection (then run ./install.sh there).
#
#   ./setup.sh          images + model for linux/amd64 servers
#   ./setup.sh --dev    also Python wheels and a pnpm store (development
#                       outside Docker - not needed to run the application)
#
#   TARGET_PLATFORM=linux/arm64 ./setup.sh   for ARM servers
#
# The images are built for TARGET_PLATFORM (default linux/amd64, the usual
# server), whatever this machine is - on an ARM laptop (Apple Silicon) the
# build is emulated and takes much longer.
#
# Requirements (on this internet-connected machine):
#   - Docker + Docker Compose v2.20+ (with buildx, included in Docker Desktop)
#   - --dev only: pnpm

set -euo pipefail

cd "$(dirname "$0")"

MODELS_DIR="./models"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
DEV=0
[ "${1:-}" = "--dev" ] && DEV=1
STEPS=$((DEV ? 5 : 3))
BOLD='\033[1m'; RESET='\033[0m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'

step() { echo -e "\n${BOLD}[${1}/${STEPS}]${RESET} ${2}"; }
ok()   { echo -e "      ${GREEN}✓ ${1}${RESET}"; }
warn() { echo -e "      ${YELLOW}⚠ ${1}${RESET}"; }

echo -e "${BOLD}=== SDAI Offline Setup (${TARGET_PLATFORM}) ===${RESET}"
echo "Run on a machine with internet access."
mkdir -p "$MODELS_DIR"

# ── 1. Pull qwen2.5vl:7b and bundle the Ollama model store ───────────────────
step 1 "Pulling qwen2.5vl:7b via Docker (~5 GB)…"

if [ -f "$MODELS_DIR/ollama-models.tar.gz" ]; then
  ok "already bundled: $MODELS_DIR/ollama-models.tar.gz (delete it to download again)"
else
  # A temporary Ollama container, so the model lands in a Docker volume (no
  # dependency on a host-installed Ollama). The model files are the same
  # on every platform.
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
fi

# ── 2. Build and save Docker images ──────────────────────────────────────────
step 2 "Building and saving Docker images for ${TARGET_PLATFORM}…"
export DOCKER_DEFAULT_PLATFORM="$TARGET_PLATFORM"
# Only the images used on a server (the cloud override adds none).
docker compose -f docker-compose.yml pull --quiet --ignore-buildable
docker compose -f docker-compose.yml build

IMAGES=$(docker compose -f docker-compose.yml config --images | sort -u | tr '\n' ' ')
[ -n "$IMAGES" ] || { echo "docker compose config --images returned nothing" >&2; exit 1; }
for image in $IMAGES; do
  arch=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
  [ "$arch" = "$TARGET_PLATFORM" ] || warn "$image is $arch, not $TARGET_PLATFORM"
done

# shellcheck disable=SC2086
docker save $IMAGES | gzip > "$MODELS_DIR/docker-images.tar.gz"
ok "Images saved: $MODELS_DIR/docker-images.tar.gz ($(du -sh "$MODELS_DIR/docker-images.tar.gz" | cut -f1))"
echo "      $IMAGES"

# ── 3. Checksums ─────────────────────────────────────────────────────────────
step 3 "Writing checksums…"
(
  cd "$MODELS_DIR"
  # sha256sum (Linux) or shasum (macOS) - same output format
  if command -v sha256sum >/dev/null; then sum=(sha256sum); else sum=(shasum -a 256); fi
  "${sum[@]}" docker-images.tar.gz ollama-models.tar.gz > SHA256SUMS
)
ok "$MODELS_DIR/SHA256SUMS (install.sh checks it on the server)"

if [ "$DEV" = 1 ]; then
  # ── 4. Python wheels (development outside Docker) ──────────────────────────
  step 4 "Downloading Python wheels for ${TARGET_PLATFORM}…"
  mkdir -p "$MODELS_DIR/wheels"
  docker run --rm \
    -v "$(pwd)/apps/api/requirements.txt:/requirements.txt:ro" \
    -v "$(pwd)/$MODELS_DIR/wheels:/wheels" \
    --platform "$TARGET_PLATFORM" \
    python:3.11-slim \
    pip download --quiet -r /requirements.txt -d /wheels/
  ok "Wheels saved: $MODELS_DIR/wheels/ ($(du -sh "$MODELS_DIR/wheels" | cut -f1))"

  # ── 5. Node packages into a local pnpm store ───────────────────────────────
  step 5 "Fetching Node packages into a local pnpm store…"
  pnpm --store-dir "$MODELS_DIR/pnpm-store" fetch --frozen-lockfile
  ok "Node packages saved: $MODELS_DIR/pnpm-store/ ($(du -sh "$MODELS_DIR/pnpm-store" | cut -f1))"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}=== Setup complete ===${RESET}"
echo "Total bundle size: $(du -sh "$MODELS_DIR" | cut -f1)"
echo ""
echo "Next step - transfer the project directory to the offline server, e.g.:"
echo "  rsync -av --exclude '.git' --exclude node_modules . admin@server:/opt/sdai/"
echo ""
echo "Then on the offline server, run:"
echo "  ./install.sh"
