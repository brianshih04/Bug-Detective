# 📋 Bug-Detective 變更歷史

格式基於 [Keep a Changelog](https://keepachangelog.com/zh-Hant/)。

---

## [2.5.0] - 2026-05-03

### 新增

- **🔄 預設值按鈕** — Settings Modal 最上方新增「預設值」按鈕，一鍵重設所有設定（Ollama + Qwen3.6:35b / 16000 tokens / 600s timeout / 灰色主題 / 16px 字體 / 關鍵字 50 / 溫度 0.3）
- **Phase B 清空 Thinking** — Phase A 批次分析完成後、Phase B 統整開始前，自動清空 thinking panel，避免大量舊思考內容干擾
- **Thinking Panel 即時預覽** — Phase B 的 content token 同時 append 到 thinking panel（分隔線隔開），作為即時串流預覽兼 keepalive
- **DeepSeek API Key 預設** — DeepSeek preset 自帶 API Key（via `DEEPSEEK_API_KEY` env var），點按鈕即可使用

### 變更

- **LLM 溫度上限** — 從 2.0 降為 1.0（RCA 分析場景不需要高隨機性）
- **Phase B keepalive** — 統整分析超過 15 秒無新 token 時，自動送出「🔄 統整分析中...」status event，防止 Cloudflare tunnel 斷線

---

## [2.4.0] - 2026-05-02

### 修復

- **Dockerfile 遷移至 FastAPI** — 從 `node:24-slim` + Express 改為 `python:3.12-slim` + uvicorn。消除舊 Express 後端的 3 個安全漏洞（API Key 寫碟、Shell Injection、無 CORS）
  - **原因：** Dockerfile 使用 `CMD ["node", "server.mjs"]` 啟動 Express，但 README 全文描述 FastAPI 架構。Express 存在 Critical 安全漏洞且已廢棄
  - **方法：** 重寫 Dockerfile 為 Python 基礎映像，CMD 改為 uvicorn；docker-compose 新增 Qdrant service + volume，移除 Express 相關環境變數

### 新增

- **requirements.txt** — 新建 Python 依賴清單，取代 README 中一長串 pip install 指令
- **.env.example** — 環境變數範本（空 API Key），讓新使用者可一步複製

### 變更

- **README 全面重寫** — 移除所有 Express 相關描述；新增 Docker 部署段落；安裝步驟改用 `pip install -r requirements.txt` + `.venv`
- **docker-compose.yml** — 簡化為 FastAPI + Qdrant 雙服務，SOURCE_DIR 掛載為唯讀
- **.gitignore** — `.env*` 全域規則改為 `.env` + `!.env.example` 例外，讓範本可 commit

---

## [2.3.0] - 2026-05-02

### 背景

v2.2.0 前端架構重構後，進行安全性、效能與穩定性審查，針對 Critical / High 項目修復。過程中發現 Cloudflare tunnel idle timeout 導致的連線中斷問題，以及 LLM 單次處理過多檔案導致的注意力稀釋問題，一併解決。

### 新增

- **Step 4 分批分析 + 統整** — 搜尋結果按 `batch_size`（預設 20）分批送 LLM，最後統整產出 RCA 報告
  - **原因：** 50+ 筆結果塞進單一 prompt 導致 LLM 注意力稀釋
  - **方法：** Phase A 逐批 `call_llm_stream`（thinking 轉發前端做 keepalive，content 靜默收集）；Phase B 統整全部串流
- **Pipeline 步驟 SSE 事件** — 每步完成發 `pipeline_step` 事件（step/status/message），取代字串推斷
- **Inverted Index** — 啟動時 lazy-init 掃描 C/C++ 檔建 `{word → [(file, line, text)]}` 反向索引
  - **原因：** 原本每次查詢 `subprocess.run(["grep"])` 掃描全目錄，O(N×K)
  - **方法：** O(1) 查表取得匹配行，即時讀原始檔取上下文
- **匯出 Markdown** — 分析卡片 header 一鍵下載 `.md` 報告
- **Git 版本顯示** — header 顯示 `v2.0-<hash>`（dirty flag 忽略 untracked）
- **Thinking 串流可見** — 分批分析 Phase A 的 thinking 事件轉發前端

### 變更

- **CORS 白名單** — `["*"]` → 明確列出 localhost / 127.0.0.1 / 正式域名
- **左欄寬度** — `2fr 3fr` → `1fr 3fr`（40% → 25%）
- **搜尋結果筆數** — 硬編碼 `[:15]` → `[:top_k]`
- **Singleton Retriever + 連線池** — VectorStoreIndex 按 top_k 快取，httpx.AsyncClient 共享（max 20）
- **串流顯示** — 串流期間 `<pre>` 純文字（零 DOM 開銷），`done` 一次性 renderMarkdown
  - **嘗試過的方案：** rAF throttle、增量渲染、escapeHtml placeholder — 全部失敗或有副作用
  - **最終方案：** save\_0502 分支的 renderMarkdown 版本，僅補 code block escape + `javascript:` 封鎖

### 修復

- **XSS 防護** — renderMarkdown 對 code block escapeHtml + 封鎖 `javascript:` 連結
- **Cloudflare tunnel 斷線** — config `connectTimeout/keepAliveTimeout: 600s` + thinking 串流 keepalive
- **pipeline_step 錯誤處理** — Step 2/4 except block 加入 error yield
- **git dirty flag** — 改用 `git diff --name-only HEAD` 忽略 untracked
- **移除 `verify=False`** — httpx 不再跳過 TLS 驗證

### 技術細節

- **Branch `save_0502`** — renderMarkdown 穩定版備份，`git diff save_0502..main -- public/app.js`
- **Cache-busting** — `style.css?v=5`、`app.js?v=5`，Cloudflare 部署後需遞增

---

## [2.2.0] - 2026-05-02

### 新增

- **前端架構重構** — `index.html`（1900+ 行）拆分為 `index.html` + `style.css` + `app.js`
- **Pipeline SVG 圖示** — 步驟圓圈改為 SVG（過濾器、文件、大腦、搜尋、警告三角）
- **Loading Skeleton** — shimmer 骨架屏取代空白 spinner
- **鍵盤快捷鍵** — `Ctrl+Enter` 快速分析、`Ctrl+K` 聚焦搜尋框
- **Toast 堆疊** — 最多 5 則通知，自動移除最舊
- **空狀態** — 搜尋無結果和初始頁引導文字

### 變更

- **暗色主題統一** — 移除 JS 20+ 處硬編碼顏色，全部改用 CSS 變數
- **折疊區塊** — `makeCollapsibleBlock()` 改用 CSS class，移除 inline style
- **ARIA 無障礙** — 互動元素加 `aria-label`/`role`/`aria-expanded`
- **Modal Focus Trap** — Tab 鍵循環、Escape 關閉、焦點還原
- **Focus-visible** — 鍵盤導航高亮外框
- **響應式增強** — 新增 1100px/600px 斷點

### 移除

- JS 中所有硬編碼 hex 顏色值

---

## [2.1.0] - 2026-05-02

### 新增

- **Pipeline 視覺化進度條** — 5 個步驟 Circle（灰 → 紫閃爍 → 綠完成）
- **計時器** — 分析過程右上角顯示 `⏱ Xs`
- **進度狀態切換** — SSE 事件驅動進度條狀態

---

## [2.0.0] - 2026-04-30

### 新增

- **5 步驟 RCA Pipeline（完整重寫）**
  - Step 0：規則式 Log 去重（黑名單 → 連續去重 → 高頻壓縮）
  - Step 1：Regex 結構化萃取
  - Step 2：LLM 語意擴充
  - Step 3：Hybrid Search（Keyword grep + Qdrant → RRF）
  - Step 4：雲端 LLM 深度分析（SSE 串流）
- **FastAPI 後端** — 從 Express（Node.js）遷移至 FastAPI（Python）
- **LlamaIndex + Qdrant RAG** — `qwen3-embedding:8b` 向量搜尋
- **多 LLM Provider** — Ollama / z.ai GLM-5 / OpenRouter / MiniMax / DeepSeek
- **安全性模組** — `security.py`：遮蔽 IP、Email、MAC、Token
- **API Key 安全** — 僅存瀏覽器記憶體，不寫入伺服器
- **Settings Modal** — Preset、Base URL、API Key、模型選擇
- **模型抓取** — 從任何 OpenAI 相容 / Ollama 端點抓取模型列表
- **SSE 即時串流** — `POST /api/analyze` 串流逐 token 顯示
- **可折疊搜尋結果** — 展開/收起 + 展開全部/摺疊全部
- **AI 分析分屏** — 思考過程面板 + 結果面板
- **Top-K / Max Tokens / Timeout** — 可調參數，存 localStorage
- **拖放上傳** — 支援日誌檔案拖放
- **嵌入索引建置** — `ingest.py`：LlamaIndex → Qdrant

### 移除

- 舊版 Express + Node.js 後端（`server.mjs` 保留為參考）
- 舊版 ONNX Runtime 本地嵌入搜尋

---

## [1.0.0] - 初始版本

### 新增

- Express 後端 + 靜態 HTML 前端
- 基礎 LLM Bug 分析功能
- 程式碼 grep 搜尋（JSON 索引）
- ONNX Runtime 本地嵌入搜尋
