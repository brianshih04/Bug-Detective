# 🐛 Bug-Detective

AI 驅動的 Bug Root Cause Analysis（RCA）系統，為 Avision 軟體部門量身打造。

透過 5 步驟 Pipeline 自動分析 C/C++ 原始碼日誌——從錯誤日誌中萃取關鍵資訊、語意擴充關鍵字、混合搜尋程式碼庫，最終產出完整的根本原因分析報告。

## ✨ 特色

- **5 步驟 Pipeline** — 規則式 Log 去重 → Regex 結構化萃取 → LLM 語意擴充 → Hybrid Search（Inverted Index + Qdrant 向量 RRF 融合）→ 雲端 LLM 深度分析
- **分批分析 + 統整** — Step 4 可將搜尋結果分批（預設每批 20 檔）送 LLM，最後統整產出完整 RCA 報告，避免單次 prompt 過長導致注意力稀釋
- **Inverted Index** — 啟動時掃描所有 C/C++ 檔建反向索引，關鍵字查詢 O(1) 查表
- **RAG 搜尋** — LlamaIndex + Qdrant 向量搜尋 + 關鍵字查表，Reciprocal Rank Fusion（RRF）融合排序
- **Thinking 串流** — LLM 的思考過程即時可見，同時作為 SSE keepalive 避免連線中斷
- **多 LLM Provider** — Ollama / z.ai GLM-5 / OpenRouter / MiniMax / DeepSeek / 任何 OpenAI 相容 API
- **安全設計** — API Key 僅存瀏覽器記憶體、CORS 白名單、XSS 防護、敏感資訊自動遮蔽
- **🔄 預設值重設** — Settings Modal 一鍵重設所有設定回預設值
- **繁體中文介面** — 全中文 UI + 分析報告

## 🏗️ 系統架構

```
┌───────────────────────┐     HTTP/SSE      ┌──────────────┐
│  Frontend             │ ◄──────────────► │  FastAPI      │
│  (HTML + CSS + JS)    │     Port 17580   │  Backend      │
└───────────────────────┘                   └──────┬───────┘
                                         │
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                        ┌─────────┐ ┌────────┐ ┌──────────┐
                        │ Qdrant  │ │ Ollama │ │ Cloud LLM│
                        │ Vector  │ │ Embed  │ │ (GLM-5/  │
                        │ DB      │ │ + LLM  │ │  OpenR)  │
                        └─────────┘ └────────┘ └──────────┘
```

## 📂 專案結構

```
bug-detective/
├── backend/
│   ├── server.py          # FastAPI 伺服器（265 行）
│   ├── rca.py             # 5 步驟 RCA Pipeline（1238 行）
│   ├── config.py          # 環境設定、LLM Presets
│   ├── ingest.py          # LlamaIndex → Qdrant 索引建置
│   ├── security.py        # 敏感資訊遮蔽（IP、Token、Email）
│   └── __init__.py
├── public/
│   ├── index.html          # HTML 結構（語意化 + ARIA）
│   ├── style.css           # CSS 樣式（暗色主題）
│   └── app.js              # 前端邏輯（SSE、搜尋、設定）
├── scripts/
│   ├── build-index.py      # 程式碼索引建置
│   ├── build-embeddings.py # ONNX 嵌入索引建置
│   └── embed-search.py     # 嵌入搜尋工具
├── data/                   # 執行期資料（.gitignore）
├── .env.example            # 環境變數範本
├── requirements.txt        # Python 依賴
├── deploy.sh               # DGX Spark 部署腳本
├── Dockerfile              # Docker 容器化（FastAPI）
├── docker-compose.yml      # Docker Compose（含 Qdrant）
└── .gitignore
```

## 🚀 快速開始

### 環境需求

- Python 3.10+
- Ollama（本地 LLM + Embedding）
- Qdrant（向量資料庫）

### 安裝

```bash
git clone https://github.com/brianshih04/Bug-Detective.git
cd Bug-Detective

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 編輯 .env，設定 SOURCE_DIR 為你的 C/C++ 原始碼目錄
```

### Docker 部署（推薦）

```bash
# 一鍵啟動（含 Qdrant）
docker compose up -d

# 查看 log
docker compose logs -f bug-detective
```

### 手動部署

```bash
# 1. 啟動 Ollama 並拉取模型
ollama pull qwen3-embedding:8b
ollama pull qwen3.6:35b-a3b-200k

# 2. 啟動 Qdrant
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

# 3. 建置向量索引（首次需執行，大型 codebase 可能需數小時）
python -m backend.ingest

# 4. 啟動伺服器
uvicorn backend.server:app --host 0.0.0.0 --port 17580 --reload
```

### 正式部署（DGX Spark）

```bash
setsid .venv/bin/python -m uvicorn backend.server:app \
  --host 0.0.0.0 --port 17580 \
  > /tmp/bug-detective.log 2>&1 < /dev/null &
```

開啟瀏覽器訪問 `http://localhost:17580`

## 🔧 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `SOURCE_DIR` | `/home/avuser/infernoStart01` | C/C++ 原始碼目錄 |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant 伺服器 URL |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama 伺服器 URL |
| `OLLAMA_MODEL` | `qwen3.6:35b-a3b-200k` | 預設本地 LLM 模型 |
| `GLM5_BASE_URL` | `https://api.z.ai/api/coding/paas/v4` | z.ai GLM-5 API 位址 |
| `GLM5_API_KEY` | — | z.ai API Key |
| `GLM5_MODEL` | `glm-5-turbo` | 雲端 LLM 模型 |
| `COLLECTION_NAME` | `infernoStart01` | Qdrant Collection 名稱 |
| `EMBEDDING_MODEL` | `qwen3-embedding:8b` | 嵌入模型 |
| `EMBEDDING_DIM` | `4096` | 嵌入向量維度 |
| `PORT` | `17580` | 伺服器端口 |

## 📊 5 步驟 RCA Pipeline

| Step | 名稱 | 說明 | 成本 |
|------|------|------|------|
| **0** | Log 去重壓縮 | 黑名單過濾 → 模糊連續去重 → 高頻壓縮 | 零成本（規則式） |
| **1** | 結構化萃取 | Regex 提取錯誤碼、函式名稱、檔案路徑、異常訊號、記憶體位址 | 零成本（Regex） |
| **2** | 語意擴充 | LLM 將萃取結果擴充為精確關鍵字 + 語意關鍵字 | 1 次 LLM 呼叫 |
| **3** | Hybrid Search | Inverted Index 查表 + Qdrant 向量搜尋 → RRF 融合排序 | 向量搜尋 |
| **4** | 深度分析 | 分批送 LLM 分析 → 統整產出 RCA 報告（thinking 串流可見） | N 批 + 1 統整 |

### Step 4 分批分析架構

當搜尋結果超過 `batch_size` 時啟用：

- **Phase A** — 每批獨立送 LLM 分析（`call_llm_stream`），thinking 即時轉發前端，content 靜默收集
- **Phase B** — 統整所有批次結果，產出最終 RCA 報告（全部串流輸出）

`batch_size=0` 或結果數 ≤ batch_size 時走單次分析流程。

## 🔐 安全設計

- **API Key** — 僅存於瀏覽器 JS 變數，永不寫入伺服器磁碟、永不透過 API 回傳
- **資料遮蔽** — 傳送雲端前自動遮蔽 API Key、Bearer Token、內部 IP、Email、MAC Address
- **CORS** — 白名單限定 `localhost`、`127.0.0.1` 及正式域名
- **XSS 防護** — code block 內容 HTML escape、封鎖 `javascript:` 協議連結
- **TLS** — httpx client 不跳過憑證驗證

## ⌨️ 鍵盤快捷鍵

| 快捷鍵 | 功能 |
|--------|------|
| `Ctrl+Enter` | 開始分析 |
| `Ctrl+K` | 聚焦搜尋框 |
| `Escape` | 關閉 Settings Modal |

## 🛠️ 開發

變更歷史請參考 [CHANGELOG.md](./CHANGELOG.md)。

## 📝 授權

內部使用專案 — Avision 軟體部門。
