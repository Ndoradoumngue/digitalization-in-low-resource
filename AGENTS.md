# AGENTS.md — Agentic Coding Guide

This file documents everything an AI coding agent needs to work effectively in this repository.

---

## Project Overview

**SDAI Digitalization** benchmarks OCR engines and Vision-Language Models (VLMs) for structured field extraction from scanned administrative documents — primarily Chadian government forms in French and Arabic. The goal is to find reliable digitalization pipelines for low-resource settings.

---

## Monorepo Structure

```
sdai_digitalization/
├── packages/
│   ├── types/          # @sdai/types  — Zod schemas shared by API client & frontend
│   └── api-client/     # @sdai/api-client — typed fetch wrapper (credentials: include)
├── apps/
│   ├── api/            # FastAPI backend
│   │   ├── api_server.py        # App entry point + auth routes
│   │   ├── auth.py              # DB-backed auth: CurrentUser, get_current_user, require_admin
│   │   ├── audit.py             # log_action() — never raises, writes to sdai_audit_log
│   │   ├── ingest_router.py     # /api/ingest/* + _engine() + DDL helpers
│   │   ├── documents_router.py  # /api/db/*
│   │   ├── review_router.py     # /api/review/*
│   │   ├── admin_router.py      # /api/admin/* (admin-only)
│   │   └── create_admin.py      # Seed script for first admin account
│   └── frontend/       # @sdai/frontend — React 18 + Vite + Tailwind + TanStack Query
├── docker-compose.yml
├── Dockerfile.frontend
├── setup.sh            # Offline bundle preparation (internet-connected machine)
├── install.sh          # Offline install + docker compose up
├── pnpm-workspace.yaml
├── tsconfig.base.json
└── documents/          # NOT in git — mounted at runtime
```

### Package dependency graph

```
@sdai/frontend
  └── @sdai/api-client
        └── @sdai/types (zod schemas)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI 0.115, python-jose (JWT), passlib[bcrypt] |
| Frontend | React 18, TypeScript 5, Vite, Tailwind CSS v3, TanStack Query v5, React Router v6 |
| Schemas | Zod v4 (discriminated union on confidence tier) |
| Package manager | pnpm v10 (workspace) |
| Containerization | Docker — multi-stage frontend, nginx reverse proxy |
| Linting | ruff (Python), tsc (TypeScript), pre-commit |

---

## Key Commands

### Local Development

```bash
# Install all deps (run from repo root)
pnpm install

# Start the FastAPI API (port 8000)
cd apps/api && uvicorn api_server:app --reload --port 8000

# Start the React dev server (port 5173)
pnpm dev

# Type-check all packages
pnpm typecheck
```

### Docker

```bash
# Full stack (api on :8000, frontend on :80)
docker compose up --build

# Rebuild only one service
docker compose up --build api
docker compose up --build frontend
```

### Pre-commit

```bash
pre-commit install        # install git hooks
pre-commit run --all-files  # run manually
```

---

## Architecture Conventions

### API (`apps/api/`)

- All data routes require `Depends(get_current_user)` — never expose data without auth.
- Admin-only routes additionally require `Depends(require_admin)` from `auth.py`.
- Auth uses httpOnly cookies (`access_token=Bearer <jwt>`). No Authorization headers. Users are stored in PostgreSQL (`sdai_users`); JWT `jti` blocklist in `sdai_token_blocklist`.
- CORS is **disabled in Docker** (nginx proxies internally). Enable via `CORS_ORIGINS` env var for local dev only.
- `log_action()` in `audit.py` is called after state-changing operations. It swallows all exceptions — never let it propagate to callers.
- `ingest_router.py` owns `_engine()`. Both `auth.py` and `audit.py` import it lazily (inside function bodies) to avoid the circular import: `ingest_router → auth → ingest_router`.
- VLM results are read from `documents/ocr_results/json/vlm_qwen25_results.json`.
- OCR results are read from `documents/ocr_results/json/`.
- Images served from `documents/raw/` and `documents/preprocessed/`.

### Packages (`packages/`)

- TypeScript `moduleResolution: "bundler"` — packages export `./src/index.ts` directly, **no build step**.
- Path aliases in tsconfig map `@sdai/types` and `@sdai/api-client` to their `src/index.ts`.
- Do **not** add `rootDir` to `packages/api-client/tsconfig.json` — it breaks cross-package path resolution.
- All fetch calls use `credentials: "include"` for cookie-based auth.

### Frontend (`apps/frontend/`)

- `AuthContext` initialises by calling `GET /api/auth/me` on mount — sets `isLoading: true` until resolved.
- `ProtectedRoute` shows a spinner during auth init, then redirects to `/login` if no user.
- `AdminRoute` additionally redirects authenticated non-admin users to `/` (not `/login`). Use it for admin-only pages in `App.tsx`.
- `DashboardPage` fetches both OCR and VLM document lists simultaneously; only the active tab's list is displayed.
- Tab state lives in `DashboardPage` — switching tabs clears `selected`.
- The admin-only Audit Log link in the sidebar is rendered only when `user?.role === "admin"`.

---

## Adding New Features

### New API route

1. Add the FastAPI route to the appropriate router (`ingest_router.py`, `documents_router.py`, `review_router.py`, `admin_router.py`) or create a new router and register it in `api_server.py`. Always include `Depends(get_current_user)`; use `Depends(require_admin)` for admin-only routes.
2. Add the Zod schema to `packages/types/src/` (new file or extend existing).
3. Add the fetch function to `packages/api-client/src/index.ts`.
4. Export from `packages/types/src/index.ts` if adding a new schema file.

### New frontend page

1. Create `apps/frontend/src/pages/MyPage.tsx`.
2. Add a `<Route>` in `apps/frontend/src/App.tsx` wrapped in `<ProtectedRoute>` (auth-gated) or `<AdminRoute>` (admin-only).
3. Add a TanStack Query hook in `apps/frontend/src/hooks/` if the page fetches data.
4. Add navigation in the sidebar in `DashboardPage.tsx` or a new layout component. Wrap admin-only sidebar links in `{user?.role === "admin" && (…)}`.

### New VLM model

1. Add results JSON to `documents/ocr_results/json/vlm_<model>_results.json`.
2. Update `VLM_RESULTS_PATH` in `api_server.py` (or parametrize the route).
3. Verify the output matches `RawVlmResultSchema` in `packages/types/src/vlm.ts`.

---

## What to Avoid

- Do not bake `documents/` into Docker images — always mount as a volume.
- Do not use `allow_origins=["*"]` with `allow_credentials=True` in CORS — browsers reject it.
- Do not add `rootDir` to `packages/api-client/tsconfig.json`.
- Do not run `pnpm approve-builds` interactively during Docker builds — esbuild is pre-approved via `"pnpm": { "onlyBuiltDependencies": ["esbuild"] }` in root `package.json`.
- Do not skip `Depends(get_current_user)` on new data routes.
- Do not import `_engine` from `ingest_router` at module level in `auth.py` or `audit.py` — always import lazily inside the function body to avoid circular imports.
- Do not let `log_action()` failures propagate — the helper swallows exceptions by design.
- Keep schemas in `packages/types`, not duplicated in `api-client` or `frontend`.

---

## Environment Variables (API)

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_SECRET_KEY` | `change-me-in-production` | JWT signing key — **change in production** |
| `AUTH_SECURE_COOKIES` | `false` | Set `true` in production (HTTPS) |
| `AUTH_TOKEN_EXPIRE_HOURS` | `8` | JWT TTL |
| `DATABASE_URL` | `postgresql+asyncpg://sdai:sdai@localhost:5432/sdai` | asyncpg connection string |
| `OLLAMA_HOST` | `http://ollama:11434` | Points to containerised Ollama; change to `http://host.docker.internal:11434` for host Ollama |
| `CORS_ORIGINS` | *(unset)* | Comma-separated allowed origins for local dev |

Users and roles are stored in PostgreSQL, not in env vars. Use `create_admin.py` to seed the first admin account. `AUTH_USERNAME` and `AUTH_PASSWORD` no longer exist.
