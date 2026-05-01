# 📋 Bug-Detective 變更歷史

所有重要的專案變更都會記錄在此檔案中。  
格式基於 [Keep a Changelog](https://keepachangelog.com/zh-Hant/)。

---

## [2.1.0] - 2026-05-02

### 新增
- **Pipeline 視覺化進度條** — 5 個步驟 Circle 進度指示器（灰 → 紫閃爍 → 綠完成），讓使用者即時了解目前執行到哪一步
- **計時器** — 分析過程中右上角顯示已耗時（`⏱ Xs`）
- **進度狀態自動切換** — SSE `status` 事件驅動進度條狀態，每個 Step 完成時自動更新為 ✅

### 變更
- `analyzeStatus` 區塊新增 `pipeline-progress` HTML 和 `pipeline-timer`
- `handleSSEEvent()` 新增進度條驅動邏輯（從 `status` 文字和 `stepN_result` 事件推斷）
- `startAnalyze()` / `finally` 加入計時器啟停

---

## [2.0.0] - 2026-04-30

### 新增
- **5 步驟 RCA Pipeline（完整重寫）**
  - Step 0：規則式 Log 去重（黑名單過濾 → 模糊連續去重 → 全局高頻壓縮，零 LLM 成本）
  - Step 1：Regex 結構化萃取（錯誤碼、函式名稱、檔案路徑、異常訊號、記憶體位址）
  - Step 2：LLM 語意擴充（精確關鍵字 + 語意關鍵字 + 摘要）
  - Step 3：Hybrid Search（Keyword grep + Qdrant 向量搜尋 → RRF 融合排序）
  - Step 4：雲端 LLM 深度分析（SSE 串流輸出）
- **FastAPI 後端** — 從 Express（Node.js）遷移至 FastAPI（Python），支援 SSE 串流
- **LlamaIndex + Qdrant RAG** — 嵌入向量搜尋引擎（`qwen3-embedding:8b`）
- **多 LLM Provider 支援** — Ollama / z.ai GLM-5 / OpenRouter / MiniMax，附帶 Preset 一鍵切換
- **安全性模組** — `security.py`：傳送雲端前遮蔽 IP、Email、MAC、Token
- **API Key 安全機制** — 僅存瀏覽器記憶體，永不寫入伺服器磁碟
- **API Key 狀態欄** — 頂部顯示 Provider / Model / API Key 狀態（🟢🟡🔴）
- **Settings Modal** — ⚙️ 設定面板，支援 Preset、Base URL、API Key、模型選擇
- **模型抓取** — 🔍 按鈕從任何 OpenAI 相容 / Ollama 端點抓取可用模型列表
- **SSE 即時串流** — `POST /api/analyze` 回傳 SSE 串流，前端逐 token 顯示
- **可折疊搜尋結果** — 每筆搜尋結果可展開/收起，支援「展開全部/摺疊全部」
- **AI 分析分屏** — 左側思考過程面板（280px）+ 右側結果面板
- **Step 結果卡片** — 每步完成後在進度區顯示彩色結果摘要 + 可展開明細
- **Top-K 搜尋數量選擇** — 10~100 筆，偏好存 `localStorage`
- **拖放上傳** — Bug 描述與 Log 文字區支援拖放檔案
- **嵌入索引建置** — `ingest.py`：LlamaIndex SentenceSplitter → Qdrant
- **Git repo 初始化** — `git init` + push 至 GitHub

### 移除
- 舊版 Express + Node.js 後端（`server.mjs`）
- 舊版 ONNX Runtime 本地嵌入搜尋（改用 LlamaIndex + Qdrant）

### 修復
- Settings Modal 不再因點擊背景而關閉（僅 ✕ / 取消 / Esc 可關閉）
- `base_url` 自動去除 `/chat/completions`、`/v1` 等多餘後綴
- Inline onclick 嵌套引號導致的 JS 語法錯誤（改用 event delegation）

---

## [1.0.0] - 初始版本

### 新增
- Express 後端 + 靜態 HTML 前端
- 基礎 LLM Bug 分析功能
- 程式碼 grep 搜尋（JSON 索引）
- ONNX Runtime 本地嵌入搜尋
