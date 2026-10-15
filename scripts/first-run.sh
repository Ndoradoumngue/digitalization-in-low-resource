#!/bin/bash
# SDAI - first-run configuration of one node (run from the project root).
#
#   ./scripts/first-run.sh                    asks the questions
#   ./scripts/first-run.sh --non-interactive  takes the answers from SDAI_* variables
#
# What it does, in order:
#   1. checks Docker / Docker Compose
#   2. writes .env (from .env.example): role, NODE_ID, language, extraction
#      model (Ollama), HTTPS, sync, backups - and generates every missing secret (never overwrites one)
#   3. loads the bundled images (models/docker-images.tar.gz) if they are missing
#   4. cloud node only, on its very first start: python -m sync.join
#   5. starts the stack (docker compose up -d --no-build) and waits for the API
#   6. local / single node: names the organisation and creates the first admin
#   7. tests the link to the peer node (python -m sync.check)
#
# Roles:
#   single  one server, no cloud copy
#   local   the on-site server (GPU, documents) - it connects to the cloud node
#   cloud   the internet-facing copy - configure the local node first, then
#           paste its SYNC_NODE_TOKEN and AUTH_SECRET_KEY here
#
# Answers for --non-interactive (and defaults for the questions):
#   SDAI_ROLE SDAI_NODE_ID SDAI_LANGUAGE SDAI_TZ SDAI_HTTPS_MODE SDAI_DOMAIN
#   SDAI_LE_EMAIL SDAI_CLOUD_URL SDAI_SYNC_TOKEN SDAI_AUTH_SECRET
#   SDAI_OLLAMA_MODE SDAI_OLLAMA_HOST
#   SDAI_BACKUP_SFTP SDAI_ORG_NAME SDAI_ADMIN_EMAIL SDAI_ADMIN_PASSWORD
#
# Where the extraction model (Ollama) runs - local / single node, OLLAMA_MODE:
#   docker          in Docker, on the CPU (works everywhere, slow)
#   docker-nvidia   in Docker, on an NVIDIA GPU (Linux + NVIDIA Container Toolkit)
#   docker-amd      in Docker, on an AMD GPU (Linux + ROCm)
#   host            installed on this machine (a Mac: Docker can't use its GPU)
#   remote          on another machine of the network (OLLAMA_HOST)
# Change it later by running this script again.
#
# Safe to run again: existing secrets are kept, the admin is updated, a cloud
# node is joined only once.

set -euo pipefail

cd "$(dirname "$0")/.."

INTERACTIVE=1
[ "${1:-}" = "--non-interactive" ] && INTERACTIVE=0

BOLD='\033[1m'; RESET='\033[0m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'
step() { echo -e "\n${BOLD}[${1}/7]${RESET} ${2}"; }
ok()   { echo -e "      ${GREEN}✓ ${1}${RESET}"; }
warn() { echo -e "      ${YELLOW}⚠ ${1}${RESET}"; }
die()  { echo -e "      ${RED}✗ ${1}${RESET}" >&2; exit 1; }

# ── .env helpers (no python/openssl needed on the host) ───────────────────────

rand_hex() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

get_env() {
  [ -f .env ] || return 0
  grep -E "^${1}=" .env | tail -n 1 | cut -d= -f2- || true
}

set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    awk -v k="$key" -v v="$value" 'index($0, k "=") == 1 { print k "=" v; next } { print }' \
      .env > .env.tmp && cat .env.tmp > .env && rm -f .env.tmp
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

# ask VAR "question" default - the answer (SDAI_* variable or default when
# not interactive) is stored in VAR.
ask() {
  local var="$1" question="$2" default="${3:-}" answer=""
  if [ "$INTERACTIVE" = 1 ]; then
    read -r -p "      ${question}${default:+ [$default]}: " answer
  fi
  printf -v "$var" '%s' "${answer:-$default}"
}

ask_secret() {
  local var="$1" question="$2" default="${3:-}" answer=""
  if [ "$INTERACTIVE" = 1 ] && [ -z "$default" ]; then
    read -r -s -p "      ${question}: " answer
    echo
  fi
  printf -v "$var" '%s' "${answer:-$default}"
}

choose() {  # choose VAR "question" default allowed...
  local var="$1" question="$2" default="$3"
  shift 3
  local value
  while true; do
    ask value "$question ($(IFS=/; echo "$*"))" "$default"
    for allowed in "$@"; do
      if [ "$value" = "$allowed" ]; then
        printf -v "$var" '%s' "$value"
        return
      fi
    done
    [ "$INTERACTIVE" = 1 ] || die "invalid value '$value' for: $question"
    warn "answer one of: $*"
  done
}

# Compose files added for an Ollama mode (see the header)
ollama_compose() {
  case "$1" in
    docker-nvidia) echo ":docker-compose.gpu-nvidia.yml" ;;
    docker-amd) echo ":docker-compose.gpu-amd.yml" ;;
    host|remote) echo ":docker-compose.ollama-external.yml" ;;
    *) echo "" ;;
  esac
}

echo -e "${BOLD}=== SDAI - first run ===${RESET}"

# ── 1. Prerequisites ─────────────────────────────────────────────────────────
step 1 "Checking Docker…"
command -v docker >/dev/null || die "Docker is not installed (see the administrator guide)."
docker info >/dev/null 2>&1 || die "Docker is installed but not running, or this user may not use it (add it to the 'docker' group)."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing (package docker-compose-plugin / docker-compose-v2)."
ok "$(docker --version), $(docker compose version --short 2>/dev/null || echo compose v2)"

# ── 2. Configuration (.env) ──────────────────────────────────────────────────
step 2 "Configuration (.env)…"
if [ ! -f .env ]; then
  cp .env.example .env
  ok ".env created from .env.example"
else
  ok "existing .env kept - only the answers below change it"
fi
chmod 600 .env
# Bind-mounted folders: created by us, not by Docker (which would make them root's)
mkdir -p documents secrets
chmod 700 secrets

current_role="$(get_env NODE_ROLE)"
default_role="${SDAI_ROLE:-}"
if [ -z "$default_role" ]; then
  if [ "$current_role" = "cloud" ]; then default_role=cloud
  elif [ -n "$(get_env SYNC_PEER_URL)" ]; then default_role=local
  else default_role=single
  fi
fi
choose ROLE "Role of this server: single (no cloud copy), local (on-site, with a cloud copy), cloud" \
  "$default_role" single local cloud

existing_node="$(get_env NODE_ID)"
[ "$existing_node" = "local" ] && [ "$ROLE" = "cloud" ] && existing_node=""
while true; do
  ask NODE_ID "Unique name of this node (lowercase, e.g. site-main, cloud-01)" \
    "${SDAI_NODE_ID:-${existing_node:-$([ "$ROLE" = cloud ] && echo cloud-01 || echo site-main)}}"
  [[ "$NODE_ID" =~ ^[a-z0-9][a-z0-9_-]{0,39}$ ]] && break
  [ "$INTERACTIVE" = 1 ] || die "invalid NODE_ID '$NODE_ID'"
  warn "lowercase letters, digits, - and _ only (max 40)"
done

choose LANGUAGE "Interface language" "${SDAI_LANGUAGE:-$(get_env DEFAULT_LANGUAGE | grep -E '^(en|fr)$' || echo fr)}" fr en
ask TZ_VALUE "Time zone (IANA name, e.g. Europe/Paris, UTC)" "${SDAI_TZ:-$(get_env TZ | grep . || echo UTC)}"

set_env NODE_ID "$NODE_ID"
set_env NODE_ROLE "$([ "$ROLE" = cloud ] && echo cloud || echo local)"
set_env DEFAULT_LANGUAGE "$LANGUAGE"
set_env TZ "$TZ_VALUE"
set_env AUTH_SECURE_COOKIES true

# Secrets: generated once, never replaced (a new AUTH_SECRET_KEY would log
# everyone out; a new database password would not match the existing data).
[ -n "$(get_env POSTGRES_PASSWORD)" ] || set_env POSTGRES_PASSWORD "$(rand_hex)"
[ -n "$(get_env REDIS_PASSWORD)" ] || set_env REDIS_PASSWORD "$(rand_hex)"
[ -n "$(get_env RESTIC_PASSWORD)" ] || set_env RESTIC_PASSWORD "$(rand_hex)"
PG_USER="$(get_env POSTGRES_USER)"; PG_USER="${PG_USER:-sdai}"
PG_DB="$(get_env POSTGRES_DB)"; PG_DB="${PG_DB:-sdai}"
set_env DATABASE_URL "postgresql+asyncpg://${PG_USER}:$(get_env POSTGRES_PASSWORD)@db:5432/${PG_DB}"
set_env REDIS_URL "redis://:$(get_env REDIS_PASSWORD)@redis:6379"
set_env CORS_ORIGINS ""

if [ "$ROLE" = cloud ]; then
  # Shared with the local node: sessions and the sync signature.
  secret="$(get_env AUTH_SECRET_KEY)"
  ask_secret secret "AUTH_SECRET_KEY of the local node (its .env)" "${SDAI_AUTH_SECRET:-$secret}"
  token="$(get_env SYNC_NODE_TOKEN)"
  ask_secret token "SYNC_NODE_TOKEN of the local node (its .env)" "${SDAI_SYNC_TOKEN:-$token}"
  [ ${#secret} -ge 32 ] || die "AUTH_SECRET_KEY must be the local node's value (64 hex characters)."
  [ ${#token} -ge 32 ] || die "SYNC_NODE_TOKEN must be the local node's value (64 hex characters)."
  set_env AUTH_SECRET_KEY "$secret"
  set_env SYNC_NODE_TOKEN "$token"
else
  [ -n "$(get_env AUTH_SECRET_KEY)" ] || set_env AUTH_SECRET_KEY "$(rand_hex)"
  if [ "$ROLE" = local ]; then
    [ -n "$(get_env SYNC_NODE_TOKEN)" ] || set_env SYNC_NODE_TOKEN "${SDAI_SYNC_TOKEN:-$(rand_hex)}"
  else
    set_env SYNC_NODE_TOKEN ""
  fi
fi

if [ "$ROLE" = cloud ]; then
  set_env COMPOSE_FILE "docker-compose.yml:docker-compose.cloud.yml"
  # The base file's on-site defaults (8080/8443) don't apply here.
  [ "$(get_env HTTP_PORT)" != 8080 ] || set_env HTTP_PORT 80
  [ "$(get_env HTTPS_PORT)" != 8443 ] || set_env HTTPS_PORT 443
  set_env HTTPS_MODE letsencrypt
  ask DOMAIN "Public domain name of this cloud node (DNS must point here)" \
    "${SDAI_DOMAIN:-$(get_env LETSENCRYPT_DOMAIN)}"
  [ -n "$DOMAIN" ] || die "a domain name is required for the cloud node"
  ask LE_EMAIL "E-mail for the certificate (Let's Encrypt expiry notices)" \
    "${SDAI_LE_EMAIL:-$(get_env LETSENCRYPT_EMAIL)}"
  set_env LETSENCRYPT_DOMAIN "$DOMAIN"
  set_env LETSENCRYPT_EMAIL "$LE_EMAIL"
  set_env PUBLIC_BASE_URL "https://${DOMAIN}"
  set_env SYNC_PEER_URL ""
else
  # Extraction model: suggest the fastest option this machine can use
  default_ollama="$(get_env OLLAMA_MODE)"
  if [ -z "$default_ollama" ]; then
    if [ "$(uname -s)" = Darwin ]; then default_ollama=host
    elif command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then default_ollama=docker-nvidia
    else default_ollama=docker
    fi
  fi
  choose OLLAMA_MODE "Where the extraction model (Ollama) runs: docker (CPU), docker-nvidia, docker-amd, host (installed on this machine), remote (another machine)" \
    "${SDAI_OLLAMA_MODE:-$default_ollama}" docker docker-nvidia docker-amd host remote
  case "$OLLAMA_MODE" in
    host) OLLAMA_URL="http://host.docker.internal:11434" ;;
    remote)
      current_url="$(get_env OLLAMA_HOST | grep -v -E 'localhost|host\.docker\.internal|//ollama:' || true)"
      ask OLLAMA_URL "Address of the Ollama machine (e.g. http://192.168.1.20:11434)" "${SDAI_OLLAMA_HOST:-$current_url}"
      [[ "$OLLAMA_URL" =~ ^https?://[^/]+ ]] || die "the Ollama address must look like http://192.168.1.20:11434"
      OLLAMA_URL="${OLLAMA_URL%/}"
      ;;
    *) OLLAMA_URL="" ;;
  esac
  set_env OLLAMA_MODE "$OLLAMA_MODE"
  [ -z "$OLLAMA_URL" ] || set_env OLLAMA_HOST "$OLLAMA_URL"
  if [ "$OLLAMA_MODE" = docker-nvidia ] && ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
    warn "Docker has no NVIDIA runtime - install the NVIDIA Container Toolkit (administrator guide), or the ollama service will not start"
  fi
  set_env COMPOSE_FILE "docker-compose.yml$(ollama_compose "$OLLAMA_MODE")"
  choose HTTPS_MODE "HTTPS certificate: self_signed (intranet) or letsencrypt (public domain)" \
    "${SDAI_HTTPS_MODE:-$(get_env HTTPS_MODE | grep . || echo self_signed)}" self_signed letsencrypt
  set_env HTTPS_MODE "$HTTPS_MODE"
  if [ "$HTTPS_MODE" = letsencrypt ]; then
    ask DOMAIN "Public domain name of this server" "${SDAI_DOMAIN:-$(get_env LETSENCRYPT_DOMAIN)}"
    ask LE_EMAIL "E-mail for the certificate" "${SDAI_LE_EMAIL:-$(get_env LETSENCRYPT_EMAIL)}"
    set_env LETSENCRYPT_DOMAIN "$DOMAIN"
    set_env LETSENCRYPT_EMAIL "$LE_EMAIL"
  else
    ask DOMAIN "Name or IP address users type to reach this server" \
      "${SDAI_DOMAIN:-$(get_env SELF_SIGNED_CN | grep . || cat /etc/hostname)}"
    set_env SELF_SIGNED_CN "$DOMAIN"
  fi
  if [ "$ROLE" = local ]; then
    ask CLOUD_URL "Address of the cloud node (e.g. https://ged.example.org)" \
      "${SDAI_CLOUD_URL:-$(get_env SYNC_PEER_URL)}"
    [[ "$CLOUD_URL" =~ ^https?:// ]] || die "the cloud node address must start with https://"
    set_env SYNC_PEER_URL "${CLOUD_URL%/}"
    # Share links are handed out on the internet-facing node.
    set_env PUBLIC_BASE_URL "${CLOUD_URL%/}"
  else
    set_env SYNC_PEER_URL ""
  fi
fi

ask BACKUP_SFTP "Off-site backup target (restic SFTP, e.g. sftp:backup@backup.example.net:/sdai; empty = later)" \
  "${SDAI_BACKUP_SFTP:-$(get_env BACKUP_SFTP_TARGET)}"
set_env BACKUP_SFTP_TARGET "$BACKUP_SFTP"
ok ".env written (mode 600) - role ${ROLE}, node ${NODE_ID}, language ${LANGUAGE}"

# ── 3. Images ────────────────────────────────────────────────────────────────
step 3 "Docker images…"
tag="$(get_env SDAI_IMAGE_TAG)"; tag="${tag:-latest}"
missing=0
for image in api frontend backup; do
  docker image inspect "sdai/${image}:${tag}" >/dev/null 2>&1 || missing=1
done
if [ "$missing" = 1 ]; then
  if [ -f models/docker-images.tar.gz ]; then
    echo "      Loading the bundled images (a few minutes)…"
    gunzip -c models/docker-images.tar.gz | docker load
    ok "images loaded from models/docker-images.tar.gz"
  else
    warn "no bundled images (models/docker-images.tar.gz) - building them (needs internet)…"
    docker compose build
  fi
else
  ok "sdai images present (tag ${tag})"
fi

if [ -n "$BACKUP_SFTP" ] && [ ! -f backup/ssh/id_ed25519 ]; then
  mkdir -p backup/ssh && chmod 700 backup/ssh
  docker run --rm -v "$(pwd)/backup/ssh:/keys" --entrypoint ssh-keygen "sdai/backup:${tag}" \
    -q -t ed25519 -N "" -C "sdai-backup@${NODE_ID}" -f /keys/id_ed25519
  sftp_host="$(echo "$BACKUP_SFTP" | sed -E 's#^sftp:([^@]*@)?([^:/]+).*#\2#')"
  docker run --rm --entrypoint ssh-keyscan "sdai/backup:${tag}" -t ed25519 "$sftp_host" \
    > backup/ssh/known_hosts 2>/dev/null || warn "could not read the host key of $sftp_host - add backup/ssh/known_hosts"
  ok "backup SSH key created - authorise this public key on the backup server:"
  echo "        $(cat backup/ssh/id_ed25519.pub)"
fi

# ── 4. Cloud node: join the deployment ───────────────────────────────────────
step 4 "Joining the deployment…"
if [ "$ROLE" = cloud ] && [ -z "$(get_env SDAI_JOINED)" ]; then
  docker compose up -d --no-build --wait db redis
  if docker compose run --rm --no-deps api python -m sync.join; then
    set_env SDAI_JOINED "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    ok "ready to receive the deployment from the local node"
  else
    die "sync.join refused (this database already holds data) - see the administrator guide."
  fi
else
  ok "nothing to do ($([ "$ROLE" = cloud ] && echo "already joined" || echo "not a cloud node"))"
fi

# ── 5. Start ─────────────────────────────────────────────────────────────────
step 5 "Starting the services…"
docker compose up -d --no-build
echo "      Waiting for the API…"
for _ in $(seq 1 90); do
  if docker compose exec -T api python -c "
import urllib.request, urllib.error
try:
    urllib.request.urlopen('http://localhost:8000/api/auth/me', timeout=3)
except urllib.error.HTTPError:
    pass
" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[ "${ready:-0}" = 1 ] || die "the API did not start - see: docker compose logs api"
ok "API answering"

if [ "$ROLE" != cloud ] && { [ "$OLLAMA_MODE" = host ] || [ "$OLLAMA_MODE" = remote ]; }; then
  # The API container must reach Ollama and find the model there
  if models="$(docker compose exec -T api python -c "
import json, os, urllib.request
with urllib.request.urlopen(os.environ['OLLAMA_HOST'] + '/api/tags', timeout=5) as r:
    print(' '.join(m['name'] for m in json.load(r).get('models', [])))
" 2>/dev/null)"; then
    if echo "$models" | grep -q 'qwen2.5vl:7b'; then
      ok "Ollama reachable at ${OLLAMA_URL}, model qwen2.5vl:7b present"
    else
      warn "Ollama answers at ${OLLAMA_URL} but has no qwen2.5vl:7b - load it on that machine:"
      echo "        ollama pull qwen2.5vl:7b    (internet), or from this bundle (administrator guide):"
      echo "        tar xzf models/ollama-models.tar.gz -C ~/.ollama        (macOS)"
      echo "        sudo tar xzf models/ollama-models.tar.gz -C /usr/share/ollama/.ollama && sudo chown -R ollama: /usr/share/ollama/.ollama   (Linux)"
    fi
  else
    warn "Ollama does not answer at ${OLLAMA_URL} - start it there (ollama serve$([ "$OLLAMA_MODE" = remote ] && echo ', listening on the network: OLLAMA_HOST=0.0.0.0:11434'))"
  fi
fi

# ── 6. Organisation and first administrator ──────────────────────────────────
step 6 "Organisation and administrator…"
if [ "$ROLE" = cloud ]; then
  ok "users and settings come from the local node with the first sync"
else
  ask ORG_NAME "Name of the organisation" "${SDAI_ORG_NAME:-}"
  if [ -n "$ORG_NAME" ]; then
    docker compose exec -T api python create_tenant.py --slug default --name "$ORG_NAME" \
      --locale "$LANGUAGE" >/dev/null
    ok "organisation: $ORG_NAME"
  fi
  ask ADMIN_EMAIL "E-mail of the first administrator (empty = skip)" "${SDAI_ADMIN_EMAIL:-}"
  if [ -n "$ADMIN_EMAIL" ]; then
    while true; do
      ask_secret ADMIN_PASSWORD "Password (at least 8 characters)" "${SDAI_ADMIN_PASSWORD:-}"
      [ ${#ADMIN_PASSWORD} -ge 8 ] && break
      [ "$INTERACTIVE" = 1 ] || die "SDAI_ADMIN_PASSWORD must have at least 8 characters"
      warn "too short"
    done
    # Passed through the environment, never on a command line (ps).
    SDAI_ADMIN_PASSWORD="$ADMIN_PASSWORD" docker compose exec -T -e SDAI_ADMIN_PASSWORD api \
      python create_admin.py --email "$ADMIN_EMAIL" --tenant default --role admin
    ok "administrator: $ADMIN_EMAIL"
  fi
fi

# ── 7. Link to the peer node ─────────────────────────────────────────────────
step 7 "Testing the link to the peer node…"
if [ "$ROLE" = single ]; then
  ok "single server - no peer"
elif docker compose exec -T sync python -m sync.check; then
  ok "sync link OK"
else
  warn "not reachable yet - normal if the other node is not installed; re-test later with:"
  echo "        docker compose exec sync python -m sync.check"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
https_port="$(get_env HTTPS_PORT)"
if [ "$ROLE" = cloud ] || [ "$(get_env HTTPS_MODE)" = letsencrypt ]; then
  url="https://$(get_env LETSENCRYPT_DOMAIN)"
else
  url="https://$(get_env SELF_SIGNED_CN):${https_port:-8443}"
fi
echo ""
echo -e "${BOLD}=== Node ${NODE_ID} (${ROLE}) is running ===${RESET}"
echo "  Address: ${url}"
[ "$ROLE" = cloud ] || echo "  Extraction model: OLLAMA_MODE=${OLLAMA_MODE}${OLLAMA_URL:+ (${OLLAMA_URL})}"
if [ "$(get_env HTTPS_MODE)" = letsencrypt ]; then
  echo "  Certificate (once DNS points here):"
  echo "    docker compose exec frontend certbot --nginx -d $(get_env LETSENCRYPT_DOMAIN) \\"
  echo "      --email $(get_env LETSENCRYPT_EMAIL) --agree-tos --non-interactive"
  echo "    docker compose restart frontend"
fi
echo ""
echo -e "  ${BOLD}Keep a copy of these, away from the servers (password manager, safe):${RESET}"
echo "    RESTIC_PASSWORD  - without it the backups cannot be read"
[ "$ROLE" = local ] && echo "    AUTH_SECRET_KEY and SYNC_NODE_TOKEN - asked when installing the cloud node"
echo "    (all in $(pwd)/.env)"
