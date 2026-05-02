# Bug-Detective 改善計畫 (Improvement Plan)

> 審查日期：2026-05-02（第三次更新）
> 審查範圍：全專案五軸審查（正確性、可讀性、架構、安全性、效能）+ README/CHANGELOG 文件一致性
> 修復狀態：Critical 2/2 ✅、High 5/7 ✅、Medium 2/13 ✅、Low 0/5

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
| 3.2 | API Key 前綴洩漏（前 8 字元） | Medium | ❌ | `server.py:163` 仍有 `api_key[:8] + "***"` |
| 3.3 | Rate Limiting | Medium | ❌ | |
| 3.4 | Security Headers | Medium | ❌ | |
| 4.1 | embedSearch spawn Python（Express） | High | 🟡 不適用 | Express 已廢棄 |
| 4.2 | load_llm_config() 每次讀磁碟 | High | ❌ | `rca.py` 3 處呼叫 |
| 4.3 | keyword_search 仍需讀磁碟取上下文 | Medium | ❌ | 反向索引存了行文字，但 context 仍即時讀檔 |
| 4.4 | embed-search.py O(n) chunk lookup | Low | ❌ | `scripts/embed-search.py:157` |
| 5.1 | 統一後端架構 | High | ✅ | v2.4.0 — Dockerfile 遷移完成 |
| 5.2 | 前端 app.js 模組化（1107 行） | Medium | ❌ | |
| 6.1 | condense_log 未使用的 api_key 參數 | Medium | ❌ | `rca.py:269` 參數未使用 |
| 6.2 | deep_analysis() 死碼 | Medium | ❌ | `rca.py:796-849` 從未被呼叫 |
| 6.3 | httpx client 無 graceful shutdown | Medium | ❌ | `rca.py:44` 模組級 client，lifespan 未關閉 |
| 6.4 | asyncio.get_event_loop() 已廢棄 | Medium | ❌ | `rca.py:621` |
| 6.5 | 重複 import（re, defaultdict） | Low | ❌ | `rca.py:8,208-209` |
| 6.6 | embed-search.py main() 重複開檔 | Low | ❌ | |
| 7.1 | 建立測試框架 | Critical | ❌ | 仍無任何測試 |
| 7.2 | CI/CD Pipeline | — | ❌ | |
| 8.1 | config.py chr(95) 混淆 | Medium | ❌ | `config.py:25-26` |
| 8.2 | rca.py 模組拆分（1238 行） | Low | ❌ | 建議下次大改時一併處理 |

---

## 剩餘項目

### 🔴 高優先

#### 7.1 [Critical] 建立測試框架

專案仍然**完全沒有測試**。優先順序：

1. **單元測試**：`backend/security.py`（sanitize 函式）、`backend/config.py`（LLM config 載入/儲存）、`renderMarkdown()` XSS 防護
2. **整合測試**：`keyword_search()`（反向索引查詢）、SSE streaming endpoint
3. **端到端測試**：完整 RCA pipeline 的 happy path

#### 4.2 [High] load_llm_config() 每次從磁碟讀取

- **檔案：** `rca.py` — 3 處呼叫（Step 2、Step 4、Phase A/Phase B）
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

### 🟡 中優先

#### 3.2 [Medium] API Key 前綴洩漏

- **檔案：** `server.py:162-163`
- **問題：** `cfg["api_key_masked"] = cfg["api_key"][:8] + "***"` 洩漏前 8 字元，足以縮小暴力破解範圍
- **方案：** 改為布林旗標 `api_key_set: true/false`

#### 3.3 [Medium] Rate Limiting

- **範圍：** `/api/analyze`、`/api/upload-log`
- **方案：** FastAPI 使用 `slowapi`

#### 3.4 [Medium] Security Headers

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    return response
```

#### 6.1 [Medium] condense_log 未使用的 api_key 參數

- **檔案：** `rca.py:269`
- **修復：** 移除 `api_key` 參數，更新呼叫端

#### 6.2 [Medium] deep_analysis() 死碼

- **檔案：** `rca.py:796-849`
- **修復：** 刪除 `deep_analysis()` 及其相關匯入（已被 `full_rca_stream` 內的 inline prompt 取代）

#### 6.3 [Medium] httpx client 無 graceful shutdown

- **檔案：** `rca.py:44-54`、`server.py:69-71`
- **修復：** 在 `lifespan` shutdown 中 `await _shared_http_client.aclose()`

#### 6.4 [Medium] asyncio.get_event_loop() 已廢棄

- **檔案：** `rca.py:621`
- **修復：** 改為 `asyncio.get_running_loop()`

#### 8.1 [Medium] config.py chr(95) 混淆

- **檔案：** `config.py:25-26`
- **修復：** `GLM5_API_KEY=_env("GLM5_API_KEY")`

#### 4.3 [Medium] keyword_search 仍需讀磁碟取上下文

- **檔案：** `rca.py:694-704`
- **方案：** 反向索引同時儲存每個檔案的完整行陣列，取上下文時直接從記憶體切片（代價：記憶體增加）

#### 5.2 [Medium] 前端 app.js 模組化

- **檔案：** `public/app.js`（1107 行）
- **建議：** 拆分為 ES Modules（SSE handler、markdown renderer、search UI、settings modal、pipeline UI）

### 🟢 低優先

#### 6.5 [Low] 重複 import

- **檔案：** `rca.py:8,208-209`
- **修復：** 刪除 line 208-209 的重複 `import re` 和 `from collections import defaultdict`

#### 6.6 [Low] embed-search.py main() 重複開檔

- **檔案：** `scripts/embed-search.py:203`
- **修復：** 改用 `load_data()` 的快取值

#### 4.4 [Low] embed-search.py O(n) chunk lookup

- **檔案：** `scripts/embed-search.py:157`
- **修復：** 啟動時建 `chunk_id → content` dict

#### 8.2 [Low] rca.py 模組拆分

- **檔案：** `backend/rca.py`（1238 行）
- **建議：** 拆分時機：下次需要大幅修改任一模組時一併處理

### 🟡 不適用（Express 已廢棄）

| # | 項目 | 說明 |
|---|------|------|
| 3.1 | 7z 路徑遍歷 | Express `server.mjs:80-101` — 已非部署目標 |
| 4.1 | embedSearch spawn Python | Express `server.mjs:193-211` — 已非部署目標 |

---

## 指標追蹤

| 指標 | v2.2.0 | v2.3.0 | v2.4.0（現在） | 目標 |
|------|--------|--------|----------------|------|
| 測試覆蓋率 | 0% | 0% | 0% | ≥ 60%（核心模組 ≥ 80%） |
| XSS 漏洞 | 4 處 | 0 處 ✅ | 0 處 | 0 處 |
| API Key 前綴洩漏 | — | — | 1 處 | 0 處 |
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

---

## 執行時程建議

| Phase | 時程 | 預估工時 | 說明 |
|-------|------|---------|------|
| 🔴 測試框架 + Rate Limit + Security Headers | Sprint 1 | 2-3 天 | 7.1 + 3.2 + 3.3 + 3.4 |
| 🟡 正確性修正（死碼、廢棄 API、shutdown） | Sprint 1 | 1 天 | 6.1~6.5 + 8.1（快速修） |
| 🟡 效能優化 | Sprint 2 | 0.5 天 | 4.2 config cache + 4.3 |
| 🟢 低優先清理 | Sprint 2 | 0.5 天 | 6.5 + 6.6 + 4.4 |
| 🟡 前端模組化 | Sprint 3 | 2-3 天 | 5.2 |
| CI/CD | Sprint 3 | 1 天 | 7.2 |
