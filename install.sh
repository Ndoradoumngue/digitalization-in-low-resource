#!/bin/bash
# SDAI Digitalisation — Offline Installation Script
#
# Run this on the target server — no internet connection required
# (as long as setup.sh was run first and ./models/ was transferred).
#
# Requirements (on the offline server):
#   - Docker + Docker Compose v2
#   - pnpm  (if you need local Node development outside Docker)
#   - Python 3.11 + pip  (if you need local Python development outside Docker)

set -euo pipefail

MODELS_DIR="./models"
BOLD='\033[1m'; RESET='\033[0m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'

step() { echo -e "\n${BOLD}[${1}/4]${RESET} ${2}"; }
ok()   { echo -e "      ${GREEN}✓ ${1}${RESET}"; }
warn() { echo -e "      ${YELLOW}⚠ ${1}${RESET}"; }

echo -e "${BOLD}=== SDAI Offline Installation ===${RESET}"

# ── 1. Load Docker images ─────────────────────────────────────────────────────
step 1 "Loading Docker images…"

if [ -f "$MODELS_DIR/docker-images.tar.gz" ]; then
  docker load < "$MODELS_DIR/docker-images.tar.gz"
  ok "Images loaded from $MODELS_DIR/docker-images.tar.gz"
else
  warn "No docker-images.tar.gz found. Attempting to pull from internet…"
  docker compose pull
fi

# ── 2. Python wheels (local development outside Docker) ───────────────────────
step 2 "Installing Python dependencies…"

if [ -d "$MODELS_DIR/wheels" ] && [ -n "$(ls -A "$MODELS_DIR/wheels" 2>/dev/null)" ]; then
  # Install into the active Python environment (useful for local dev / scripts).
  # The Docker API image already has these baked in during docker compose build.
  pip install --quiet --no-index \
    --find-links="$MODELS_DIR/wheels/" \
    -r apps/api/requirements.txt
  ok "Python deps installed from $MODELS_DIR/wheels/"
else
  warn "No wheel cache found — skipping (Docker image already includes all deps)"
fi

# ── 3. Node packages (local development outside Docker) ───────────────────────
step 3 "Installing Node packages…"

if [ -d "$MODELS_DIR/pnpm-store" ]; then
  pnpm --store-dir "$MODELS_DIR/pnpm-store" install --offline
  ok "Node packages installed from $MODELS_DIR/pnpm-store/"
else
  warn "No pnpm offline store found — skipping (not needed for Docker-only deployment)"
fi

# ── 4. Start the stack ────────────────────────────────────────────────────────
step 4 "Starting services (Docker Compose)…"

if [ ! -f ".env" ]; then
  cp .env.example .env
  warn ".env created from .env.example — edit AUTH_SECRET_KEY before production use"
fi

# Build any services that need local source (api, frontend).
# Pre-pulled images (postgres, ollama) are already loaded and won't be re-pulled.
docker compose build --quiet
docker compose up -d

ok "Stack started"

# ── Post-install instructions ─────────────────────────────────────────────────
echo ""
echo -e "${BOLD}=== Installation complete ===${RESET}"
echo ""
echo "The Ollama model will load from the bundled archive on first start."
echo "This takes about 30–60 seconds — check progress with:"
echo "  docker compose logs -f ollama"
echo ""
echo "Once the stack is healthy, create the first admin user:"
echo ""
echo -e "  ${BOLD}docker compose exec api python create_admin.py \\"
echo -e "    --email admin@ministry.td --password yourpassword${RESET}"
echo ""
echo "Then open http://localhost in your browser."
