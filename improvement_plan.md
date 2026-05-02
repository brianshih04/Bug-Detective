# Bug-Detective 改善計畫 (Improvement Plan)

> 審查日期：2026-05-02（第四次更新 — 全面重審）
> 審查範圍：全專案五軸審查（正確性、可讀性、架構、安全性、效能）
> 修復狀態：Critical 2/2 ✅、High 5/9 ✅、Medium 2/22 ✅、Low 0/7

---

## 修復追蹤總覽

### 第一次審查（v2.3.0 之前）— 已全數修復 ✅

| # | 項目 | 嚴重度 | 狀態 | 修復版本 |
|---|------|--------|------|----------|
| 1.1 | XSS 漏洞（renderMarkdown） | Critical | ✅ | v2.3.0 |
| 1.2 | keyword_search 檔案掃描 O(N×K) | Critical | ✅ | v2.3.0 |
| 2.1 | CORS `*` | High | ✅ | v2.3.0 |
| 2.2 | SSL verify=False | High | ✅ | v2.3.0 |
| 3.1 | Singleton Retriever | High | ✅ | v2.3.0 |
| 3.2 | httpx 連線池 | High | ✅ | v2.3.0 |
| 3.3 | Markdown 增量渲染 | High | ✅ | v2.3.0 |

### 第二次審查（v2.3.0）— 修復狀態

| # | 項目 | 嚴重度 | 狀態 | 說明 |
|---|------|--------|------|------|
| 1.1 | Dockerfile 使用 Express 啟動 | Critical | ✅ | v2.4.0 — Dockerfile 遷移 FastAPI |
| 1.2 | README 安全聲明與 Express 不符 | Critical | ✅ | v2.4.0 — Express 不再是部署目標 |
| 2.1 | README 聲稱分批分析但未實作 | High | ✅ | v2.3.0 — 已實作 Phase A/B |
| 2.2 | CHANGELOG.md 不存在 | High | ✅ | v2.3.0 |
| 2.3 | .env.example 不存在 | Medium | ✅ | v2.4.0 |
| 2.4 | README 安全段落缺 Express 注意事項 | Medium | ✅ | v2.4.0 — Express 已移除，問題不復存在 |
| 3.1 | 7z 路徑遍歷（Express） | Medium | 🟡 不適用 | Express 已廢棄，server.mjs 僅供參考 |
| 4.1 | embedSearch spawn Python（Express） | High | 🟡 不適用 | Express 已廢棄 |
| 5.1 | 統一後端架構 | High | ✅ | v2.4.0 — Dockerfile 遷移完成 |

---

## 剩餘項目

### 🔴 Critical

#### 7.1 建立測試框架

專案仍然**完全沒有測試**。優先順序：

1. **單元測試**：`backend/security.py`（sanitize 函式）、`backend/config.py`（LLM config 載入/儲存）、`renderMarkdown()` XSS 防護
2. **整合測試**：`keyword_search()`（反向索引查詢）、SSE streaming endpoint
3. **端到端測試**：完整 RCA pipeline 的 happy path

---

### 🟠 High

#### 9.1 renderMarkdown XSS — 未轉義的連結 URL 與文字

- **檔案：** `public/app.js:1026-1029`
- **問題：** `renderMarkdown()` 中 markdown 連結的文字和 URL 均未經 HTML 轉義
  - `[click](foo" onmouseover="alert(1))` → `<a href="foo" onmouseover="alert(1)" ...>` — 屬性注入
  - `[<img src=x onerror=alert(1)>](url)` → 未轉義的 HTML 標籤注入
- **風險：** LLM 回應中的 markdown 經 prompt injection 可觸發 XSS
- **修復：**
  ```javascript
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(_, text, url) {
    if (/^\s*(javascript|data|vbscript)\s*:/i.test(url)) return escapeHtml(text);
    return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + escapeHtml(text) + '</a>';
  });
  ```

#### 4.2 load_llm_config() 每次從磁碟讀取

- **檔案：** `rca.py` — 4 處呼叫（Step 2:512、Step 4:801、Pipeline:879、Pipeline:1018）
- **方案：** TTL 記憶體快取（30 秒）

```python
_config_cache = {"ts": 0, "data": None}
_CACHE_TTL = 30

def load_llm_config_cached() -> dict:
    if _config_cache["data"] is None or (time.time() - _config_cache["ts"]) > _CACHE_TTL:
        _config_cache["data"] = load_llm_config()
        _config_cache["ts"] = time.time()
    return _config_cache["data"]
```

#### 9.2 package.json scripts 指向已廢棄的 Express

- **檔案：** `package.json:8-9`
- **問題：** `"dev": "node server.mjs"` 和 `"start": "node --env-file=.env server.mjs"` 指向 Express 後端，但部署已遷移至 FastAPI
- **影響：** 新開發者會被誤導
- **修復：**
  - `dev` → `"uvicorn backend.server:app --reload --port 17580"`
  - `start` → `"python -m uvicorn backend.server:app --host 0.0.0.0 --port 17580"`
  - 或者從 package.json 移除這些 scripts（純 Python 專案不需要 Node.js 啟動命令）

---

### 🟡 Medium

#### 3.2 API Key 前綴洩漏

- **檔案：** `server.py:162-163`
- **問題：** `cfg["api_key_masked"] = cfg["api_key"][:8] + "***"` 洩漏前 8 字元
- **方案：** 改為布林旗標 `api_key_set: true/false`

#### 3.3 Rate Limiting

- **範圍：** `/api/analyze`、`/api/search`
- **方案：** FastAPI 使用 `slowapi`

#### 3.4 Security Headers

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    return response
```

#### 6.1 condense_log 未使用的 api_key 參數

- **檔案：** `rca.py:269`
- **修復：** 移除 `api_key` 參數，更新呼叫端

#### 6.2 deep_analysis() 死碼

- **檔案：** `rca.py:796-849`
- **修復：** 刪除 `deep_analysis()` 及其相關匯入（已被 `full_rca_stream` 內的 inline prompt 取代）

#### 6.3 httpx client 無 graceful shutdown

- **檔案：** `rca.py:44-54`、`server.py:69-71`
- **修復：** 在 `lifespan` shutdown 中 `await _shared_http_client.aclose()`

#### 6.4 asyncio.get_event_loop() 已廢棄

- **檔案：** `rca.py:621`
- **修復：** 改為 `asyncio.get_running_loop()`

#### 8.1 config.py chr(95) 混淆

- **檔案：** `config.py:25-26`
- **修復：** `GLM5_API_KEY=_env("GLM5_API_KEY")`

#### 4.3 keyword_search 仍需讀磁碟取上下文

- **檔案：** `rca.py:694-704`
- **方案：** 反向索引同時儲存每個檔案的完整行陣列，取上下文時直接從記憶體切片（代價：記憶體增加）

#### 5.2 前端 app.js 模組化

- **檔案：** `public/app.js`（1077 行）
- **建議：** 拆分為 ES Modules（SSE handler、markdown renderer、search UI、settings modal、pipeline UI）

#### 9.3 無輸入大小限制 — AnalyzeRequest.log_text

- **檔案：** `backend/server.py:28`
- **問題：** `log_text: str` 無 max length 約束，極大輸入可導致 OOM
- **修復：** 加上 `Field(max_length=5_000_000)` 或在 middleware 層限制 request body 大小

#### 9.4 API Key 明文存於 localStorage

- **檔案：** `public/app.js:247`
- **問題：** `_apiKey = localStorage.getItem(API_KEY_STORAGE)` — 明文存儲，XSS 可直接讀取
- **方案：** 改用 sessionStorage（關閉瀏覽器即清除）或完全不持久化（每次開啟頁面重新輸入）

#### 9.5 get_retriever 每個 top_k 建立新 QdrantClient

- **檔案：** `rca.py:145`
- **問題：** `QdrantClient(url=QDRANT_URL)` 在 `_RETRIEVER_CACHE` 每個 top_k 值都建立新的 client，造成連線洩漏
- **修復：** 共用單一 QdrantClient 實例（透過 `app.state.qdrant` 傳入或模組級別共享）

#### 9.6 .env.spark 未加入 .gitignore

- **檔案：** `.gitignore`
- **問題：** `.env.spark` 是 untracked 狀態，不在 `.gitignore` 中
- **修復：** 在 `.gitignore` 的 env files 區段加入 `.env.*`（排除 `.env.example`）

#### 9.7 renderMarkdown 未過濾 data: URI

- **檔案：** `public/app.js:1027`
- **問題：** 僅阻擋 `javascript:` scheme，`data:text/html,...` URI 可執行腳本
- **修復：** 與 9.1 一併處理，擴大 scheme 過濾範圍

#### 9.8 vector_search 無 timeout 保護

- **檔案：** `rca.py:621`
- **問題：** `run_in_executor(None, retriever.retrieve, query_bundle)` 無 timeout，若 Qdrant 掛住會永遠等待
- **修復：** 使用 `asyncio.wait_for(asyncio.get_running_loop().run_in_executor(...), timeout=30)`

#### 9.9 雙後端共存無明確標示

- **範圍：** `server.mjs` + `backend/` 並存
- **問題：** 新成員無法判斷哪個是主要後端
- **方案：** 在 `server.mjs` 頂端加明確棄用註解 `// DEPRECATED — 保留僅供參考，部署目標為 backend/ (FastAPI)`

#### 9.10 full_rca_stream 單一函式 368 行

- **檔案：** `rca.py:870-1238`
- **問題：** 整個 pipeline 邏輯集中在一個 async generator，難以測試和除錯
- **方案：** 拆分為 `step0_dedup()`、`step1_extract()`、`step2_expand()`、`step3_search()`、`step4_analyze()` 獨立函式

---

### 🟢 Low

#### 6.5 重複 import

- **檔案：** `rca.py:8,208-209`
- **修復：** 刪除 line 208-209 的重複 `import re` 和 `from collections import defaultdict`

#### 6.6 embed-search.py main() 重複開檔

- **檔案：** `scripts/embed-search.py:203`
- **問題：** `json.load(open(META_PATH))` — 檔案 handle 未關閉，且資料已在 `load_data()` 中載入
- **修復：** 改用 `load_data()` 的快取值

#### 4.4 embed-search.py O(n) chunk lookup

- **檔案：** `scripts/embed-search.py:157`
- **修復：** 啟動時建 `chunk_id → content` dict

#### 8.2 rca.py 模組拆分

- **檔案：** `backend/rca.py`（1238 行）
- **建議：** 下次大幅修改時拆分

#### 9.11 embed-search.py 檔案 handle 洩漏

- **檔案：** `scripts/embed-search.py:203`
- **問題：** `json.load(open(META_PATH))` 未關閉檔案
- **修復：** 使用 `with open(...)` 或複用 `load_data()` 快取

#### 9.12 Magic numbers 未命名常數

- **檔案：** `rca.py` 多處
- **問題：** `50000`（condensed_log 截斷）、`800`（snippet 長度）、`300`（error line 長度）等值散布在程式碼中
- **修復：** 提取為模組頂端的命名常數

#### 9.13 Cache-busting 使用 query string

- **檔案：** `public/index.html:8,292`（`?v=5`）
- **問題：** 需手動更新版本號，容易忘記
- **建議：** 低優先，目前可接受。長期方案可使用 build 工具自動 hash

---

### 🟡 不適用（Express 已廢棄）

| # | 項目 | 說明 |
|---|------|------|
| 3.1 | 7z 路徑遍歷 | Express `server.mjs:80-101` — 已非部署目標，路徑參數均為系統生成，無用戶可控注入點 |
| 4.1 | embedSearch spawn Python | Express `server.mjs:193-211` — 已非部署目標 |

---

## 指標追蹤

| 指標 | v2.2.0 | v2.3.0 | v2.4.0（現在） | 目標 |
|------|--------|--------|----------------|------|
| 測試覆蓋率 | 0% | 0% | 0% | ≥ 60%（核心模組 ≥ 80%） |
| XSS 漏洞 | 4 處 | 0 處 ✅ | **2 處（新發現）** | 0 處 |
| API Key 前綴洩漏 | — | — | 1 處 | 0 處 |
| API Key 明文存儲 | — | — | 1 處（localStorage） | 0 處 |
| API Key 寫入磁碟 | 2 處 | 1 處（Express） | 0 處 ✅ | 0 處 |
| CORS 全域開放 | ✅ | ✅ FastAPI | ✅ 全部 | 特定 origin |
| Shell Injection | 有（Express） | 有（Express） | 0 處 ✅ | 0 處 |
| keyword_search 延遲 | 數十秒 | < 2s ✅ | < 2s ✅ | < 2s |
| 連線池重用 | 無 | httpx ✅ | httpx ✅ | 全域共享 |
| Dockerfile 與 README 一致 | ❌ | ❌ | ✅ | 一致 |
| README 功能描述準確 | ❌ | ❌ | ✅ | 準確 |
| CHANGELOG.md | ❌ | ✅ | ✅ | 存在且更新 |
| .env.example | ❌ | ❌ | ✅ | 存在 |
| 死碼（deep_analysis） | — | — | 有 | 移除 |
| Rate Limiting | 無 | 無 | 無 | 有 |
| Security Headers | 無 | 無 | 無 | 有 |
| 輸入大小限制 | 無 | 無 | 無 | 有 |
| QdrantClient 共用 | — | — | 無（每 top_k 建一個） | 共用單一實例 |
| httpx graceful shutdown | — | — | 無 | 有 |
| 載入的 LLM config 磁碟 I/O | — | 每次 | 每次（×4/pipeline） | TTL 快取 |

---

## 執行時程建議

| Phase | 時程 | 預估工時 | 說明 |
|-------|------|---------|------|
| 🔴 **Sprint 1a：安全修復** | Day 1 | 2h | 9.1 renderMarkdown XSS + 9.7 data URI + 3.2 API Key 前綴 + 9.6 .env.spark |
| 🔴 **Sprint 1b：測試框架** | Day 1-2 | 4h | 7.1 — pytest 設定、security.py 單元測試、config.py 單元測試、SSE endpoint 測試 |
| 🟠 **Sprint 1c：正確性快速修** | Day 2 | 2h | 6.1~6.5 + 8.1 + 9.2 package.json scripts（多為刪除/改名） |
| 🟡 **Sprint 2a：防禦加固** | Day 3 | 2h | 3.3 Rate Limit + 3.4 Security Headers + 9.3 輸入大小限制 + 9.4 localStorage |
| 🟡 **Sprint 2b：效能優化** | Day 3 | 2h | 4.2 config cache + 4.3 keyword context 快取 + 9.5 QdrantClient 共用 + 9.8 vector timeout |
| 🟡 **Sprint 3：pipeline 重構** | Day 4-5 | 4h | 9.10 full_rca_stream 拆分 + 6.2 刪除 deep_analysis 死碼 |
| 🟢 **Sprint 4：清理** | Day 5 | 1h | 6.5 + 6.6 + 4.4 + 9.11 + 9.12 magic numbers |
| 🟡 **Sprint 5：前端模組化** | Day 6-7 | 6h | 5.2 app.js → ES Modules |
| CI/CD | Day 8 | 2h | 7.2 — GitHub Actions + lint + test |
