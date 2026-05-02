# Bug-Detective Development Plan v3

> Generated: 2026-05-02
> Method: Five-Axis Code Review (Correctness, Readability, Architecture, Security, Performance)
> Scope: All active source files
> Version: v2.5.0 (dev branch, Sprint 2c complete)
> Previous plans: `improvement_plan.md` (v2.4.0), first `development_plan.md` (v2.3.0)

---

## Executive Summary

Sprint 1–2c resolved **all Critical and most High-severity issues** identified in earlier reviews:

- XSS vulnerabilities patched, API key handling hardened
- Rate limiting + security headers in place
- Performance optimized (shared clients, config TTL cache, vector timeout)
- 50 unit tests covering security, config, server endpoints, and XSS
- Dead code removed, package.json updated to uvicorn

The codebase is now **production-viable** for single-instance deployment. Remaining work focuses on:

1. **One broken endpoint** (`/api/search` — call signature mismatch)
2. **Dead code cleanup** (server.mjs, Next.js artifacts)
3. **Architecture debt** (rca.py 1239 lines, app.js 1127 lines)

---

## Five-Axis Assessment

| Axis | Rating | Summary |
|------|--------|---------|
| Correctness | 🟢 | Core RCA pipeline works end-to-end. `/api/search` endpoint is broken (signature mismatch). |
| Readability | 🟡 | `rca.py` (1239 lines) and `app.js` (1127 lines) are still monolithic. Magic numbers remain. |
| Architecture | 🟢 | Clean separation: FastAPI server → rca pipeline → vector store. Dead code is the main issue. |
| Security | 🟢 | XSS patched, API key session-only, rate limiting, security headers, input validation. |
| Performance | 🟢 | Shared httpx/QdrantClient, config TTL cache, vector timeout, inverted index for keyword search. |

---

## Resolved Items (Sprint 1–2c)

| Item | Sprint | Status |
|------|--------|--------|
| renderMarkdown XSS (link text/URL injection) | 1a | ✅ Fixed — `app.js:1077-1079` |
| renderMarkdown data:/vbscript: URI blocking | 1a | ✅ Fixed — `app.js:1078` |
| API Key prefix leak (first 8 chars exposed) | 1a | ✅ Fixed — `server.py:207-221` uses `api_key_set` boolean |
| `.env.spark` not in `.gitignore` | 1a | ✅ Fixed — `.gitignore` has `.env.*` with `!.env.example` |
| pytest framework + 50 unit tests | 1b | ✅ Fixed — `tests/` directory with 5 test files |
| Dead code: `deep_analysis()`, unused `api_key` param | 1c | ✅ Fixed — removed from `rca.py` |
| `asyncio.get_event_loop()` deprecation | 1c | ✅ Fixed — `rca.py:643` uses `get_running_loop()` |
| Rate limiting (in-memory sliding window) | 2a | ✅ Fixed — `server.py:112-128` |
| Security headers middleware | 2a | ✅ Fixed — `server.py:86-94` |
| API Key stored in sessionStorage (not localStorage) | 2a | ✅ Fixed — `app.js:286-287` |
| Input validation (2 MB log_text limit) | 2a | ✅ Fixed — `server.py:38-44` |
| Config TTL cache (5s in-memory) | 2b | ✅ Fixed — `config.py:40-76` |
| Shared httpx client + graceful shutdown | 2b | ✅ Fixed — `rca.py:45-63`, `server.py:82` |
| Shared QdrantClient (single instance) | 2b | ✅ Fixed — `rca.py:151-158` |
| vector_search timeout (30s) | 2b | ✅ Fixed — `rca.py:643-646` |
| `package.json` scripts pointing to Express | 2c | ✅ Fixed — now points to uvicorn |

---

## Outstanding Findings

### Critical (1 item)

#### C-1: `/api/search` endpoint call signature mismatch

- **File:** `server.py:183`
- **Problem:** `hybrid_search(req.query, top_k=req.top_k)` passes a `str` as first arg, but `rca.hybrid_search` expects `(exact_keywords: list[str], semantic_keywords: list[str], summary: str, top_k: int)` — wrong type AND missing 2 required positional args. Additionally, `hybrid_search` returns a 3-tuple `(results, kw_count, vec_count)` but the endpoint treats it as a flat list.
- **Impact:** `POST /api/search {"query":"PaperJam"}` always throws `TypeError`. Quick search UI is broken.
- **Fix:**
  ```python
  # server.py — new simple wrapper in rca.py
  # rca.py
  async def simple_keyword_search(query: str, top_k: int = 10) -> list[dict]:
      """Single-query keyword search for /api/search endpoint."""
      kw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query)
      return await keyword_search(kw_tokens, max_results=top_k)

  # server.py
  from backend.rca import simple_keyword_search

  @app.post("/api/search")
  async def search(req: SearchRequest):
      results = await simple_keyword_search(req.query, top_k=req.top_k)
      return {"query": req.query, "results": results, "count": len(results)}
  ```

---

### High (3 items)

#### H-1: Legacy Express backend `server.mjs` (1204 lines dead code)

- **File:** `server.mjs` (repo root)
- **Problem:** Entire Express/Node.js backend replaced by FastAPI but still present. Contains known security vulnerabilities (documented in `improvement_plan.md`). Confusing for new developers.
- **Fix:** Delete `server.mjs`. The Express backend has zero production use.

#### H-2: Next.js/TypeScript dead artifacts

- **Files:** `next.config.ts`, `tsconfig.json`, `eslint.config.mjs`, `postcss.config.mjs`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `next-env.d.ts`
- **Problem:** Remnants from an abandoned Next.js prototype. The project uses vanilla HTML/CSS/JS with no build step. These files add clutter and confusion.
- **Fix:** Delete all 7 files. Also delete `node_modules/` and `package-lock.json` — `package.json` is kept only for the `npm test` convenience alias.

#### H-3: `config.py` silently swallows config load errors

- **File:** `config.py:71`
- **Problem:** `except Exception: pass` masks file permission errors, encoding issues, and disk failures during `llm-config.json` loading. Falls back to defaults silently, making debugging impossible.
- **Fix:**
  ```python
  except json.JSONDecodeError:
      print(f"[config] Corrupt {LLM_CONFIG_PATH}, using defaults")
  except OSError as e:
      print(f"[config] Cannot read {LLM_CONFIG_PATH}: {e}")
  ```

---

### Medium (6 items)

| # | Item | File | Details |
|---|------|------|---------|
| M-1 | `rca.py` monolithic (1239 lines) | `rca.py` | Pipeline + search + LLM helpers in one file. Split when next major change touches it. |
| M-2 | `app.js` monolithic (1127 lines) | `app.js` | SSE handler + markdown + search UI + settings in one IIFE. Split into ES Modules when practical. |
| M-3 | Magic numbers unnamed | `rca.py` | `50000` (condensed log cap), `800` (snippet length), `300` (error line truncation) — extract to module-level constants. |
| M-4 | Inverted index never refreshes | `rca.py:221-226` | `_KEYWORD_INDEX` built once on first access. Source file changes require server restart. Add a `/api/reload-index` admin endpoint or filesystem watcher. |
| M-5 | Rate limit state in-memory only | `server.py:113` | `_RATE_LIMITS` resets on server restart. Not suitable for multi-instance deployment. Acceptable for current single-instance use. |
| M-6 | `config.py` variable indirection | `config.py:26-27` | `_gk = "GLM5_API_KEY"; GLM5_API_KEY = _env(_gk)` — just write `GLM5_API_KEY = _env("GLM5_API_KEY")`. |

---

### Low (3 items)

| # | Item | File | Details |
|---|------|------|---------|
| L-1 | `keyword_search` reads disk for context | `rca.py:720-731` | Inverted index stores `(path, line_no, line_text[:200])` but context expansion reads full files. Could cache file contents in the index. |
| L-2 | No CI/CD pipeline | — | Tests exist but no GitHub Actions workflow. |
| L-3 | Cache-busting manual version | `index.html:8,305` | `?v=7` query string must be updated manually on each deploy. Low priority. |

---

## Execution Plan

### Sprint 3: Fix + Clean (est. 2h)

| Task | Time | Acceptance Criteria |
|------|------|---------------------|
| **3.1** Fix `/api/search` endpoint | 30m | `POST /api/search {"query":"PaperJam"}` returns results without error |
| **3.2** Delete `server.mjs` | 5m | File removed, no imports reference it |
| **3.3** Delete Next.js artifacts | 5m | Remove `next.config.ts`, `tsconfig.json`, `eslint.config.mjs`, `postcss.config.mjs`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `next-env.d.ts` |
| **3.4** Fix `config.py` error handling | 10m | Config load errors logged instead of silently swallowed |
| **3.5** Fix `config.py` variable indirection | 2m | `_gk` eliminated, direct `_env("GLM5_API_KEY")` |
| **3.6** Extract magic numbers | 15m | `MAX_CONDENSED_LOG = 50000`, `MAX_SNIPPET = 800`, etc. |
| **3.7** Delete `node_modules/` and `package-lock.json` | 2m | Clean repo — `package.json` kept for npm test alias only |
| **3.8** Add test for `/api/search` fix | 15m | Unit test verifying search endpoint works with `simple_keyword_search` |

### Sprint 4: Architecture (est. 6h, optional)

Only pursue if rca.py or app.js needs major feature work. Otherwise, monolithic files are acceptable for the current team size and feature velocity.

| Task | Time | Scope |
|------|------|-------|
| **4.1** Split `rca.py` into modules | 3h | `pipeline/step0_dedup.py`, `step1_extract.py`, `step2_expand.py`, `step3_search.py`, `step4_analyze.py`, `orchestrator.py`, `search/keyword.py`, `search/vector.py`, `search/hybrid.py` |
| **4.2** Split `app.js` into ES Modules | 3h | `modules/sse.js`, `modules/markdown.js`, `modules/search.js`, `modules/settings.js`, `modules/pipeline.js`, `app.js` as entry point |

### Sprint 5: CI/CD (est. 2h)

| Task | Time | Acceptance Criteria |
|------|------|---------------------|
| **5.1** GitHub Actions workflow | 1h | Push/PR triggers pytest + ruff lint |
| **5.2** `requirements-dev.txt` | 15m | Local dev can `pip install -r requirements-dev.txt` and run tests |
| **5.3** Pre-commit hooks (optional) | 45m | ruff + pytest before commit |

---

## Metrics Tracking

| Metric | Current (Sprint 2c) | Sprint 3 Target | Goal |
|--------|---------------------|-----------------|------|
| Test count | 50 | 52+ | Cover all API endpoints |
| Broken endpoints | 1 (`/api/search`) | 0 | 0 |
| Dead code files | 8 (server.mjs + 7 Next.js) | 0 | 0 |
| Monolithic files | 2 (rca.py, app.js) | 2 | Split when needed |
| Security headers | ✅ Present | ✅ | ✅ |
| Rate limiting | ✅ Present | ✅ | ✅ |
| XSS vulnerabilities | 0 | 0 | 0 |
| API key leaks | 0 | 0 | 0 |
| Config I/O per pipeline | 1 (TTL cached) | 1 | 1 |
| Shared clients | ✅ httpx + Qdrant | ✅ | ✅ |

---

## Anti-Goals

The following are explicitly excluded to prevent over-engineering:

1. **No frontend framework** — Vanilla JS + ES Modules is sufficient. React/Vue would add build complexity with no benefit.
2. **No WebSocket** — SSE already handles streaming. No bidirectional need.
3. **No database change** — Qdrant is appropriate for the current scale.
4. **No embedding rebuild** — Existing Qdrant index works. Only rebuild if chunking strategy changes.
5. **No premature module split** — Only split `rca.py` / `app.js` when a feature change naturally requires touching multiple concerns in those files.
