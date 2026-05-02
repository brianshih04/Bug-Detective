# Bug-Detective 改善計畫 (Improvement Plan)

> 審查日期：2026-05-02（第二次審查）
> 審查範圍：全專案五軸審查（正確性、可讀性、架構、安全性、效能）
> 前次審查修復狀態：7 項已修復，15+ 項未修復，另發現 9 項新問題

---

## 前次審查修復追蹤

| # | 項目 | 狀態 | 說明 |
|---|------|------|------|
| 1.1 | XSS 漏洞 | ✅ 已修復 | `app.js:1030-1092` 所有 regex 替換使用 `escapeHtml()` 回呼 |
| 1.2 | keyword_search 檔案掃描 | ✅ 已修復 | `rca.py:157-203` 記憶體反向索引 |
| 2.1 | CORS `*` | ✅ 已修復（FastAPI） | `server.py:77-81` 限定來源（Express 端仍缺） |
| 2.2 | SSL verify=False | ✅ 已修復 | 已移除 |
| 3.1 | Singleton Retriever | ✅ 已修復 | `rca.py:134-154` `_RETRIEVER_CACHE` |
| 3.2 | httpx 連線池 | ✅ 已修復 | `rca.py:44-54` `_shared_http_client` |
| 3.3 | Markdown 增量渲染 | ✅ 已修復 | `app.js:516-565`（後改為 rAF 節流） |

---

## 執行摘要

| 軸向 | Critical | High | Medium | Low | 總計 |
|------|----------|------|--------|-----|------|
| 安全性 (Security) | 2 | 1 | 4 | 1 | 8 |
| 效能 (Performance) | 0 | 2 | 2 | 1 | 5 |
| 正確性 (Correctness) | 0 | 0 | 4 | 2 | 6 |
| 架構 (Architecture) | 0 | 1 | 1 | 0 | 2 |
| 可讀性 (Readability) | 0 | 0 | 1 | 2 | 3 |

**最高風險：** Express（正式部署用的 `server.mjs`）寫入 API Key 到磁碟 + Shell Injection + 無 CORS。Dockerfile 使用 Express 而非 FastAPI，因此正式環境暴露所有 Express 端的安全漏洞。

---

## Phase 1：立即修復（本週內）

> 目標：消除 Express（正式環境）的 Critical 安全漏洞。

### 1.1 [Critical] Express 將 API Key 寫入磁碟

- **檔案：** `server.mjs:1172`
- **問題：** `PUT /api/llm-config` 執行 `fs.writeFileSync(LLM_CONFIG_PATH, JSON.stringify(llmConfig, null, 2))`，將含 `apiKey` 的完整 config 寫入 `data/llm-config.json`。FastAPI 端（`server.py:170`）已正確處理（`data.pop("api_key")`），但 Express 沒有。Dockerfile 啟動的是 Express。
- **修復方案：** 寫入前移除 `apiKey`。

```javascript
// server.mjs PUT /api/llm-config (line 1167-1175)
app.put("/api/llm-config", (req, res) => {
  const { baseUrl, apiKey, model } = req.body;
  if (baseUrl) llmConfig.baseUrl = baseUrl;
  if (apiKey !== undefined) llmConfig.apiKey = apiKey;
  if (model) llmConfig.model = model;
  // 寫入磁碟前移除 apiKey
  const diskConfig = { ...llmConfig };
  delete diskConfig.apiKey;
  fs.writeFileSync(LLM_CONFIG_PATH, JSON.stringify(diskConfig, null, 2));
  console.log("LLM config updated:", { baseUrl: llmConfig.baseUrl, model: llmConfig.model, hasKey: !!llmConfig.apiKey });
  res.json({ ok: true });
});
```

### 1.2 [Critical] Shell Injection in embedSearch

- **檔案：** `server.mjs:198`
- **問題：** `exec(cmd, ...)` 使用字串拼接，`query` 來自 `req.body.query`（使用者輸入）。`JSON.stringify(query)` 不能防止 shell 注入 — 若 query 包含 `` `rm -rf /` `` 或 `$(...)` 等 shell metacharacter，將被 shell 直譯器執行。
- **修復方案：** 改用 `execFile()` 以參數陣列傳遞，避免 shell 解析。

```javascript
import { execFile } from "child_process";

function embedSearch(query, topK = 10) {
  return new Promise((resolve, reject) => {
    const script = path.join(__dirname, "scripts", "embed-search.py");
    const env = { ...process.env };
    if (process.env.VLLM_EMBED_URL) env.VLLM_EMBED_URL = process.env.VLLM_EMBED_URL;
    const python = process.env.EMBED_PYTHON || "python3";
    execFile(
      python,
      [script, query, "--top", String(topK)],
      { timeout: 30_000, maxBuffer: 50 * 1024 * 1024, env },
      (err, stdout, stderr) => {
        if (err) {
          console.error("Embed search error:", stderr?.slice(0, 500));
          return reject(new Error(stderr?.slice(0, 200) || err.message));
        }
        try { resolve(JSON.parse(stdout)); }
        catch { reject(new Error("Failed to parse embed-search output")); }
      }
    );
  });
}
```

### 1.3 [High] Express 無 CORS 中介軟體

- **檔案：** `server.mjs`（全域）
- **問題：** Express 完全沒有 CORS 設定，FastAPI 有。但 Dockerfile 啟動 Express（`CMD ["node", "--env-file=.env", "server.mjs"]`），正式環境無 CORS 保護。
- **修復方案：** 加入 CORS 中介軟體。

```javascript
import cors from "cors";
app.use(cors({
  origin: ["http://localhost:17580", "http://127.0.0.1:17580", "https://bug.avision-gb10.org"],
  credentials: true,
}));
```

---

## Phase 2：安全性強化（下一個 Sprint）

### 2.1 [Medium] 7z 解壓縮路徑遍歷

- **檔案：** `server.mjs:80-101`
- **問題：** `readTextFilesFromDir` 未驗證解析後的路徑是否在 extraction base directory 內。惡意 7z 若含 symlink 或 `../../` 路徑，可讀取任意檔案。
- **修復方案：** 加入 `resolved.startsWith(resolvedBase)` 檢查。

```javascript
function readTextFilesFromDir(dir) {
  let combined = [];
  let extractedCount = 0;
  const resolvedBase = path.resolve(dir);
  try {
    const entries = fs.readdirSync(dir, { recursive: true, withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const full = path.join(entry.parentPath || dir, entry.name);
      const resolved = path.resolve(full);
      if (!resolved.startsWith(resolvedBase + path.sep) && resolved !== resolvedBase) {
        console.warn(`Skipping path traversal entry: ${entry.name}`);
        continue;
      }
      // ... rest unchanged
    }
  } catch {}
  return { combined: combined.join("\n\n"), extractedCount };
}
```

### 2.2 [Medium] API Key 前綴洩漏

- **檔案：** `server.py:162`
- **問題：** `cfg["api_key_masked"] = cfg["api_key"][:8] + "***"` 洩漏前 8 個字元。8 個字元足以縮小暴力破解範圍。
- **修復方案：** 改為布林旗標。

```python
if cfg.get("api_key"):
    cfg["api_key_set"] = True
    cfg["api_key"] = ""
```

### 2.3 [Medium] Rate Limiting

- **範圍：** `/api/analyze`、`/api/upload-log`
- **方案：** Express 使用 `express-rate-limit`；FastAPI 使用 `slowapi`

### 2.4 [Medium] Security Headers

```javascript
app.use((req, res, next) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'");
    next();
});
```

---

## Phase 3：效能優化（下一個 Sprint）

### 3.1 [High] embedSearch 每次啟動新 Python 進程

- **檔案：** `server.mjs:193-211`
- **問題：** 每次 semantic search 都 spawn 新 Python 進程，重新載入 ~14MB 的 embeddings（numpy + ONNX/vLLM 初始化）。
- **方案 A（推薦）：** 常駐 Python 進程，透過 stdin/stdout JSON IPC 通訊。
- **方案 B：** 以 Node.js 原生實作 embedding 搜尋（`onnxruntime-node`）。

### 3.2 [High] `load_llm_config()` 每次從磁碟讀取

- **檔案：** `rca.py:512, 801, 1023`（3 處呼叫）
- **問題：** `full_rca_stream` 呼叫 `load_llm_config()` 兩次（line 879 和 1023），`llm_expand_keywords` 呼叫一次（line 512），每次都 `json.load()` 從磁碟讀取。
- **修復方案：** 加 TTL 記憶體快取。

```python
_config_cache = {"ts": 0, "data": None}
_CACHE_TTL = 30  # seconds

def load_llm_config_cached() -> dict:
    if _config_cache["data"] is None or (time.time() - _config_cache["ts"]) > _CACHE_TTL:
        _config_cache["data"] = load_llm_config()
        _config_cache["ts"] = time.time()
    return _config_cache["data"]
```

### 3.3 [Medium] keyword_search 仍需讀磁碟取上下文

- **檔案：** `rca.py:694-704`
- **問題：** 反向索引儲存了 `line_text`，但取上下文（前 5 行 + 後 10 行）仍需 `read_text()` 從磁碟讀取完整檔案。在大量搜尋結果時產生多次 I/O。
- **方案：** 反向索引同時儲存每個檔案的完整行陣列（建索引時已讀取），取上下文時直接從記憶體切片。代價是記憶體使用增加（取決於程式碼庫大小）。

### 3.4 [Low] embed-search.py O(n) chunk lookup

- **檔案：** `scripts/embed-search.py:157`
- **問題：** 對每個 top result，遍歷整個 `code_index.chunks` 陣列查找對應的 `chunk_meta.id`。若 `top_k=20` 且 chunks 有 5000+ 筆，每次搜尋做 100,000+ 次比較。
- **修復方案：** 啟動時建立 `chunk_id -> content` 的 dict。

```python
# 在 load_data() 中
_chunk_by_id = {c["id"]: c for c in _code_index.get("chunks", [])}

# 在 search() 中替換 line 157
chunk_data = _chunk_by_id.get(cm["id"], {})
chunk_info["content"] = chunk_data.get("content", "")
```

---

## Phase 4：架構改善（中期規劃）

### 4.1 [High] 統一後端架構

- **現狀：** Dockerfile 使用 Express（`CMD ["node", "--env-file=.env", "server.mjs"]`），但 Express 有 3 個 Critical/High 安全漏洞（API Key 寫入、Shell Injection、無 CORS），而 FastAPI 已全部修復。雙後端導致功能不一致（API Key 處理、CORS、LLM config 格式不同）。
- **建議：** 以 FastAPI 為唯一後端。Express 的功能遷移至 FastAPI：
  1. 靜態檔案服務 → FastAPI `StaticFiles`（已有）
  2. 7z 解壓 → Python `py7zr`
  3. Semantic search → 直接從 FastAPI 呼叫 Python（無需 spawn 新進程）
  4. LLM streaming → 已有（`call_llm_stream`）
- **步驟：**
  1. 盤點 Express 獨有的 API endpoints（`/api/upload-log`、`/api/semantic-search`、`/api/analyze` polling）
  2. 將功能逐一遷移至 FastAPI
  3. 更新 Dockerfile 使用 FastAPI
  4. 移除 Express 或降級為純開發用 static proxy

### 4.2 [Medium] 前端模組化

- **檔案：** `public/app.js`（1107 行）
- **建議：** 拆分為 ES Modules（SSE handler、markdown renderer、search UI、settings modal、pipeline UI）

---

## Phase 5：正確性修正（下一個 Sprint）

### 5.1 [Medium] `condense_log` 未使用的參數

- **檔案：** `rca.py:269`
- **問題：** `def condense_log(log_text, bug_desc="", api_key="")` — `api_key` 參數在函式體內從未使用。呼叫端 `rca.py:890` 傳入了 `api_key`。
- **修復：** 移除未使用的 `api_key` 參數，更新呼叫端。

### 5.2 [Medium] `deep_analysis()` 死碼

- **檔案：** `rca.py:796-849`
- **問題：** `deep_analysis()` 是非串流版本的 LLM 分析函式，但串流 pipeline `full_rca_stream` 已直接在 line 1066-1072 呼叫 `call_llm_stream` 處理深度分析。`deep_analysis()` 從未被呼叫。
- **修復：** 刪除 `deep_analysis()` 及其相關匯入。

### 5.3 [Medium] 共用 httpx client 無 graceful shutdown

- **檔案：** `rca.py:44-54`
- **問題：** `_shared_http_client` 在模組層級建立，但 `server.py` 的 `lifespan` shutdown 階段（line 70）未關閉它。應用關閉時可能產生 "Unclosed client session" 警告或資源洩漏。
- **修復方案：** 在 `lifespan` shutdown 中關閉 client。

```python
# server.py lifespan
yield
# Shutdown
print("Shutting down...")
if hasattr(rca_module, '_shared_http_client') and rca_module._shared_http_client:
    await rca_module._shared_http_client.aclose()
```

### 5.4 [Medium] `asyncio.get_event_loop()` 已廢棄

- **檔案：** `rca.py:621`
- **修復：** 改為 `asyncio.get_running_loop()`

```python
loop = asyncio.get_running_loop()
nodes = await loop.run_in_executor(None, retriever.retrieve, query_bundle)
```

### 5.5 [Low] 重複 import

- **檔案：** `rca.py:8, 208-209`
- **問題：** `re` 和 `defaultdict` 被重複匯入（line 8 + line 208/209）。
- **修復：** 刪除 line 208-209 的重複 import。

### 5.6 [Low] embed-search.py `main()` 重複開檔

- **檔案：** `scripts/embed-search.py:203`
- **問題：** `main()` 為了取 `totalIndexed` 重新 `json.load(open(META_PATH))`，但 `load_data()` 已經讀過並快取了。應使用 `_meta` 的快取值。
- **修復：** 改為 `len(_meta.get("chunks", [])) if _meta else 0`。

---

## Phase 6：品質保證基礎建設（持續投入）

### 6.1 [Critical] 建立測試框架

- **現狀：** 專案仍然**完全沒有測試**。
- **優先順序：**
  1. **單元測試：** `backend/security.py`（sanitize 函式）、`backend/config.py`（LLM config 載入/儲存）、`renderMarkdown()` XSS 防護
  2. **整合測試：** `keyword_search()`（反向索引查詢）、SSE streaming endpoint
  3. **端到端測試：** 完整 RCA pipeline 的 happy path

### 6.2 CI/CD Pipeline

- 建立 GitHub Actions：
  - Lint：`ruff check backend/`、`eslint public/`
  - Type check：`mypy backend/`
  - 測試：`pytest backend/tests/`
  - 安全掃描：`pip audit`、`npm audit`

---

## Phase 7：可讀性改善（隨日常開發逐步處理）

### 7.1 [Medium] config.py `chr(95)` 混淆

- **檔案：** `config.py:25-26`
- **修復：** `GLM5_API_KEY = _env("GLM5_API_KEY")`，直接使用明文變數名

### 7.2 [Low] rca.py 模組拆分建議

- `rca.py` 有 1087 行，職責過多（LLM call、log dedup、regex extraction、hybrid search、RCA pipeline）
- 建議拆分時機：下次需要大幅修改任一模組時一併處理

---

## 執行時程建議

| Phase | 時程 | 預估工時 | 前置依賴 |
|-------|------|---------|---------|
| Phase 1 | 本週 | 0.5 天 | 無 |
| Phase 2 | Sprint 2 | 1-2 天 | Phase 1 |
| Phase 3 | Sprint 2-3 | 2-3 天 | Phase 1 |
| Phase 4 | Sprint 3-4 | 3-5 天 | Phase 3 |
| Phase 5 | Sprint 2 | 1 天 | Phase 1 |
| Phase 6 | Sprint 3 起，持續 | 持續投入 | Phase 1 |
| Phase 7 | 隨日常開發 | 漸進式 | 無 |

---

## 指標追蹤

| 指標 | 前次狀態 | 現狀 | 目標 |
|------|---------|------|------|
| 測試覆蓋率 | 0% | 0% | ≥ 60%（核心模組 ≥ 80%） |
| keyword_search 延遲 | 數十秒 | < 2 秒（反向索引）✅ | < 2 秒 |
| XSS 漏洞 | 4 處 | 0 處 ✅ | 0 處 |
| API Key 明文儲存 | 2 處 | 1 處（Express 未修） | 0 處 |
| CORS 設定 | `*` | FastAPI ✅ / Express ❌ | 特定 origin |
| Shell Injection | 有 | 仍有（Express） | 無 |
| 連線池重用 | 無 | httpx ✅ / retriever ✅ | 全域共享 |
