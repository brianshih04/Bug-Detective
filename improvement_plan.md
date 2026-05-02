# Bug-Detective 改善計畫 (Improvement Plan)

> 審查日期：2026-05-02
> 審查範圍：全專案五軸審查（正確性、可讀性、架構、安全性、效能）

---

## 執行摘要

| 軸向 | 嚴重 | 高 | 中 | 低 | 總計 |
|------|------|---|---|---|------|
| 安全性 (Security) | 2 | 3 | 4 | 3 | 12 |
| 效能 (Performance) | 3 | 4 | 3 | 3 | 13 |
| 架構 (Architecture) | 0 | 2 | 2 | 2 | 6 |
| 正確性 (Correctness) | 1 | 1 | 1 | 0 | 3 |
| 可讀性 (Readability) | 0 | 0 | 2 | 2 | 4 |

**最高風險項目：** `keyword_search()` 每次請求遍歷整個檔案系統 + XSS 注入漏洞 + 零測試覆蓋。

---

## Phase 1：立即修復（本週內）

> 目標：消除會導致資料外洩、注入攻擊、或嚴重效能瓶頸的問題。

### 1.1 [Critical] 修復 Markdown 渲染 XSS 漏洞

- **檔案：** `public/app.js:920-932`
- **問題：** `renderMarkdown()` 的 regex 替換直接將 `$1`/`$2` 捕獲組插入 HTML，未經 `escapeHtml()`。LLM 輸出若含惡意 markdown 連結（如 `[click](javascript:alert(...))`）會觸發 XSS。
- **修復方案：** 所有 `replace` 的回調函數中，對捕獲組呼叫 `escapeHtml()`。markdown 連結需額外檢查 `javascript:` scheme。

```javascript
// 修正前（危險）
html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" ...>$1</a>');

// 修正後（安全）
html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(_, text, url) {
    if (/^javascript:/i.test(url.trim())) return escapeHtml(text);
    return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(text) + '</a>';
});
// 同理修正所有 heading/bold/italic/code regex 替換
```

### 1.2 [Critical] 終結每次請求的檔案系統掃描

- **檔案：** `backend/rca.py:570-630`
- **問題：** `keyword_search()` 對每個 keyword 執行 `root.rglob("*")` 遍歷整個源碼目錄（2,727 檔案），且每個檔案都 `read_text()` 讀取。10 個 keyword = 最多 27,270 次檔案讀取。匹配後還會**重複讀取同一檔案**取得上下文（line 609）。
- **修復方案：** 啟動時載入 `code-index.json`（已有 12MB 的完整 chunk 索引），在記憶體中建立 inverted index，直接查表取代檔案系統掃描。

```python
# 方案：啟動時建立記憶體索引
import re
from collections import defaultdict

class KeywordIndex:
    def __init__(self, index_path: str):
        with open(index_path) as f:
            data = json.load(f)
        self.file_contents = {}   # file_path -> full_text
        self.chunks = data.get("chunks", [])
        # 預載所有檔案內容
        for chunk in self.chunks:
            fp = chunk.get("file_path", "")
            if fp and fp not in self.file_contents:
                try:
                    self.file_contents[fp] = Path(SOURCE_DIR).joinpath(fp).read_text(errors="replace")
                except Exception:
                    pass

    def search(self, keyword: str, max_results: int = 5) -> list:
        results = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        for fp, content in self.file_contents.items():
            for i, line in enumerate(content.split("\n"), 1):
                if pattern.search(line):
                    # 直接用已載入的 content 取上下文，無需再次讀檔
                    lines = content.split("\n")
                    start = max(0, i - 5)
                    end = min(len(lines), i + 10)
                    results.append((fp, i, "\n".join(lines[start:end])[:800]))
                    if len(results) >= max_results:
                        return results
        return results
```

### 1.3 [Critical] 停止將 API Key 寫入磁碟

- **檔案：** `server.mjs:1167-1175`
- **問題：** Express 的 `PUT /api/llm-config` 將含 `apiKey` 的完整 config 寫入 `data/llm-config.json`。FastAPI 端已正確處理（`data.pop("api_key")`），但 Express 端沒有。
- **修復方案：** 寫入前移除 `apiKey`，與 FastAPI 行為一致。

```javascript
// server.mjs PUT /api/llm-config
const diskConfig = { ...llmConfig };
delete diskConfig.apiKey;
fs.writeFileSync(LLM_CONFIG_PATH, JSON.stringify(diskConfig, null, 2));
```

### 1.4 [Critical] 修復 7z 解壓縮的路徑遍歷風險

- **檔案：** `server.mjs:104-150`
- **問題：** 7z 檔案可能包含 `../../` 路徑項，解壓後可寫入任意目錄。
- **修復方案：** 在 `readTextFilesFromDir` 加入路徑驗證：

```javascript
function readTextFilesFromDir(dir) {
    const results = [];
    const resolvedBase = path.resolve(dir);
    (function walk(d) {
        for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
            const full = path.join(d, entry.name);
            const resolved = path.resolve(full);
            if (!resolved.startsWith(resolvedBase)) {
                console.warn(`Skipping path traversal entry: ${entry.name}`);
                continue;
            }
            if (entry.isDirectory()) walk(full);
            else if (entry.isFile() && /\.(log|txt)$/i.test(entry.name)) {
                results.push({ name: entry.name, content: fs.readFileSync(full, "utf-8") });
            }
        }
    })(dir);
    return results;
}
```

---

## Phase 2：安全性強化（下一個 Sprint）

### 2.1 [High] CORS 限制特定來源

- **檔案：** `backend/server.py:75-79`
- **修正：** `allow_origins=["*"]` 改為 `["http://localhost:17580", "http://127.0.0.1:17580"]`

### 2.2 [High] 移除 SSL 驗證關閉

- **檔案：** `backend/server.py:220`
- **修正：** `verify=False` → 移除該參數，或改為可配置的 CA bundle

### 2.3 [High] Shell Injection 防範

- **檔案：** `server.mjs:198`
- **修正：** `exec()` 改為 `execFile()` 或 `spawn()`，以參數陣列傳遞，避免 shell 解析

```javascript
const { execFile } = require("child_process");
execFile(
    process.env.EMBED_PYTHON || "python3",
    [script, query, "--top", String(topK)],
    { timeout: 30_000, maxBuffer: 50 * 1024 * 1024, env },
    (err, stdout, stderr) => { /* ... */ }
);
```

### 2.4 [Medium] 加入 Rate Limiting

- **範圍：** `/api/analyze`、`/api/upload-log`
- **方案：** Express 使用 `express-rate-limit`（每分鐘 5 次）；FastAPI 使用 `slowapi`

### 2.5 [Medium] 加入 Security Headers

```javascript
// Express middleware
app.use((req, res, next) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'");
    next();
});
```

### 2.6 [Medium] 基本認證機制

- 加入 `API_TOKEN` 環境變數作為 Bearer Token 認證中間件
- 開發模式可跳過，生產環境強制啟用

### 2.7 [Low] API Key 前綴洩漏

- **檔案：** `backend/server.py:153-155`
- **修正：** 改為 `has_key: bool` 旗標，不顯示任何前綴字元

### 2.8 [Low] 清除 config.py 中的 chr() 混淆

- **檔案：** `backend/config.py:25-26`
- **修正：** `GLM5_API_KEY = _env("GLM5_API_KEY")`，直接使用明文變數名

---

## Phase 3：效能優化（下一個 Sprint）

### 3.1 [High] Singleton Retriever 模式

- **檔案：** `backend/rca.py:112-126`
- **問題：** `get_retriever()` 每次呼叫都建立新的 `QdrantClient` + `OllamaEmbedding` + `VectorStoreIndex`
- **修正：** 在 `server.py` 的 `lifespan` 中建立一次，存入 `app.state`，透過參數傳遞

```python
# server.py lifespan
app.state.retriever = get_retriever(similarity_top_k=20)

# rca.py 使用時接收 retriever 參數
async def vector_search(query, retriever, ...):
    nodes = retriever.retrieve(QueryBundle(query_str=query))
```

### 3.2 [High] httpx 連線池

- **檔案：** `backend/rca.py` 全域
- **問題：** `call_llm_stream` 和 `call_llm_sync` 每次建立新的 `httpx.AsyncClient`
- **修正：** 在 `lifespan` 中建立一個共享 `httpx.AsyncClient`，設 `limits=httpx.Limits(max_connections=20)`

### 3.3 [High] Markdown 增量渲染

- **檔案：** `public/app.js:519-520`
- **問題：** 每次 SSE `content` 事件都對**整段**累積文字做完整 markdown 解析 + DOM 重建
- **修正：** 追蹤上次渲染位置，僅對新增 delta 做 markdown 解析並 append DOM node。串流結束時做一次完整 re-render 確保一致性。

### 3.4 [High] embed-search.py 持久化進程

- **檔案：** `server.mjs:264-338`
- **問題：** 每次語意搜索都 spawn 新的 Python 進程，重新從磁碟載入 ~14MB 的 embeddings
- **方案 A：** 改為常駐 Python 進程，透過 stdin/stdout IPC 通訊
- **方案 B：** 以 Node.js 原生實作（使用 `onnxruntime-node` 載入 embedding model）

### 3.5 [Medium] LLM Config 記憶體快取

- **檔案：** `backend/rca.py` 多處呼叫 `load_llm_config()`
- **修正：** 使用 `functools.lru_cache` 加 TTL 快取

```python
from functools import lru_cache
import time

_config_cache = {"ts": 0, "data": None}

def load_llm_config_cached() -> dict:
    if _config_cache["data"] is None or (time.time() - _config_cache["ts"]) > 30:
        _config_cache["data"] = load_llm_config()
        _config_cache["ts"] = time.time()
    return _config_cache["data"]
```

### 3.6 [Medium] Express JSON Body Size 限制

- **檔案：** `server.mjs:29`
- **修正：** `express.json({ limit: "50mb" })` → `{ limit: "10mb" }`，大型 log 改用 `/api/upload-log`

### 3.7 [Medium] 废弃 asyncio API 替換

- **檔案：** `backend/rca.py:578`
- **修正：** `asyncio.get_event_loop()` → `asyncio.get_running_loop()`

### 3.8 [Low] embed-search.py Chunk 查詢優化

- **檔案：** `scripts/embed-search.py:156-164`
- **問題：** O(top_k × total_chunks) 巢狀迴圈查找 chunk
- **修正：** 啟動時建立 `chunk_id -> content` 的 dict

---

## Phase 4：架構改善（中期規劃）

### 4.1 [High] 統一後端架構

- **現狀：** 專案同時維護 `server.mjs`（Express）和 `backend/server.py`（FastAPI），功能重疊且容易不一致（例如 API Key 處理邏輯已經不同步）
- **建議：** 以 FastAPI 為唯一後端。Express 的功能（靜態檔案服務、7z 解壓）可以遷移至 FastAPI。保留 Express 僅作為開發時的 static proxy。
- **步驟：**
  1. 盤點 Express 獨有的 API endpoints
  2. 將遷移至 FastAPI 的功能逐一搬移
  3. Express 降級為純靜態檔案伺服器或完全移除

### 4.2 [Medium] 前端模組化

- **檔案：** `public/app.js`（979 行單一檔案）
- **建議：** 拆分為 ES Modules：
  - `sse-handler.js` — SSE 串流處理
  - `markdown-renderer.js` — Markdown 渲染（並改用 `marked` 或 `markdown-it` 函式庫）
  - `search-ui.js` — 搜索互動邏輯
  - `settings-modal.js` — 設定面板
  - `pipeline-ui.js` — Pipeline 步驟可視化

### 4.3 [Medium] 分析任務佇列管理

- **檔案：** `server.mjs:782, 1134`
- **問題：** `analyzeJobs` Map 無上限，記憶體可能無限增長
- **修正：** 加入最大並行任務數限制（如 5），超過時回傳 429 Too Many Requests

---

## Phase 5：品質保證基礎建設（中期規劃）

### 5.1 [Critical] 建立測試框架

- **現狀：** 專案**完全沒有測試**
- **優先順序：**
  1. **單元測試：** `backend/security.py`（sanitize 函式）、`backend/config.py`（LLM config 載入/儲存）、markdown 渲染函式
  2. **整合測試：** `keyword_search()`、`vector_search()`、SSE streaming endpoint
  3. **端到端測試：** 完整 RCA pipeline 的 happy path

```python
# 測試框架：pytest + httpx AsyncClient
# backend/tests/test_security.py
from backend.security import sanitize_for_cloud

def test_redacts_api_key():
    assert "sk-abc123" not in sanitize_for_cloud("my key is sk-abc123")

def test_redacts_ip():
    assert "192.168.1.100" not in sanitize_for_cloud("server at 192.168.1.100")

def test_preserves_safe_text():
    text = "function foo() returns error code 42"
    assert sanitize_for_cloud(text) == text
```

### 5.2 [High] CI/CD Pipeline

- 建立 GitHub Actions（或對應 CI）：
  - Lint：`ruff check backend/`、`eslint public/`
  - Type check：`mypy backend/`
  - 測試：`pytest backend/tests/`
  - 安全掃描：`pip audit`、`npm audit`

### 5.3 [Medium] Log 分析輸入限制

- **檔案：** `backend/server.py:27-33`
- **修正：** `AnalyzeRequest.log_text` 加入 `max_length` 驗證（如 500,000 字元）

```python
from pydantic import field_validator

class AnalyzeRequest(BaseModel):
    log_text: str
    bug_description: str = ""
    api_key: str = ""
    top_k: int = 20
    max_tokens: int = 0
    timeout: int = 0

    @field_validator("log_text")
    @classmethod
    def log_text_not_too_large(cls, v):
        if len(v) > 500_000:
            raise ValueError("log_text exceeds maximum length of 500,000 characters")
        return v
```

---

## Phase 6：可讀性改善（隨日常開發逐步處理）

### 6.1 Markdown 渲染器升級

- `public/app.js:907-964` 的手寫 regex markdown 渲染器脆弱且不安全
- 替換為 `marked` 或 `markdown-it` 等成熟函式庫，配合 sanitizer（如 DOMPurify）

### 6.2 config.py 混淆清除

- `backend/config.py:25-26` 的 `chr(95)` 混淆改為直接 `GLM5_API_KEY = _env("GLM5_API_KEY")`

### 6.3 rca.py 模組拆分

- `backend/rca.py` 有 973 行，職責過多（LLM call、log dedup、regex extraction、hybrid search、RCA pipeline）
- 建議拆分：
  - `llm.py` — LLM 呼叫輔助函式
  - `log_parser.py` — Step 0/1 的 log 處理
  - `search.py` — Step 3 的 hybrid search
  - `rca.py` — Pipeline 編排層（呼叫上述模組）

---

## 執行時程建議

| Phase | 時程 | 預估工時 | 前置依賴 |
|-------|------|---------|---------|
| Phase 1 | 本週 | 2-3 天 | 無 |
| Phase 2 | Sprint 2 | 2-3 天 | Phase 1 |
| Phase 3 | Sprint 2-3 | 3-5 天 | Phase 1 |
| Phase 4 | Sprint 3-4 | 5-7 天 | Phase 3 |
| Phase 5 | Sprint 3 起，持續 | 持續投入 | Phase 1 |
| Phase 6 | 隨日常開發 | 漸進式 | 無 |

---

## 指標追蹤

改善完成後應達成的可量化目標：

| 指標 | 現狀 | 目標 |
|------|------|------|
| 測試覆蓋率 | 0% | ≥ 60%（核心模組 ≥ 80%） |
| keyword_search 延遲 | 數十秒（27K 檔案掃描） | < 2 秒（記憶體索引） |
| XSS 漏洞 | 4 處 | 0 處 |
| API Key 明文儲存 | 2 處（Express + .env.spark） | 0 處（僅環境變數） |
| CORS 設定 | `*` | 特定 origin |
| 連線池重用 | 無（每次新建） | 全域共享 |
