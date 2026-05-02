# Bug-Detective Development Plan v4

> Generated: 2026-05-02
> Method: Five-Axis Code Review (Correctness, Readability, Architecture, Security, Performance)
> Scope: All active source files — backend, frontend, tests, config
> Version: v2.5.0 (main branch, Sprint 3 complete)
> Previous plans: v3 (`development_plan.md` Sprint 2c), v2.4 (`improvement_plan.md`), v2.3 (first `development_plan.md`)

---

## Executive Summary

Sprint 3 resolved the **sole Critical issue** (`/api/search` broken endpoint) and completed most cleanup:

- `/api/search` now uses `simple_keyword_search()` — fully functional, tested
- `server.mjs`, `next.config.ts`, `tsconfig.json`, `postcss.config.mjs`, `package-lock.json` deleted
- Config error handling improved (specific exceptions instead of bare `except`)
- Magic numbers extracted to named constants
- Test count grew from 50 → 69 (all passing)

The codebase is **production-stable** for single-instance deployment on DGX Spark. Remaining work shifts from "fix broken things" to **strategic improvements**: dead code remnants, test resilience, and CI/CD.

---

## Five-Axis Assessment

| Axis | Rating | Summary |
|------|--------|---------|
| Correctness | 🟢 | All endpoints functional. 69 tests pass. RCA pipeline works end-to-end on production data. |
| Readability | 🟢 | Named constants replace magic numbers. Monolithic files (`rca.py`, `app.js`) are large but internally well-structured with clear step boundaries and docstrings. |
| Architecture | 🟢 | Clean module boundaries: FastAPI server → RCA pipeline → vector store. Dead code reduced from 8 files to 2 remnants. |
| Security | 🟢 | XSS patched, API key session-only, rate limiting, security headers, input validation, data sanitization for cloud LLM. No known vulnerabilities. |
| Performance | 🟢 | Shared httpx/QdrantClient, config TTL cache (5s), vector search timeout (30s), inverted index for keyword search, connection pooling. |

**Verdict:** Approve for continued production use. No Critical or blocking issues.

---

## Resolved Items (Sprint 1–3)

| Item | Sprint | Status |
|------|--------|--------|
| renderMarkdown XSS (link text/URL injection) | 1a | ✅ Fixed — `app.js:1077-1079` |
| renderMarkdown data:/vbscript: URI blocking | 1a | ✅ Fixed — `app.js:1078` |
| API Key prefix leak (first 8 chars exposed) | 1a | ✅ Fixed — `server.py:207-221` uses `api_key_set` boolean |
| `.env.spark` not in `.gitignore` | 1a | ✅ Fixed — `.gitignore` has `.env.*` with `!.env.example` |
| pytest framework + 50 unit tests | 1b | ✅ Done — `tests/` directory with 5 test files |
| Dead code: `deep_analysis()`, unused `api_key` param | 1c | ✅ Removed from `rca.py` |
| `asyncio.get_event_loop()` deprecation | 1c | ✅ Fixed — `rca.py:653` uses `get_running_loop()` |
| Rate limiting (in-memory sliding window) | 2a | ✅ `server.py:112-128` |
| Security headers middleware | 2a | ✅ `server.py:86-94` |
| API Key stored in sessionStorage (not localStorage) | 2a | ✅ `app.js:286-287` |
| Input validation (2 MB log_text limit) | 2a | ✅ `server.py:38-44` |
| Config TTL cache (5s in-memory) | 2b | ✅ `config.py:39-76` |
| Shared httpx client + graceful shutdown | 2b | ✅ `rca.py:55-73`, `server.py:82` |
| Shared QdrantClient (single instance) | 2b | ✅ `rca.py:161-168` |
| vector_search timeout (30s) | 2b | ✅ `rca.py:656` |
| `package.json` scripts pointing to Express | 2c | ✅ Points to uvicorn |
| **`/api/search` broken endpoint** | 3 | ✅ Fixed — `simple_keyword_search()` wrapper, 3 tests added |
| **Delete `server.mjs`** | 3 | ✅ Removed |
| **Delete Next.js artifacts** (5 of 7 files) | 3 | ✅ `next.config.ts`, `tsconfig.json`, `postcss.config.mjs`, `package-lock.json`, `node_modules/` removed |
| **Fix `config.py` error handling** | 3 | ✅ `json.JSONDecodeError` and `KeyError/TypeError` caught specifically with logging |
| **Extract magic numbers** | 3 | ✅ Module-level constants in `rca.py` |

---

## Outstanding Findings

### High (2 items)

#### H-1: Remaining dead code artifacts (2 files)

- **Files:** `next-env.d.ts`, `eslint.config.mjs`
- **Problem:** Remnants from abandoned Next.js prototype. No references in active code. `eslint.config.mjs` is an ESLint flat config for TypeScript — meaningless in a Python + vanilla JS project.
- **Fix:** Delete both files.

#### H-2: Tests import `server` module directly — fragile path coupling

- **File:** `tests/test_server.py:8`
- **Problem:** `from server import app` works only because `conftest.py` adds `backend/` to `sys.path`, but the module name `server` is generic and could collide with other packages. Tests for `config.py`, `security.py` also use bare names via `sys.path` manipulation.
- **Impact:** Tests break if directory structure changes. IDE auto-imports may resolve wrong module.
- **Fix:** Use proper package imports: `from backend.server import app`, `from backend.config import ...`. Update `conftest.py` to add the project root instead of `backend/`.

---

### Medium (5 items)

| # | Item | File | Details |
|---|------|------|---------|
| M-1 | `rca.py` monolithic (1258 lines) | `rca.py` | Pipeline + search + LLM helpers in one file. Internally well-organized with clear step headers. Split when next major feature touches multiple concerns. |
| M-2 | `app.js` monolithic (1127 lines) | `app.js` | SSE handler + markdown + search UI + settings in one IIFE. Functional and clean. Split into ES Modules when practical. |
| M-3 | Inverted index never refreshes | `rca.py:231-236` | `_KEYWORD_INDEX` built once on first access. Source file changes require server restart. Add a `/api/reload-index` admin endpoint or filesystem watcher. |
| M-4 | Rate limit state in-memory only | `server.py:113` | `_RATE_LIMITS` resets on server restart. Not suitable for multi-instance deployment. Acceptable for current single-instance use. |
| M-5 | No `requirements-dev.txt` completeness | `requirements-dev.txt` | Missing `pytest-mock`, `ruff`. Only has `pytest` and `httpx`. Test dependencies should be installable in one command. |

---

### Low (4 items)

| # | Item | File | Details |
|---|------|------|---------|
| L-1 | `keyword_search` reads disk for context | `rca.py:730-742` | Inverted index stores `(path, line_no, line_text[:200])` but context expansion reads full files from disk. Could cache file contents in the index. |
| L-2 | No CI/CD pipeline | — | 69 tests exist but no GitHub Actions workflow. |
| L-3 | Cache-busting manual version | `index.html:8,305` | `?v=7` query string must be updated manually on each deploy. Low priority. |
| L-4 | `pnpm-workspace.yaml` and `pnpm-lock.yaml` still present | repo root | Leftover from Next.js prototype. No `pnpm` usage in current stack. |

---

## Execution Plan

### Sprint 4: Final Cleanup + Test Hardening (est. 2h)

| Task | Time | Acceptance Criteria |
|------|------|---------------------|
| **4.1** Delete `next-env.d.ts` and `eslint.config.mjs` | 2m | Both files removed |
| **4.2** Delete `pnpm-workspace.yaml` and `pnpm-lock.yaml` | 2m | Both files removed |
| **4.3** Fix test imports to use package paths | 30m | `from backend.server import app` instead of `from server import app`; `conftest.py` adds project root to `sys.path`; all 69 tests pass |
| **4.4** Complete `requirements-dev.txt` | 5m | Add `pytest-mock`, `ruff`; verify `pip install -r requirements-dev.txt` works |
| **4.5** Add ruff config to `pyproject.toml` | 10m | `[tool.ruff]` section with line-length, target-version |
| **4.6** Run ruff on codebase, fix findings | 15m | Zero ruff errors on all `.py` files |

### Sprint 5: CI/CD Pipeline (est. 2h)

| Task | Time | Acceptance Criteria |
|------|------|---------------------|
| **5.1** GitHub Actions workflow | 1h | Push/PR triggers `pytest` + `ruff check`; runs on Python 3.12 |
| **5.2** Pre-commit hooks (optional) | 45m | ruff + pytest before commit via `.pre-commit-config.yaml` |

### Sprint 6: Architecture (est. 6h, optional)

Only pursue if `rca.py` or `app.js` needs major feature work. Otherwise monolithic files are acceptable for current team size.

| Task | Time | Scope |
|------|------|-------|
| **6.1** Split `rca.py` into modules | 3h | `pipeline/step0_dedup.py`, `step1_extract.py`, `step2_expand.py`, `step3_search.py`, `step4_analyze.py`, `orchestrator.py`, `search/keyword.py`, `search/vector.py`, `search/hybrid.py` |
| **6.2** Split `app.js` into ES Modules | 3h | `modules/sse.js`, `modules/markdown.js`, `modules/search.js`, `modules/settings.js`, `modules/pipeline.js`, `app.js` as entry point |

---

## Metrics Tracking

| Metric | Current (Sprint 3) | Sprint 4 Target | Sprint 5 Target |
|--------|---------------------|-----------------|-----------------|
| Test count | 69 | 69 (imports fixed) | 69+ |
| Broken endpoints | 0 | 0 | 0 |
| Dead code files | 4 (`next-env.d.ts`, `eslint.config.mjs`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`) | 0 | 0 |
| Ruff errors | Unknown | 0 | 0 |
| CI/CD | None | None | GitHub Actions |
| Monolithic files | 2 (`rca.py`, `app.js`) | 2 | 2 (split when needed) |
| Security headers | ✅ Present | ✅ | ✅ |
| Rate limiting | ✅ Present | ✅ | ✅ |
| XSS vulnerabilities | 0 | 0 | 0 |
| API key leaks | 0 | 0 | 0 |
| Shared clients | ✅ httpx + Qdrant | ✅ | ✅ |

---

## File Inventory (Active Source Files)

| File | Lines | Role |
|------|-------|------|
| `backend/rca.py` | 1258 | Core 5-step RCA pipeline + search + LLM helpers |
| `backend/server.py` | 311 | FastAPI server, SSE endpoints, security, rate limiting |
| `backend/config.py` | 129 | Environment, LLM presets, TTL-cached config I/O |
| `backend/security.py` | 25 | Data sanitization for cloud LLM |
| `backend/ingest.py` | 182 | Code ingestion → Qdrant vector index |
| `public/index.html` | 307 | Single-page UI |
| `public/app.js` | 1127 | Frontend logic, SSE, markdown, settings |
| `public/style.css` | 1140 | Dark/light/black/coffee themes |
| `tests/conftest.py` | 33 | Mock heavy deps (qdrant, llama_index) |
| `tests/test_server.py` | 214 | API endpoint tests |
| `tests/test_config.py` | 136 | Config load/save/preset tests |
| `tests/test_security.py` | 92 | Sanitization tests |
| `tests/test_render_markdown.py` | 142 | XSS protection tests (Node.js) |
| `scripts/build-embeddings.py` | — | ONNX embedding index builder |
| `scripts/build-index.py` | — | Code index constructor |
| `scripts/embed-search.py` | — | Standalone embedding search |

---

## Anti-Goals

The following are explicitly excluded to prevent over-engineering:

1. **No frontend framework** — Vanilla JS is sufficient. React/Vue adds build complexity with no benefit.
2. **No WebSocket** — SSE handles streaming. No bidirectional need.
3. **No database change** — Qdrant is appropriate for the current scale.
4. **No embedding rebuild** — Existing Qdrant index works. Only rebuild if chunking strategy changes.
5. **No premature module split** — Only split `rca.py` / `app.js` when a feature change naturally requires it.
6. **No authentication system** — Single-instance internal tool. Rate limiting is sufficient.
7. **No observability stack** — Server logs + health endpoint are adequate for current deployment.
