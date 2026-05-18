# CONTEXT.md — Current Project State

This file captures what has been built, why, and what is known to be incomplete or pending. Update it as the project evolves.

---

## What Is Built

### Authentication

- JWT-based login via httpOnly cookie (`access_token=Bearer <token>`, `SameSite=Lax`).
- Single hardcoded user configured via env vars (`AUTH_USERNAME`, `AUTH_PASSWORD`).
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`.
- React: `AuthContext` + `ProtectedRoute` + `LoginPage`.
- All API data routes require a valid JWT — unauthenticated requests get HTTP 401.

### VLM Extraction Tab

- Reads `documents/ocr_results/json/vlm_qwen25_results.json` (output of `vlm_ollama_test.py`).
- Zod discriminated union on `extraction_confidence` → tier: `high | medium | low | failed`.
- Frontend renders `VlmPanel` — shows extracted fields keyed by confidence tier.
- Sidebar lists all filenames present in the VLM results JSON.

### OCR Comparison Tab

- Reads per-document JSON files from `documents/ocr_results/json/`.
- Shows side-by-side engine results (Tesseract, EasyOCR, etc.) via `OcrPanel`.
- Sidebar lists documents that have an OCR result file.

### Document Image Viewer

- Images served from `GET /images/raw/{filename}` and `GET /images/preprocessed/{filename}`.
- Rendered inside `OcrPanel` alongside the extracted text.

### Docker Deployment

- `Dockerfile.api`: Python 3.11 FastAPI, `documents/` mounted as volume.
- `Dockerfile.frontend`: multi-stage — pnpm build → nginx. nginx proxies `/api/` and `/images/` to the API container so the browser sees a single origin (no CORS needed).
- `docker-compose.yml`: `api` + `frontend` services, shared network, env vars for auth config.

---

## Architecture Decisions

| Decision | Reason |
|---|---|
| httpOnly cookie (not localStorage JWT) | Prevents XSS token theft |
| CORS disabled in Docker | nginx proxies internally — no cross-origin request |
| Zod discriminated union on `tier` | Guarantees exhaustive handling of confidence tiers in TypeScript |
| packages export `./src/index.ts` directly | No build step needed for workspace packages; Vite resolves them at dev time |
| `moduleResolution: "bundler"` | Required for Vite + TypeScript path aliases to resolve workspace packages correctly |
| `documents/` as a volume | Documents are not part of the source code; avoids bloating Docker images |

---

## Known Limitations / Not Yet Built

| Feature | Status |
|---|---|
| Document upload UI | Not built — documents must be placed in `documents/` manually |
| Document browser with filters/search | Not built — sidebar is a flat list |
| Categories / document type grouping | Not built |
| Field correction / annotation UI | Not built — results are read-only |
| Multi-user auth | Not built — single hardcoded user |
| VLM model selector | Not built — only `vlm_qwen25_results.json` is wired |
| Pagination | Not built — all documents loaded at once |
| Mobile / responsive layout | Not tested — designed for desktop |

---

## File Layout Reference

```
documents/                          # NOT in git, mounted at runtime
  raw/                              # original scanned images
  preprocessed/                     # pre-processed variants (denoised, binarised, etc.)
  ocr_results/
    json/
      vlm_qwen25_results.json       # VLM batch results (one entry per document)
      <filename>.json               # per-document OCR results

apps/frontend/src/
  context/AuthContext.tsx           # global auth state (user, login, logout)
  components/auth/ProtectedRoute.tsx
  pages/LoginPage.tsx
  pages/DashboardPage.tsx           # main app shell — sidebar + tab switcher + panels
  components/DocumentList.tsx       # sidebar document list with hasResult indicator
  components/OcrPanel.tsx           # OCR comparison view
  components/VlmPanel.tsx           # VLM extraction view
  hooks/useDocuments.ts             # TanStack Query hooks for OCR
  hooks/useVlm.ts                   # TanStack Query hooks for VLM

packages/types/src/
  auth.ts                           # UserSchema, LoginResponseSchema
  ocr.ts                            # OcrDocument, DocumentEntry, EngineResult schemas
  vlm.ts                            # ExtractionResult discriminated union + parseVlmResult()

packages/api-client/src/
  index.ts                          # all fetch functions, imageUrl helper
```

---

## Suggested Next Steps

1. **Document browser** — add search/filter by document type or extraction quality tier.
2. **VLM model switcher** — allow selecting between multiple `vlm_*_results.json` files.
3. **Field correction** — allow users to edit extracted fields and save corrections to a JSON sidecar.
4. **Multi-user auth** — replace hardcoded credentials with a proper user store (SQLite + bcrypt).
5. **Upload pipeline** — allow uploading new documents and triggering OCR/VLM processing.
6. **Export** — download extracted fields as CSV or structured JSON.
