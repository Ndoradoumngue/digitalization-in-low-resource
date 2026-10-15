#!/bin/bash
# SDAI Digitalisation - Offline Installation Script
#
# Run this on the target server - no internet connection required
# (as long as setup.sh was run first and ./models/ was transferred).
#
#   ./install.sh          load the bundle, then configure and start this node
#   ./install.sh --non-interactive   answers from SDAI_* variables (scripts/first-run.sh)
#   ./install.sh --dev    also install the Python wheels / Node packages for
#                         development outside Docker (needs python3-pip, pnpm)
#
# Requirements (on the offline server):
#   - Docker Engine + Docker Compose v2 (see the administrator guide)
#
# Updating an installed server with a newer bundle: run ./install.sh again -
# the new images are loaded, .env and all data are kept, the stack restarts.

set -euo pipefail

cd "$(dirname "$0")"

MODELS_DIR="./models"
BOLD='\033[1m'; RESET='\033[0m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'

step() { echo -e "\n${BOLD}[${1}/3]${RESET} ${2}"; }
ok()   { echo -e "      ${GREEN}✓ ${1}${RESET}"; }
warn() { echo -e "      ${YELLOW}⚠ ${1}${RESET}"; }
die()  { echo -e "      ${RED}✗ ${1}${RESET}" >&2; exit 1; }

DEV=0
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--dev" ]; then DEV=1; else ARGS+=("$arg"); fi
done

echo -e "${BOLD}=== SDAI Offline Installation ===${RESET}"

# ── 1. Check and load the bundle ─────────────────────────────────────────────
step 1 "Checking and loading the bundle…"

if [ -f "$MODELS_DIR/SHA256SUMS" ]; then
  # A copy over USB disks or a bad link can corrupt a 10 GB file silently.
  (cd "$MODELS_DIR" && sha256sum --quiet -c SHA256SUMS) \
    || die "the bundle is damaged (checksum mismatch) - copy ./models/ again"
  ok "bundle intact (SHA256SUMS)"
else
  warn "no models/SHA256SUMS - integrity not checked"
fi

if [ -f "$MODELS_DIR/docker-images.tar.gz" ]; then
  gunzip -c "$MODELS_DIR/docker-images.tar.gz" | docker load
  ok "images loaded from $MODELS_DIR/docker-images.tar.gz"
else
  warn "no docker-images.tar.gz - first-run will build the images (needs internet)"
fi
if [ -f "$MODELS_DIR/ollama-models.tar.gz" ]; then
  ok "VLM model archive present (restored by the ollama container at first start; for an Ollama outside Docker, first-run shows how to load it)"
else
  warn "no ollama-models.tar.gz - Ollama will have to download the model (needs internet)"
fi

# ── 2. Development dependencies (optional) ───────────────────────────────────
step 2 "Development dependencies…"
if [ "$DEV" = 1 ]; then
  if [ -d "$MODELS_DIR/wheels" ] && [ -n "$(ls -A "$MODELS_DIR/wheels" 2>/dev/null)" ]; then
    pip install --quiet --no-index --find-links="$MODELS_DIR/wheels/" -r apps/api/requirements.txt
    ok "Python deps installed from $MODELS_DIR/wheels/"
  else
    warn "no wheel cache in $MODELS_DIR/wheels/ (setup.sh --dev)"
  fi
  if [ -d "$MODELS_DIR/pnpm-store" ]; then
    pnpm --store-dir "$MODELS_DIR/pnpm-store" install --offline
    ok "Node packages installed from $MODELS_DIR/pnpm-store/"
  else
    warn "no pnpm offline store in $MODELS_DIR/pnpm-store/ (setup.sh --dev)"
  fi
else
  ok "skipped - not needed to run the application (./install.sh --dev to install them)"
fi

# ── 3. Configure and start this node ─────────────────────────────────────────
step 3 "Configuring and starting this node…"
exec ./scripts/first-run.sh "${ARGS[@]+"${ARGS[@]}"}"
