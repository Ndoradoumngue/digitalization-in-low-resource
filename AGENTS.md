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
│   └── frontend/       # @sdai/frontend — React 18 + Vite + Tailwind + TanStack Query
├── api_server.py       # FastAPI backend (auth + OCR + VLM routes)
├── requirements.api.txt
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.frontend
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
python api_server.py

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

### API (`api_server.py`)

- All data routes require `Depends(get_current_user)` — never expose data without auth.
- Auth uses httpOnly cookies (`access_token=Bearer <jwt>`). No Authorization headers.
- CORS is **disabled in Docker** (nginx proxies internally). Enable via `CORS_ORIGINS` env var for local dev only.
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
- `DashboardPage` fetches both OCR and VLM document lists simultaneously; only the active tab's list is displayed.
- Tab state lives in `DashboardPage` — switching tabs clears `selected`.

---

## Adding New Features

### New API route

1. Add the FastAPI route to `api_server.py` with `Depends(get_current_user)`.
2. Add the Zod schema to `packages/types/src/` (new file or extend existing).
3. Add the fetch function to `packages/api-client/src/index.ts`.
4. Export from `packages/types/src/index.ts` if adding a new schema file.

### New frontend page

1. Create `apps/frontend/src/pages/MyPage.tsx`.
2. Add a `<Route>` in `apps/frontend/src/App.tsx` wrapped in `<ProtectedRoute>` if auth-gated.
3. Add a TanStack Query hook in `apps/frontend/src/hooks/` if the page fetches data.
4. Add navigation in the sidebar in `DashboardPage.tsx` or a new layout component.

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
- Keep schemas in `packages/types`, not duplicated in `api-client` or `frontend`.

---

## Environment Variables (API)

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `AUTH_USERNAME` | `admin` | Login username |
| `AUTH_PASSWORD` | `admin` | Login password |
| `AUTH_SECURE_COOKIES` | `false` | Set `true` in production (HTTPS) |
| `AUTH_TOKEN_EXPIRE_HOURS` | `8` | JWT TTL |
| `CORS_ORIGINS` | *(unset)* | Comma-separated allowed origins for local dev |
