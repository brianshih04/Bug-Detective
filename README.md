# 🐛 Bug-Detective

AI 驅動的 Bug Root Cause Analysis（RCA）系統，為 Avision 軟體部門量身打造。  
透過 5 步驟 Pipeline 自動分析 C/C++ 原始碼日誌，從錯誤日誌中萃取關鍵資訊、語意擴充關鍵字、混合搜尋程式碼庫，最終產出完整的根本原因分析報告。

## ✨ 特色

- **5 步驟 Pipeline** — 規則式 Log 去重 → Regex 結構化萃取 → LLM 語意擴充 → Hybrid Search（Keyword + Vector RRF 融合）→ 雲端 LLM 深度分析
- **分批分析 + 統整** — Step 4 支援將搜尋結果分批（預設每批 20 檔）送 LLM 分析，最後統整產出完整 RCA 報告，避免單次 prompt 過長導致 LLM 注意力稀釋
- **RAG 搜尋** — 基於 LlamaIndex + Qdrant 向量資料庫，結合關鍵字 grep 與語意向量搜尋，Reciprocal Rank Fusion（RRF）融合排序
- **Inverted Index 關鍵字搜尋** — 啟動時預建反向索引，查詢從 O(N×K) 檔案系統掃描變為 O(1) 記憶體查表
- **多 LLM Provider** — 支援 Ollama（本地）、z.ai GLM-5、OpenRouter、MiniMax，以及任何 OpenAI 相容 API
- **串流即時回饋** — SSE 串流輸出，視覺化 Pipeline 進度條（SVG 圖示），每步驟即時顯示結果，thinking/reasoning 過程即時可見
- **安全設計** — API Key 僅存於瀏覽器記憶體，從不寫入伺服器磁碟；上傳至雲端 LLM 前自動遮蔽 IP、Email、Token 等敏感資訊
- **無障礙設計** — 語意化 HTML、ARIA 標籤、Modal Focus Trap、鍵盤快捷鍵（`Ctrl+Enter` 分析、`Ctrl+K` 搜尋）
- **繁體中文介面** — 全中文 UI，分析報告以繁體中文產出

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
│   ├── server.py      # FastAPI 伺服器、API 路由、SSE 串流端點
│   ├── rca.py         # 5 步驟 RCA Pipeline（Regex、LLM、Hybrid Search）
│   ├── config.py      # 環境設定、LLM Presets、路徑管理
│   ├── ingest.py      # 程式碼嵌入索引建置（LlamaIndex → Qdrant）
│   ├── security.py    # 敏感資訊遮蔽（IP、Token、Email 等）
│   └── __init__.py
├── public/
│   ├── index.html     # 前端 HTML 結構（語意化標記 + ARIA）
│   ├── style.css      # CSS 樣式（設計 token、元件、響應式）
│   └── app.js         # 前端邏輯（SSE 串流、搜尋、設定、快捷鍵）
├── scripts/
│   ├── build-index.py     # 程式碼索引建置（JSON grep 用）
│   ├── build-embeddings.py# ONNX 嵌入索引建置
│   └── embed-search.py    # 嵌入搜尋工具
├── data/                  # 執行期資料（.gitignore）
├── .env.example           # 環境變數範本
├── requirements.txt       # Python 依賴
├── deploy.sh              # DGX Spark 部署腳本
├── Dockerfile             # Docker 容器化
├── docker-compose.yml     # Docker Compose（含 Qdrant）
├── server.mjs             # [舊版] Express 後端（已廢棄，僅供參考）
└── .gitignore
```

## 🚀 快速開始

### 環境需求

- Python 3.10+
- Ollama（本地 LLM + Embedding）
- Qdrant（向量資料庫）

### 安裝

```bash
# 1. Clone 專案
git clone https://github.com/brianshih04/Bug-Detective.git
cd Bug-Detective

# 2. 建立 Python 虛擬環境並安裝依賴
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 設定環境變數
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

# 3. 建置向量索引
python -m backend.ingest

# 4. 啟動伺服器
python -m uvicorn backend.server:app --host 0.0.0.0 --port 17580 --reload
```

### 建置索引

```bash
# 將 C/C++ 原始碼建立向量嵌入索引
python -m backend.ingest
```

### 啟動伺服器

```bash
# 開發模式（自動重載）
python -m uvicorn backend.server:app --host 0.0.0.0 --port 17580 --reload

# 正式模式（DGX Spark 部署用）
setsid .venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 17580 > /tmp/bug-detective.log 2>&1 < /dev/null &
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
| **0** | Log 去重壓縮 | 三層過濾：黑名單過濾 → 模糊連續去重 → 全局高頻壓縮 | 零成本（規則式） |
| **1** | 結構化萃取 | Regex 提取錯誤碼、函式名稱、檔案路徑、異常訊號、記憶體位址 | 零成本（Regex） |
| **2** | 語意擴充 | LLM 將萃取結果擴充為精確關鍵字 + 語意關鍵字 | 1 次 LLM 呼叫 |
| **3** | Hybrid Search | Inverted Index 關鍵字查表 + Qdrant 向量搜尋 → RRF 融合排序 | 向量搜尋 |
| **4** | 深度分析 | 分批送 LLM 分析 → 統整產出完整 RCA 報告（thinking 串流可見） | N 批 + 1 統整（串流） |

## 🔐 安全設計

- **API Key** — 僅存於瀏覽器 JS 變數（`localStorage`），永不透過 API 回傳或寫入伺服器磁碟
- **資料遮蔽** — 傳送至雲端 LLM 前，自動遮蔽：API Key、Bearer Token、內部 IP、Email、MAC Address
- **CORS** — 限定允許 `localhost`、`127.0.0.1` 及正式域名，開發階段不開放全域
- **XSS 防護** — Markdown 渲染時對 code block 內容做 HTML escape，並封鎖 `javascript:` 協議連結
- **設定檔** — `data/llm-config.json` 已加入 `.gitignore`，避免 API Key 洩漏

## 🛠️ 開發

詳細的開發指南請參考 [DEVELOPMENT.md](./DEVELOPMENT.md)。

變更歷史請參考 [CHANGELOG.md](./CHANGELOG.md)。

## 📝 授權

內部使用專案 — Avision 軟體部門。
