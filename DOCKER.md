# Docker Quickstart

Everything you need to run SDAI Digitalization with Docker Compose — from a one-liner first boot to production HTTPS and offline deployments.

---

## Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Docker Engine | 24 | `docker --version` |
| Docker Compose | 2.20 | `docker compose version` (the `compose` sub-command, not `docker-compose`) |
| Disk space | ~10 GB | Images (~2 GB) + Ollama model (~5 GB) + data |
| RAM | 8 GB | 4 GB for Ollama, rest for services |

---

## Services at a glance

```
┌──────────────────────────────────────────────────────────┐
│                      Docker network                       │
│                                                          │
│  ┌──────────┐   ┌──────────┐   ┌────────┐  ┌────────┐  │
│  │ frontend │──▶│   api    │──▶│   db   │  │ redis  │  │
│  │  nginx   │   │ FastAPI  │   │Postgres│  │        │  │
│  │ :80/:443 │   │  :8000   │   │ :5432  │  │ :6379  │  │
│  └──────────┘   └────┬─────┘   └────────┘  └────────┘  │
│       ▲              │                                   │
│  browser             ▼                                   │
│                ┌──────────┐                              │
│                │  ollama  │                              │
│                │  :11434  │                              │
│                └──────────┘                              │
└──────────────────────────────────────────────────────────┘
```

| Service | Image | Role |
|---|---|---|
| `frontend` | custom (nginx + React build) | Serves the SPA; reverse-proxies `/api/` and `/images/` to `api` |
| `api` | custom (Python 3.11 FastAPI) | REST API, ingestion pipeline, DB writes |
| `db` | `postgres:15-alpine` | Document storage, user accounts, audit log |
| `redis` | `redis:7-alpine` | Schema/types/review-count response cache |
| `ollama` | `ollama/ollama:latest` | Runs `qwen2.5vl:7b` VLM for field extraction |

---

## Quickstart (self-signed HTTPS — default)

This is the recommended starting point for local testing, intranet, and offline ministry servers.

```bash
# 1. Clone the repo
git clone <repo-url> && cd sdai_digitalization

# 2. Create your .env file
cp .env.example .env
# At minimum, change AUTH_SECRET_KEY to a long random string:
#   AUTH_SECRET_KEY=$(openssl rand -hex 32)

# 3. Build and start the stack
#    qwen2.5vl:7b (~5 GB) is pulled automatically on first boot
docker compose up --build

# 4. Create the first admin user (run once, after the stack is healthy)
docker compose exec api python create_admin.py \
  --email admin@example.com \
  --password yourpassword \
  --full-name "Admin" \
  --tenant default
```

Open **https://localhost** (accept the browser security warning for the self-signed cert).

| URL | What it is |
|---|---|
| `https://localhost` | Dashboard |
| `https://localhost/api/docs` | Interactive API docs (Swagger UI) |

> The self-signed certificate is generated once at build time and is stable across restarts. No renewals required.

---

## HTTPS modes

Set `HTTPS_MODE` in `.env` before starting the stack.

### Mode 1 — `self_signed` (default)

No extra config. Certificate is baked into the image at build time.

```bash
HTTPS_MODE=self_signed   # this is the default; the line can be omitted
```

After confirming HTTPS works, harden the auth cookie:

```bash
# .env
AUTH_SECURE_COOKIES=true
```

```bash
docker compose restart api
```

### Mode 2 — `letsencrypt`

Requires a public domain name pointing to the server's IP and port 80 reachable from the internet.

```bash
# .env
HTTPS_MODE=letsencrypt
LETSENCRYPT_DOMAIN=sdai.example.gov.td
LETSENCRYPT_EMAIL=admin@example.gov.td
AUTH_SECURE_COOKIES=true
```

```bash
# Start the stack (nginx starts HTTP-only until the cert is issued)
docker compose up -d

# Issue the certificate — run once
docker compose exec frontend certbot --nginx \
  -d "${LETSENCRYPT_DOMAIN}" \
  --email "${LETSENCRYPT_EMAIL}" \
  --agree-tos \
  --non-interactive

# Restart to switch nginx to full HTTPS
docker compose restart frontend
```

Set up automatic renewal (add to crontab on the host):

```bash
0 3 1 * * docker compose -f /opt/sdai_digitalization/docker-compose.yml exec -T frontend certbot renew --quiet && \
          docker compose -f /opt/sdai_digitalization/docker-compose.yml exec -T frontend nginx -s reload
```

---

## Common operations

### View logs

```bash
docker compose logs -f            # all services
docker compose logs -f api        # API only
docker compose logs -f frontend   # nginx / certbot
```

### Stop / restart

```bash
docker compose stop               # stop containers, keep volumes
docker compose down               # stop and remove containers (volumes preserved)
docker compose restart api        # restart one service
```

### Rebuild after code changes

```bash
docker compose up --build api      # rebuild and restart only the API
docker compose up --build frontend # rebuild and restart only the frontend
docker compose up --build          # rebuild everything
```

### Open a shell inside a container

```bash
docker compose exec api bash
docker compose exec db psql -U sdai sdai
```

### Backup and restore

```bash
# Backup (stack must be running)
./backup.sh

# Files created:
#   backups/db_YYYYMMDD_HHMMSS.sql.gz   — PostgreSQL dump
#   backups/data_YYYYMMDD_HHMMSS.tar.gz — uploaded/processed images
```

To restore a database backup:

```bash
gunzip -c backups/db_20260101_120000.sql.gz | \
  docker compose exec -T db psql -U sdai sdai
```

### Reset everything (destructive)

```bash
docker compose down -v   # removes containers AND volumes (all data lost)
docker compose up --build
```

---

## Offline deployment

For air-gapped ministry servers with no internet access.

### Step 1 — Prepare on an internet-connected machine

```bash
./setup.sh
```

Produces `./models/` (~7–10 GB):

| File | Contents |
|---|---|
| `models/ollama-models.tar.gz` | `qwen2.5vl:7b` weights and manifest |
| `models/docker-images.tar.gz` | All Docker images |
| `models/wheels/` | Python wheels for `linux/amd64` |
| `models/pnpm-store/` | Node package cache |

### Step 2 — Transfer to the offline server

```bash
rsync -av --exclude '.git' . user@server:/opt/sdai_digitalization/
```

### Step 3 — Install and start

```bash
ssh user@server
cd /opt/sdai_digitalization
./install.sh
```

### Step 4 — Create admin user

```bash
docker compose exec api python create_admin.py \
  --email admin@example.com \
  --password yourpassword \
  --tenant default
```

---

## Environment variables

Full list in `.env.example`. Key variables:

| Variable | Default | Description |
|---|---|---|
| `AUTH_SECRET_KEY` | `dev-secret-key-change-in-production` | JWT signing key — **change this** |
| `AUTH_SECURE_COOKIES` | `false` | Set `true` when using HTTPS |
| `AUTH_TOKEN_EXPIRE_HOURS` | `8` | JWT lifetime |
| `HTTPS_MODE` | `self_signed` | `self_signed` or `letsencrypt` |
| `LETSENCRYPT_DOMAIN` | *(empty)* | Required for `letsencrypt` mode |
| `LETSENCRYPT_EMAIL` | *(empty)* | Required for `letsencrypt` mode |
| `DATABASE_URL` | `postgresql+asyncpg://sdai:sdai@db:5432/sdai` | asyncpg connection string |
| `REDIS_URL` | `redis://redis:6379` | Redis connection string |
| `OLLAMA_HOST` | `http://ollama:11434` | Change to `http://host.docker.internal:11434` to use host Ollama |
| `DATA_DIR` | `./data` | Writable directory for processed images and uploads |

---

## Troubleshooting

**The model download hangs or fails**
The first boot pulls `qwen2.5vl:7b` (~5 GB). If this fails, check network access from inside the `ollama` container or use the offline deployment workflow.

**`api` service exits immediately**
The `api` waits for `ollama` to be healthy. If Ollama takes a long time to load the model, the `api` container may restart a few times — this is normal. Check `docker compose logs ollama`.

**Browser shows "Your connection is not private"**
Expected with `self_signed` mode. Click "Advanced" → "Proceed to localhost". The warning does not appear after accepting once in most browsers.

**Port 443 or 80 already in use**
Another process (another nginx, Apache) is using the port. Stop it or change `ports` in `docker-compose.yml`.

**Permission denied on `./data/`**
The `api` container writes to `DATA_DIR`. Make sure the host directory is writable:
```bash
chmod -R 755 ./data
```
