# Contributing to SDAI Digitalization

Thank you for taking the time to contribute. This document covers how to set up the project locally, the conventions to follow, and how to submit changes.

---

## Table of contents

1. [Getting started](#getting-started)
2. [Project layout](#project-layout)
3. [Development workflow](#development-workflow)
4. [Code conventions](#code-conventions)
5. [Testing](#testing)
6. [Submitting changes](#submitting-changes)
7. [What not to do](#what-not-to-do)

---

## Getting started

### Prerequisites

| Tool | Minimum version |
|---|---|
| Python | 3.11 |
| Node.js | 20 |
| pnpm | 10 |
| Docker + Compose | any recent version |
| PostgreSQL | 15 (or run via Docker) |

### First-time setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd sdai_digitalization

# 2. Install all frontend/package deps
pnpm install

# 3. Create and activate a Python virtualenv
python3.11 -m venv venv311
source venv311/bin/activate

# 4. Install API production + test deps
pip install -r apps/api/requirements.txt
pip install -r apps/api/requirements.test.txt

# 5. Install pre-commit hooks
pre-commit install
```

### Running locally

```bash
# Start the FastAPI server (port 8000)
cd apps/api && uvicorn api_server:app --reload --port 8000

# Start the React dev server (port 5173) — from repo root
pnpm dev
```

### Full stack via Docker

```bash
docker compose up --build
```

---

## Project layout

```
apps/api/         — FastAPI backend (Python)
apps/frontend/    — React + Vite frontend (TypeScript)
packages/types/   — Zod schemas shared by frontend and api-client
packages/api-client/ — typed fetch wrapper used by the frontend
```

See [AGENTS.md](./AGENTS.md) for the detailed architecture reference and [CONTEXT.md](./CONTEXT.md) for current project state.

---

## Development workflow

### Branching

- Branch from `main`.
- Use short, descriptive branch names: `feat/dedup-hash`, `fix/nginx-cache-headers`, `docs/update-readme`.

### Commit messages

Write commits in the imperative mood, present tense:

```
Add SHA-256 deduplication to ingest pipeline
Fix nginx proxy_cache_path placement
Update test fixtures for bcrypt migration
```

Keep the subject line under 72 characters. Add a blank line and a body if the change needs more context.

### Pre-commit hooks

The following checks run automatically on every `git commit`:

| Hook | What it checks |
|---|---|
| `trailing-whitespace` | No trailing spaces |
| `end-of-file-fixer` | Files end with a newline |
| `check-merge-conflict` | No leftover conflict markers |
| `check-added-large-files` | No file > 500 KB committed |
| `ruff` | Python lint (auto-fixed where possible) |
| `ruff-format` | Python formatting |
| `tsc` | TypeScript type-check across all packages |

Run them manually at any time:

```bash
pre-commit run --all-files
```

---

## Code conventions

### Python (FastAPI)

- All data routes **must** use `Depends(get_current_user)`. Admin-only routes additionally use `Depends(require_admin)`.
- Never use `allow_origins=["*"]` with `allow_credentials=True`.
- Import `_engine` and `_session` from `ingest_router` **lazily** (inside function bodies) in `auth.py` and `audit.py` — this avoids a circular import.
- `log_action()` must never propagate exceptions — it swallows them by design.
- Use `bcrypt` directly for password hashing — `passlib` is not a dependency of this project.
- Schemas (Zod) live in `packages/types`; fetch functions live in `packages/api-client`. Do not duplicate them.

### TypeScript (React)

- `moduleResolution: "bundler"` — packages export `./src/index.ts` directly (no build step).
- Do **not** add `rootDir` to `packages/api-client/tsconfig.json`.
- All fetch calls use `credentials: "include"` for cookie-based auth.
- New pages go in `apps/frontend/src/pages/` and are registered in `App.tsx` inside `<ProtectedRoute>` (or `<AdminRoute>` for admin-only pages).

### nginx

- All nginx config templates live in `apps/frontend/`. There are three: `nginx.conf` (self-signed TLS), `nginx.letsencrypt.conf` (Let's Encrypt), `nginx.letsencrypt_pending.conf` (HTTP pending first cert).
- `proxy_cache_path` must be placed **before** the `server {}` block (i.e., at the `http` context level).

### General

- Do not bake `documents/` into Docker images — always mount as a volume.
- Default to writing no comments. Add one only when the *why* is non-obvious — a hidden constraint, a workaround for a specific bug, a subtle invariant.
- Don't add error handling or validation for scenarios that cannot happen. Only validate at system boundaries (user input, external APIs).

---

## Testing

### Run the standard test suite

```bash
# From repo root
pnpm test:api

# Or directly with pytest (excludes performance benchmarks)
cd apps/api
venv311/bin/pytest -m "not benchmark" -v
```

### Run performance benchmarks

```bash
venv311/bin/pytest -m benchmark -v
```

Benchmarks are excluded from the standard run by design — they are slow and not suitable for CI. Mark new benchmark tests with `@pytest.mark.benchmark` and document latency budgets with `time.perf_counter()` assertions (do **not** use `benchmark.stats.mean` — it is broken in pytest-benchmark 4.x).

### Writing tests

- Every new API route needs at least: an auth-guard test (unauthenticated → 401/403), a happy-path test, and one error-path test.
- Use the `mock_db` fixture to patch the async DB engine. Configure it with `mock_db.execute.return_value` or `mock_db.execute.side_effect`.
- Use `_make_fake_session(monkeypatch, fetchall=[...])` (defined in `test_ingest.py`) to patch `SessionLocal` for session-based queries.
- Call async helpers from sync tests with `asyncio.run(some_async_fn(...))`.
- When patching a module's reference to a stdlib module (e.g., patching `time` for the middleware test), patch the **module-level attribute** (`monkeypatch.setattr(api_server, "time", mock_time)`) rather than the global stdlib module, so only the target module is affected.

### TypeScript schema tests

```bash
pnpm --filter @sdai/types test   # Zod schema tests only
pnpm test                         # all packages
```

---

## Submitting changes

1. **Open an issue first** for non-trivial changes to agree on the approach before writing code.
2. Make sure `pre-commit run --all-files` passes cleanly.
3. Make sure the full test suite passes: `pnpm test && pnpm test:api`.
4. Open a pull request against `main` with a clear description of *what* changed and *why*.
5. Keep PRs focused — one feature or fix per PR. Large refactors should be discussed in an issue first.

---

## What not to do

- Do not skip `Depends(get_current_user)` on any data route.
- Do not commit files from `documents/`, `venv/`, or `venv311/` — they are in `.gitignore`.
- Do not add `rootDir` to `packages/api-client/tsconfig.json`.
- Do not use `passlib` — use `bcrypt` directly.
- Do not use `allow_origins=["*"]` with `allow_credentials=True`.
- Do not commit `.env` files or any file containing secrets.
- Do not amend published commits or force-push to `main`.
