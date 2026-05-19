# CLAUDE.md

This file is read automatically by Claude Code at the start of every session.

See [AGENTS.md](./AGENTS.md) for the full agentic coding guide: project structure, tech stack, key commands, architecture conventions, what to avoid, and how to add new features.

See [CONTEXT.md](./CONTEXT.md) for the current state of the project: what is built, known limitations, and suggested next steps.

## Quick reference

```bash
pnpm install                    # install all workspace deps
pnpm dev                        # start React dev server (port 5173)
cd apps/api && uvicorn api_server:app --reload --port 8000  # start FastAPI
pnpm typecheck                  # type-check all packages
pnpm test:api                   # run Python tests
docker compose up --build       # full stack via Docker
pre-commit run --all-files      # run all linters manually
```

## Project layout

```
apps/
  api/          ← FastAPI backend (api_server.py + routers)
  frontend/     ← React + Vite frontend
packages/
  api-client/   ← fetch functions
  types/        ← Zod schemas shared by frontend and api-client
```

## Critical rules

- All API data routes must use `Depends(get_current_user)`.
- Never add `rootDir` to `packages/api-client/tsconfig.json`.
- Never use `allow_origins=["*"]` with `allow_credentials=True`.
- Never bake `documents/` into Docker images — always mount as a volume.
- Schemas live in `packages/types`; fetch functions live in `packages/api-client`.
