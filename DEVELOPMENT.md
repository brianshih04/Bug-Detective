# 🛠️ Bug-Detective 開發指南

本文件提供接手開發所需的完整技術資訊，包含架構概覽、開發環境設定、部署流程、以及程式碼規範。

---

## 📌 快速導覽

- [技術棧](#-技術棧)
- [開發環境設定](#-開發環境設定)
- [本機開發流程](#-本機開發流程)
- [專案架構](#-專案架構)
- [API 端點](#-api-端點)
- [SSE 事件協議](#-sse-事件協議)
- [部署流程](#-部署流程)
- [安全設計](#-安全設計)
- [除錯技巧](#-除錯技巧)
- [常見問題](#-常見問題)

---

## 🔧 技術棧

| 層級 | 技術 | 說明 |
|------|------|------|
| **前端** | 原生 HTML/CSS/JS | 單一 `index.html`，無框架、無 Build 步驟 |
| **後端** | FastAPI + Uvicorn | Python 非同步 Web 框架 |
| **RAG** | LlamaIndex + Qdrant | 向量搜尋 + 語意檢索 |
| **嵌入模型** | Ollama (`qwen3-embedding:8b`) | 4096 維向量 |
| **LLM** | 多 Provider | Ollama / z.ai GLM-5 / OpenRouter / MiniMax |
| **容器** | Docker + Docker Compose | 選用，目前 DGX Spark 用 host mode |

---

## 💻 開發環境設定

### 1. 安裝依賴

```bash
cd bug-detective
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn httpx pydantic qdrant-client \
    llama-index llama-index-vector-stores-qdrant \
    llama-index-embeddings-ollama python-dotenv
```

### 2. 啟動依賴服務

```bash
# Qdrant
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

# Ollama（安裝後啟動）
ollama serve
ollama pull qwen3-embedding:8b
```

### 3. 設定環境變數

複製或建立 `.env` 檔案：

```bash
SOURCE_DIR=/path/to/your/c/source/code
QDRANT_URL=http://localhost:6333
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.6:35b-a3b-200k
PORT=17580
COLLECTION_NAME=your_collection_name
EMBEDDING_MODEL=qwen3-embedding:8b
EMBEDDING_DIM=4096
```

---

## 🔄 本機開發流程

```bash
# 啟動開發伺服器（自動重載）
python -m uvicorn backend.server:app --host 0.0.0.0 --port 17580 --reload

# 或直接執行
python backend/server.py
```

前端修改 `public/index.html` 後重新整理即可（無 Build 步驟）。

### 建置嵌入索引

```bash
python -m backend.ingest
```

此步驟會掃描 `SOURCE_DIR` 下的所有 C/C++ 檔案，切割成 chunks，透過 Ollama 嵌入模型轉為向量，存入 Qdrant。

---

## 🏛️ 專案架構

### 檔案說明

#### `backend/server.py`（FastAPI 伺服器）

主要 API 伺服器，包含：
- **生命週期管理** — 啟動時連接 Qdrant，檢查 Collection 是否存在
- **靜態檔案服務** — 提供 `public/index.html`
- **SSE 串流端點** — `POST /api/analyze` 執行 RCA Pipeline 並串流輸出
- **LLM 設定 API** — CRUD 操作 `data/llm-config.json`
- **模型抓取 API** — 從任何 OpenAI 相容端點獲取模型列表

#### `backend/rca.py`（RCA Pipeline）

核心分析邏輯，927 行：
- `_chat_url()` — 自動補全 `/chat/completions` 路徑
- `call_llm_stream()` / `call_llm_sync()` — OpenAI 相容 API 呼叫封裝
- `condense_log()` — Step 0：三層規則式 Log 去重（黑名單 → 模糊去重 → 高頻壓縮）
- `extract_structured_log()` — Step 1：Regex 提取錯誤碼、函式名、檔案路徑等
- `llm_expand_keywords()` — Step 2：LLM 語意擴充
- `hybrid_search()` — Step 3：Keyword grep + Qdrant 向量搜尋 → RRF 融合
- `deep_analysis()` — Step 4：雲端 LLM 深度分析
- `full_rca_stream()` — 完整 5 步驟 Pipeline 串流產生器

#### `backend/config.py`（設定管理）

- 環境變數讀取（`.env`）
- LLM Presets 定義（Ollama / GLM-5 / OpenRouter / MiniMax）
- `load_llm_config()` / `save_llm_config()` — JSON 設定讀寫
- 自動修復 `base_url` 格式（去除多餘後綴）

#### `backend/ingest.py`（嵌入索引建置）

- 掃描 C/C++ 原始碼（`.c`, `.h`, `.cpp`, `.hpp` 等）
- LlamaIndex `SentenceSplitter` 切割（chunk_size=15000, overlap=500）
- Ollama 嵌入模型轉向量 → 存入 Qdrant

#### `backend/security.py`（資料遮蔽）

傳送至雲端 LLM 前自動遮蔽：
- API Key / Secret / Token / Password
- Bearer / Basic Auth tokens
- 內部 IP 位址（10.x, 172.16-31.x, 192.168.x）
- Email 地址
- MAC 位址

#### `public/index.html`（前端）

單一 HTML 檔案（約 1900+ 行），包含所有 CSS 和 JS：
- 暗色主題 UI
- Bug 描述 + Log 文字輸入（支援拖放檔案）
- LLM 設定 Modal（Provider / Base URL / API Key / Model）
- Pipeline 視覺化進度條（5 步驟 Circle + 計時器）
- 搜尋結果列表（可折疊）
- AI 分析分屏（思考過程 + 結果）
- Markdown 渲染（簡易實作）

---

## 📡 API 端點

### 分析

| Method | Path | 說明 |
|--------|------|------|
| `POST` | `/api/analyze` | 執行 RCA Pipeline（SSE 串流回應） |
| `POST` | `/api/search` | 快速搜尋程式碼 |

**`POST /api/analyze` Request Body：**

```json
{
  "log_text": "錯誤日誌內容...",
  "bug_description": "Bug 描述（選填）",
  "api_key": "sk-...（僅存瀏覽器，不寫入磁碟）",
  "top_k": 20
}
```

### LLM 設定

| Method | Path | 說明 |
|--------|------|------|
| `GET` | `/api/llm-config` | 取得目前 LLM 設定（API Key 回傳空字串 + masked） |
| `PUT` | `/api/llm-config` | 更新 LLM 設定（API Key 不會寫入磁碟） |
| `GET` | `/api/llm-presets` | 取得所有 Preset |
| `POST` | `/api/llm-config/preset/{provider}` | 套用 Preset |
| `POST` | `/api/fetch-models` | 抓取可用模型列表 |
| `GET` | `/api/models` | 列出 Ollama 本地模型 |

### 其他

| Method | Path | 說明 |
|--------|------|------|
| `GET` | `/` | 前端頁面 |
| `GET` | `/api/health` | 健康檢查 |
| `GET` | `/api/repos/status` | Qdrant Collection 狀態 |
| `POST` | `/api/sanitize` | 測試資料遮蔽功能 |

---

## 📡 SSE 事件協議

`POST /api/analyze` 回傳 SSE 串流，每行一個 JSON 事件：

### `status` — 狀態更新

```json
{"type": "status", "text": "🧠 Step 2/5: 語意擴充（同義詞、相關模組）..."}
```

### `step0_result` — Log 去重結果

```json
{
  "type": "step0_result",
  "data": {
    "original_lines": 15000,
    "condensed_lines": 3200,
    "reduction_pct": 78.7,
    "condensed_log": "..."
  }
}
```

### `step1_result` — 結構化萃取結果

```json
{
  "type": "step1_result",
  "data": {
    "error_codes": ["ERR_001", "ERR_042"],
    "function_names": ["handleRequest", "processData"],
    "file_paths": ["src/handler.c"],
    "exceptions": ["SIGSEGV"],
    "memory_addresses": ["0x7fff5a3b"],
    "error_lines": ["line with error..."],
    "error_lines_count": 150
  }
}
```

### `step2_result` — 語意擴充結果

```json
{
  "type": "step2_result",
  "data": {
    "summary": "問題摘要...",
    "exact": ["keyword1", "keyword2"],
    "semantic": ["synonym1", "related_module"]
  }
}
```

### `step3_result` — 搜尋結果

```json
{
  "type": "step3_result",
  "data": {
    "keyword_matches": 15,
    "vector_matches": 8,
    "fused_results": [
      {
        "file_path": "src/handler.c",
        "text": "code snippet...",
        "language": "c",
        "rrf_score": 0.85
      }
    ]
  }
}
```

### `thinking` — LLM 思考過程（串流）

```json
{"type": "thinking", "text": "正在分析錯誤碼..."}
```

### `content` — 分析結果（串流 Markdown）

```json
{"type": "content", "text": "## 根本原因\n..."}
```

### `done` — 分析完成

```json
{"type": "done"}
```

---

## 🚀 部署流程

### DGX Spark 部署（Host Mode）

伺服器位於 `192.168.0.134`，使用者 `avuser`。

#### SSH 連線

```python
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.0.134', username='avuser', password='PASSWORD', timeout=10)
```

> ⚠️ DGX Spark 環境無 `sshpass`、`expect`、`pexpect`，必須用 Python `paramiko`。

#### 同步檔案

```python
sftp = ssh.open_sftp()
sftp.put('local/path/backend/rca.py', '/home/avuser/bug-detective/backend/rca.py')
sftp.put('local/path/public/index.html', '/home/avuser/bug-detective/public/index.html')
# ... 其他檔案
sftp.close()
```

#### 重啟服務

```bash
# 在 DGX Spark 上執行
kill $(pgrep -f "uvicorn backend.server") 2>/dev/null
cd /home/avuser/bug-detective
nohup venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 17580 > /tmp/bug-detective.log 2>&1 &
```

> ⚠️ `nohup ... &` 必須透過 `bash -c` 包裹才能正確背景化，直接 `exec_command` 可能不會。

#### 外部存取

- **URL**：`https://bug.avision-gb10.org`（Cloudflare Tunnel）
- ⚠️ **Cloudflare 會快取 `index.html`**，部署後使用者需 `Ctrl+Shift+R` 強制刷新
- 💡 建議未來加上 cache-busting query string 解決此問題

### Docker 部署（選用）

```bash
docker-compose up -d
```

---

## 🔐 安全設計

### API Key 生命週期

```
使用者輸入 API Key
    ↓
存入瀏覽器 localStorage
    ↓
每次分析時透過 POST body 傳到後端
    ↓
後端用於呼叫 LLM API（不寫入磁碟）
    ↓
GET /api/llm-config 回傳時遮蔽為 "sk-xxxxx***"
```

### 資料遮蔽流程

```
原始程式碼 + Log
    ↓
sanitize_for_cloud()
    ↓
遮蔽 IP / Email / Token / MAC / API Key
    ↓
才傳送至雲端 LLM
```

### 需注意的安全規則

1. `data/llm-config.json` **絕不** commit（已在 `.gitignore`）
2. `.env` **絕不** commit（已在 `.gitignore`）
3. `GET /api/llm-config` 永遠不回傳完整 API Key
4. `PUT /api/llm-config` 永遠不將 API Key 寫入磁碟
5. 所有傳至雲端 LLM 的內容都必須先經過 `sanitize_for_cloud()`

---

## 🐛 除錯技巧

### 檢查後端日誌

```bash
# DGX Spark
tail -f /tmp/bug-detective.log

# 本機
# uvicorn 直接輸出到 stdout
```

### 檢查 Qdrant 狀態

```bash
curl http://localhost:6333/collections/{collection_name}
```

### 檢查 Ollama 狀態

```bash
curl http://localhost:11434/api/tags
```

### 常見問題

| 問題 | 可能原因 | 解法 |
|------|----------|------|
| Settings Modal 打不開 | JS 語法錯誤 / Cloudflare 快取 | 檢查 Console → `Ctrl+Shift+R` |
| Step 0 結果為空 | Log 格式不匹配黑名單 | 檢查 `condense_log()` 的 patterns |
| Step 2 無結果 | LLM max_tokens 不夠 / thinking tokens 耗盡 | 增加 `max_tokens`（目前 4096） |
| Step 3 無搜尋結果 | Qdrant Collection 未建置 | 確認 `ingest.py` 已執行完畢 |
| Step 4 無回應 | API Key 未設定 / Provider 錯誤 | 檢查設定面板 🔴 指示燈 |
| 外部訪問 404 | Cloudflare 快取舊版 index.html | `Ctrl+Shift+R` 強制刷新 |

---

## 📐 程式碼規範

### Python 後端

- Python 3.10+ 語法（type hints、walrus operator）
- async/await 用於所有 I/O 操作
- FastAPI + Pydantic 資料驗證
- 所有對外 LLM 呼叫必須使用 `_chat_url()` 自動補全路徑
- 所有傳至雲端的資料必須經過 `sanitize_for_cloud()`

### 前端（index.html）

- 單一 HTML 檔案，內嵌 CSS + JS
- 使用 IIFE 包裹 JS 避免全域污染
- 避免使用 inline `onclick`（用 event delegation 代替，防止嵌套引號錯誤）
- DOM 操作使用 `document.createElement()` + `appendChild()`
- 事件處理統一在 `handleSSEEvent()` switch-case 中
- CSS 變數定義在 `:root`，主題色以 `var(--xxx)` 引用

### Git

- main branch 為主要開發分支
- commit message 使用繁體中文
- 不 commit `data/llm-config.json`、`.env`、`__pycache__/`

---

## 📂 關鍵路徑速查

| 項目 | DGX Spark | 本機（WSL） |
|------|-----------|-------------|
| 專案目錄 | `/home/avuser/bug-detective` | `/mnt/d/Projects/bug-detective` |
| 原始碼 | `/home/avuser/infernoStart01` | — |
| 伺服器日誌 | `/tmp/bug-detective.log` | stdout |
| LLM 設定 | `data/llm-config.json` | 同左 |
| Qdrant | `localhost:6333` | 同左 |
| Ollama | `localhost:11434` | 同左 |
| 外部 URL | `bug.avision-gb10.org` | — |
