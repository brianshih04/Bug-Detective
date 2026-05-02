"""Root Cause Analysis: 4-step pipeline.

Step 1: Regex log extraction (error codes, func names, file paths)
Step 2: LLM semantic expansion (Qwen local — synonyms, related modules)
Step 3: Hybrid search (keyword grep + Qdrant vector → RRF fusion)
Step 4: Cloud LLM deep analysis (GLM-5 / OpenRouter / MiniMax)
"""
import asyncio
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import AsyncGenerator

import httpx
from llama_index.core import VectorStoreIndex, QueryBundle
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.config import (
    QDRANT_URL, COLLECTION_NAME, EMBEDDING_MODEL, EMBEDDING_DIM,
    OLLAMA_URL, OLLAMA_MODEL, GLM5_BASE_URL, GLM5_API_KEY, GLM5_MODEL,
    SOURCE_DIR, load_llm_config,
)
from backend.security import sanitize_for_cloud


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------
def _chat_url(base_url: str) -> str:
    """Ensure base_url ends with /chat/completions."""
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


_shared_http_client: httpx.AsyncClient | None = None

def _get_shared_http_client(timeout: float = 300) -> httpx.AsyncClient:
    """Get or create a shared httpx.AsyncClient with connection pooling."""
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_http_client


async def close_shared_clients():
    """Gracefully close shared httpx client (call on shutdown)."""
    global _shared_http_client
    if _shared_http_client is not None:
        await _shared_http_client.aclose()
        _shared_http_client = None


async def call_llm_stream(
    base_url: str, api_key: str, model: str, messages: list[dict],
    temperature: float = 0.3, max_tokens: int = 4096, timeout: float = 120,
) -> AsyncGenerator[str, None]:
    """Stream LLM response via OpenAI-compatible API (SSE)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens, "stream": True,
    }

    try:
        client = _get_shared_http_client(timeout)
        async with client.stream(
            "POST", _chat_url(base_url), json=payload, headers=headers,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                yield json.dumps({"type": "error", "text": f"LLM API 回應 HTTP {resp.status_code}: {body.decode(errors='replace')[:500]}"}) + "\n"
                return
            token_count = 0
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    if token_count > 0:
                        yield json.dumps({"type": "token_usage", "completion_tokens": token_count, "max_tokens": max_tokens}) + "\n"
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    reasoning = (
                        delta.get("reasoning", "")
                        or delta.get("reasoning_content", "")
                    )
                    if reasoning:
                        yield json.dumps({"type": "thinking", "text": reasoning}) + "\n"
                        token_count += 1
                    if content:
                        yield json.dumps({"type": "content", "text": content}) + "\n"
                        token_count += 1
                except json.JSONDecodeError:
                    continue
    except httpx.TimeoutException:
        yield json.dumps({"type": "error", "text": f"LLM API 連線逾時（{timeout}s）"}) + "\n"
    except Exception as e:
        yield json.dumps({"type": "error", "text": f"LLM API 呼叫失敗：{type(e).__name__}: {str(e)[:300]}"}) + "\n"


async def call_llm_sync(
    base_url: str, api_key: str, model: str, messages: list[dict],
    temperature: float = 0.3, max_tokens: int = 4096, timeout: float = 120,
) -> tuple[str, dict]:
    """Non-streaming LLM call. Returns (response_text, usage_dict)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }

    client = _get_shared_http_client(timeout)
    resp = await client.post(_chat_url(base_url), json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    # Ollama reasoning models put thinking in reasoning_content, answer in content
    content = msg.get("content", "") or ""
    reasoning = msg.get("reasoning_content", "") or ""
    text = content.strip() or reasoning.strip() or ""
    usage = data.get("usage", {})
    return text, usage


# ---------------------------------------------------------------------------
# Index / Retriever setup
# ---------------------------------------------------------------------------
_RETRIEVER_CACHE: dict[int, object] = {}
_SHARED_QDRANT_CLIENT: "QdrantClient | None" = None

def _get_shared_qdrant_client() -> "QdrantClient":
    """Share a single QdrantClient across all retrievers."""
    global _SHARED_QDRANT_CLIENT
    if _SHARED_QDRANT_CLIENT is None:
        _SHARED_QDRANT_CLIENT = QdrantClient(url=QDRANT_URL)
    return _SHARED_QDRANT_CLIENT

def get_retriever(similarity_top_k: int = 10):
    """Get vector retriever from Qdrant (cached by top_k)."""
    if similarity_top_k in _RETRIEVER_CACHE:
        return _RETRIEVER_CACHE[similarity_top_k]
    embed_model = OllamaEmbedding(
        model_name=EMBEDDING_MODEL,
        base_url=OLLAMA_URL,
        embed_dim=EMBEDDING_DIM,
    )
    qdrant_client = _get_shared_qdrant_client()
    vector_store = QdrantVectorStore(
        client=qdrant_client, collection_name=COLLECTION_NAME,
    )
    index = VectorStoreIndex.from_vector_store(
        vector_store, embed_model=embed_model,
    )
    retriever = index.as_retriever(similarity_top_k=similarity_top_k)
    _RETRIEVER_CACHE[similarity_top_k] = retriever
    return retriever


# ---------------------------------------------------------------------------
# Inverted index for keyword_search (built once at startup)
# ---------------------------------------------------------------------------
# {lowercased_word: [(rel_path, line_no, line_text), ...]}
_KEYWORD_INDEX: dict[str, list[tuple[str, int, str]]] | None = None

_CPP_SUFFIXES = frozenset({
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hxx",
})

def _build_keyword_index(source_dir: str = SOURCE_DIR) -> dict[str, list[tuple[str, int, str]]]:
    """Scan all C/C++ files once and build an in-memory inverted index."""
    index: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    root = Path(source_dir)
    if not root.exists():
        return dict(index)

    scanned = 0
    t0 = time.time()
    for fpath in root.rglob("*"):
        if not fpath.is_file() or fpath.suffix.lower() not in _CPP_SUFFIXES:
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(fpath.relative_to(root))
        for i, line in enumerate(content.split("\n"), 1):
            # Extract alphanumeric tokens (>= 2 chars)
            for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", line):
                lw = word.lower()
                index[lw].append((rel, i, line.strip()[:200]))
        scanned += 1

    elapsed = time.time() - t0
    print(f"[keyword-index] Built index: {scanned} files, "
          f"{len(index)} unique tokens, {elapsed:.1f}s")
    return dict(index)


def get_keyword_index() -> dict[str, list[tuple[str, int, str]]]:
    """Lazy-init and return the global keyword index."""
    global _KEYWORD_INDEX
    if _KEYWORD_INDEX is None:
        _KEYWORD_INDEX = _build_keyword_index()
    return _KEYWORD_INDEX


# =========================================================================
# STEP 0: Rule-based Log Filtering & Deduplication
# =========================================================================

# ── Blacklist: known noisy patterns to drop entirely (unless near errors) ──
_NOISE_PATTERNS = [
    # 定著器/加熱器 毫秒級溫度回報 (heartbeat)
    re.compile(r'FUSER_FUNC_ShowHeatingInfo'),
    # 背景常態輪詢 (FakeAppProc recheck)
    re.compile(r'_PrnJobMgr_FakeAppProc'),
    # 溫濕度感測器重複讀取
    re.compile(r'TempertureHumidity'),
    re.compile(r'CalculateHumidity'),
    # SNMP 網管通訊
    re.compile(r'\{SNMP\}'),
    re.compile(r'SNMPSysReadInfo'),
    re.compile(r'SNMP_TRAP'),
]

# ── Error detection: lines matching these are NEVER dropped ──
_KEEP_KEYWORDS = re.compile(
    r'(error|exception|crash|fault|fail|panic|abort|segfault|timeout|'
    r'warning|warn|fatal|assert|broken|corrupt|overflow|undefined|'
    r'ERR_|E-|0x[0-9a-fA-F]{4,})',
    re.IGNORECASE,
)

# ── Fingerprinting: normalize variable parts for pattern matching ──
#    Each rule replaces a type of variable data with a fixed placeholder,
#    so lines that differ only in numbers/timestamps get the same fingerprint.
_FP_RULES = [
    (re.compile(r'\(\d+ms\)'),                      '(Xms)'),       # (5169610ms)
    (re.compile(r'\([\d, ]+\)'),                    '(N)'),          # ( 358, 108, ... )
    (re.compile(r'T:\s*\d+'),                       'T:N'),          # T: 12345
    (re.compile(r'msT\(\d+\)'),                    'msT(N)'),       # msT(12345)
    (re.compile(r'data\d?:0x[0-9a-fA-F]+'),        'dataN:0xN'),    # data1:0x64
    (re.compile(r'getData:\d+'),                    'getData:N'),    # getData:123
    (re.compile(r'\b\d{4,}\b'),                     'N'),            # standalone numbers ≥4 digits
    (re.compile(r'0x[0-9a-fA-F]{2,}'),              '0xN'),          # hex values
    (re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}[\s_]\d{2}:\d{2}:\d{2}'),  # dates
     'DATE'),
]


def _fingerprint(line: str) -> str:
    """Normalize variable parts (timestamps, numbers, hex) for fuzzy comparison."""
    s = line
    for pat, repl in _FP_RULES:
        s = pat.sub(repl, s)
    return s


def _is_noise(line: str) -> bool:
    """Check if a line matches a known noise pattern."""
    return any(p.search(line) for p in _NOISE_PATTERNS)


def _is_important(line: str) -> bool:
    """Check if a line contains error/warning keywords (never drop these)."""
    return bool(_KEEP_KEYWORDS.search(line))


def condense_log(log_text: str, bug_desc: str = "") -> str:
    """Step 0: Programmatic log condensation — two-layer approach.

    Layer 1 — Blacklist filter:
        Drop known high-frequency noise patterns entirely
        (FUSER heartbeat, FakeAppProc, temp/humidity polling, SNMP).
    Layer 2 — Fingerprint sampling:
        For remaining high-frequency patterns, keep:
          • First occurrence (with count annotation)
          • Evenly-spaced samples across the full timeline
          • Last occurrence (with end annotation)
          • Any line within ±5 lines of an error/warning
        This preserves the temporal progression while drastically reducing volume.

    Error/warning lines and their ±5 line context are ALWAYS protected.
    """
    if not log_text or len(log_text) < 500:
        return log_text

    lines = log_text.split('\n')
    total = len(lines)

    # ── Mark protected lines (error context ±5) ──
    PROTECT_RANGE = 5
    protected = set()
    for i, line in enumerate(lines):
        if _is_important(line):
            for d in range(-PROTECT_RANGE, PROTECT_RANGE + 1):
                j = i + d
                if 0 <= j < total:
                    protected.add(j)

    # ── Layer 1: Blacklist filter (unless protected) ──
    after_filter = []       # [(orig_line_index, line_text), ...]
    dropped_by_pattern = Counter()

    for i, line in enumerate(lines):
        if _is_noise(line) and i not in protected:
            for p in _NOISE_PATTERNS:
                if p.search(line):
                    dropped_by_pattern[p.pattern] += 1
                    break
            continue  # drop noise
        after_filter.append((i, line))

    # ── Layer 2: Fingerprint sampling ──
    n_filtered = len(after_filter)
    fingerprints = [_fingerprint(line) for _, line in after_filter]

    # Count fingerprint frequencies
    fp_count = Counter()
    fp_groups = defaultdict(list)  # fp → list of indices in after_filter
    for idx, (_, line) in enumerate(after_filter):
        fp = fingerprints[idx].strip()
        if fp:
            fp_count[fp] += 1
            fp_groups[fp].append(idx)

    # Mark error-context lines in after_filter coordinates
    filtered_protected = set()
    for idx, (orig_i, _) in enumerate(after_filter):
        if orig_i in protected:
            filtered_protected.add(idx)

    # Select which indices to keep
    FREQ_THRESHOLD = 5     # Min occurrences to trigger sampling
    MAX_SAMPLES = 12       # Max evenly-spaced samples per pattern (plus first+last)

    keep = set()

    for fp, indices in fp_groups.items():
        count = fp_count[fp]
        if count <= FREQ_THRESHOLD:
            # Low frequency: keep all
            keep.update(indices)
            continue

        # High frequency: always keep all protected lines
        prot_in_group = {i for i in indices if i in filtered_protected}
        keep.update(prot_in_group)

        # From non-protected: keep first + evenly-spaced samples + last
        free = sorted(i for i in indices if i not in filtered_protected)
        if not free:
            continue

        selected = {free[0], free[-1]}
        if len(free) > 2:
            n = min(MAX_SAMPLES, len(free) - 2)
            for k in range(1, n + 1):
                pos = int(k * (len(free) - 1) / (n + 1))
                if 0 < pos < len(free) - 1:
                    selected.add(free[pos])
        keep.update(selected)

    # Keep empty/whitespace lines
    for idx, (_, line) in enumerate(after_filter):
        if not line.strip():
            keep.add(idx)

    # Track first/last non-protected sample for annotations
    first_sample = {}
    last_sample = {}
    for fp in fp_groups:
        if fp_count[fp] <= FREQ_THRESHOLD:
            continue
        free_kept = sorted(
            i for i in fp_groups[fp] if i in keep and i not in filtered_protected
        )
        if free_kept:
            first_sample[fp] = free_kept[0]
            last_sample[fp] = free_kept[-1]

    # Build output
    output = []
    for idx, (_, line) in enumerate(after_filter):
        if idx not in keep:
            continue
        fp = fingerprints[idx].strip()
        annotation = ""
        if fp and fp in first_sample and idx == first_sample[fp]:
            annotation = f"  ← 此模式共 {fp_count[fp]} 次（保留代表性取樣）"
        elif fp and fp in last_sample and idx == last_sample[fp]:
            annotation = f"  ← 此模式到此結束"
        output.append(line + annotation)

    # Build summary header
    n_compressed = sum(1 for c in fp_count.values() if c > FREQ_THRESHOLD)
    reduction = len(output) / max(total, 1) * 100
    noise_lines = sum(dropped_by_pattern.values())

    summary = [
        '--- Log 去重結果 ---',
        f'原始: {total:,} 行 → 去重後: {len(output):,} 行 ({reduction:.0f}%)',
    ]
    if noise_lines:
        summary.append(f'黑名單過濾: {noise_lines:,} 行雜訊已刪除')
        for pat, cnt in dropped_by_pattern.most_common(5):
            name = pat.replace('\\', '').replace('(', '').replace(')', '')[:50]
            summary.append(f'  - {name}: {cnt:,} 行')
    if n_compressed:
        summary.append(
            f'指紋取樣: {n_compressed} 種高頻模式 '
            f'(頻率 >{FREQ_THRESHOLD}，每種最多 {MAX_SAMPLES + 2} 筆取樣)'
        )
    summary += ['--- 以下為去重後 Log ---', '']

    return '\n'.join(summary + output)


# =========================================================================
# STEP 1: Regex Log Extraction
# =========================================================================
# Regex patterns for structured extraction from C/C++ logs
_LOG_PATTERNS = {
    "error_codes": [
        # E-0012, ERR_SOMETHING, WARNING_42
        r'\b(?:E|ERR|ERROR|WARN|WARNING|FATAL|BUG)[-_]?\w{1,30}\b',
        # 0xERR hex error codes
        r'\b0x[0-9A-Fa-f]{4,16}\b',
        # HTTP-like codes
        r'\b(?:status|code|errno)\s*[=:]\s*(\d{3,5})\b',
    ],
    "function_names": [
        # C function calls: funcName(
        r'\b[a-zA-Z_]\w{1,60}\s*\(',
        # C++ method: Class::method(
        r'\b[a-zA-Z_]\w{1,40}::{1,3}[a-zA-Z_]\w{1,60}\s*\(',
        # #define FUNC macros
        r'#\s*define\s+([A-Z_]\w{1,40})',
    ],
    "file_paths": [
        # /path/to/file.c:123
        r'[/\w\-_.]+\.(?:c|cpp|h|hpp|cc|cxx|hxx|hh|ipp)[:]\d+',
        # "file.c", line 42
        r'["\x27]?[\w\-_./]+\.(?:c|cpp|h|hpp|cc|cxx|hxx|hh|ipp)["\x27]?',
    ],
    "exceptions": [
        # C++ exceptions
        r'\b(?:exception|throw|catch|Segfault|SIGSEGV|SIGABRT|SIGBUS|SIGFPE)\b',
        r'\b(?:null\s*pointer|nullptr|dereference|out\s*of\s*bounds|buffer\s*overflow|stack\s*overflow|memory\s*leak|double\s*free|use\s*after\s*free)\b',
        # Assertion failures
        r'\bassert(?:ion)?\s*(?:failed|failure)?\b',
    ],
    "memory_addresses": [
        r'\b0x[0-9A-Fa-f]{8,16}\b',
    ],
}


def extract_structured_log(log_text: str) -> dict:
    """Step 1: Use regex to extract structured info from log text.

    Returns dict with keys: error_codes, function_names, file_paths,
    exceptions, memory_addresses, raw_lines (relevant log lines).
    """
    result = {key: [] for key in _LOG_PATTERNS}
    seen = {key: set() for key in _LOG_PATTERNS}

    for category, patterns in _LOG_PATTERNS.items():
        for pattern in patterns:
            for m in re.finditer(pattern, log_text, re.IGNORECASE):
                match_text = m.group(0).strip()
                # Clean up function names — remove trailing (
                if category == "function_names":
                    match_text = match_text.rstrip("(").strip()
                if match_text and match_text not in seen[category]:
                    seen[category].add(match_text)
                    result[category].append(match_text)

    # Also extract lines that look like error/warning/fatal messages
    error_lines = []
    for line in log_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.search(
            r'\b(error|err|fail|fatal|crash|exception|segfault|abort|panic|warning|warn)\b',
            line, re.IGNORECASE,
        ):
            error_lines.append(line[:300])  # truncate long lines

    result["error_lines"] = error_lines[:50]  # cap at 50 lines

    return result


# =========================================================================
# STEP 2: LLM Semantic Expansion (Qwen local)
# =========================================================================
async def llm_expand_keywords(
    structured: dict, bug_desc: str, api_key: str = "",
    max_tokens: int = 0, timeout: int = 0,
) -> dict:
    """Step 2: Use LLM to expand keywords with semantic understanding.

    Uses the configured cloud LLM (or local Ollama as fallback).
    Takes structured extraction from Step 1, asks LLM to produce:
      - exact: precise search terms (from log, high confidence)
      - semantic: related terms, synonyms, module names (for vector search)
      - summary: concise problem description
    """
    # Prefer cloud LLM config; fall back to Ollama
    llm_cfg = load_llm_config()
    if llm_cfg.get("provider") == "ollama":
        # Ollama reasoning model returns empty content — use shorter prompt
        base_url = OLLAMA_URL + "/v1"
        key = ""
        model = OLLAMA_MODEL
    else:
        base_url = llm_cfg["base_url"]
        key = api_key or llm_cfg.get("api_key", "")
        model = llm_cfg["model"]

    # Resolve from params or llm-config
    _max_tok = max_tokens if max_tokens > 0 else llm_cfg.get("max_tokens", 4096)
    _timeout = timeout if timeout > 0 else llm_cfg.get("timeout", 300)

    # Build context from Step 1 results (truncate to avoid overwhelming LLM)
    parts = []
    if structured["error_codes"]:
        parts.append(f"Error codes: {', '.join(structured['error_codes'][:20])}")
    if structured["function_names"]:
        parts.append(f"Functions: {', '.join(structured['function_names'][:20])}")
    if structured["file_paths"]:
        parts.append(f"Files: {', '.join(structured['file_paths'][:15])}")
    if structured["exceptions"]:
        parts.append(f"Exceptions: {', '.join(structured['exceptions'][:15])}")
    if structured["error_lines"]:
        parts.append(f"Error lines:\n" + "\n".join(structured["error_lines"][:15]))

    extracted_context = "\n".join(parts)

    prompt = f"""你是一個 C/C++ 嵌入式系統（MFP 印表機）的 Bug 分析助手。
請根據以下的 Bug 描述和從 Log 中萃取出的結構化資訊，產生搜尋關鍵字。

## Bug 描述
{bug_desc}

## Log 結構化萃取結果
{extracted_context}

## 任務
請產生用來搜尋原始碼的關鍵字，分為兩類：

1. **exact**：精確匹配的關鍵字（error code、function name、變數名稱、macro 名稱），
   這些關鍵字必須能在程式碼中直接找到。最多 15 個。
2. **semantic**：語意相關的關鍵字（相關模組名稱、同義詞、功能描述詞），
   用來做語意搜尋，找到字面上不同但概念相關的程式碼。最多 10 個。
3. **summary**：用一句話（50 字以內）描述這個 Bug 的核心問題。

請「只」回覆 JSON，不要加任何其他文字或 markdown code block：
{{"exact": ["keyword1", "keyword2"], "semantic": ["related1", "related2"], "summary": "核心問題描述"}}"""

    messages = [{"role": "user", "content": prompt}]

    try:
        resp, usage = await call_llm_sync(
            base_url, key, model,
            messages, temperature=0.1, max_tokens=_max_tok, timeout=_timeout,
        )
        # Handle reasoning models that put answer in reasoning field
        if not resp.strip():
            resp = "[]"
        # Parse JSON from response
        resp = resp.strip()
        if resp.startswith("```"):
            resp = re.sub(r'^```\w*\n?', '', resp)
            resp = re.sub(r'\n?```$', '', resp)
        # Try to extract JSON object — handle truncated JSON
        json_match = re.search(r'\{.*\}', resp, re.DOTALL)
        if json_match:
            resp = json_match.group(0)
            # Fix truncated JSON: close open brackets
            open_brackets = resp.count('[') - resp.count(']')
            open_braces = resp.count('{') - resp.count('}')
            if open_brackets > 0 or open_braces > 0:
                resp = resp.rstrip().rstrip(',') + ']' * max(0, open_brackets) + '}' * max(0, open_braces)
        expanded = json.loads(resp)
        return {
            "summary": expanded.get("summary", bug_desc[:100]),
            "exact": expanded.get("exact", []),
            "semantic": expanded.get("semantic", []),
            "structured": structured,
            "usage": usage or {},
        }
    except Exception as e:
        # Fallback: use regex results directly
        exact = (
            structured.get("error_codes", [])
            + structured.get("function_names", [])[:10]
            + structured.get("exceptions", [])[:5]
        )
        semantic = structured.get("file_paths", [])[:5]
        error_msg = f"Step 2 LLM 擴充失敗: {e}"
        return {
            "summary": bug_desc[:100],
            "exact": exact,
            "semantic": semantic,
            "structured": structured,
            "error": error_msg,
        }


# =========================================================================
# STEP 3: Hybrid Search (Keyword grep + Qdrant vector → RRF fusion)
# =========================================================================
async def vector_search(query: str, top_k: int = 15) -> list[dict]:
    """Search code using Qdrant vector similarity."""
    try:
        retriever = get_retriever(similarity_top_k=top_k)
        query_bundle = QueryBundle(query_str=query)
        loop = asyncio.get_running_loop()
        nodes = await asyncio.wait_for(
            loop.run_in_executor(None, retriever.retrieve, query_bundle),
            timeout=30,
        )

        results = []
        for node in nodes:
            results.append({
                "file_path": node.metadata.get("file_path", "unknown"),
                "file_name": node.metadata.get("file_name", "unknown"),
                "language": node.metadata.get("language", "unknown"),
                "start_line": node.metadata.get("start_line", 0),
                "end_line": node.metadata.get("end_line", 0),
                "text": node.text[:800],
                "score": node.score if hasattr(node, "score") else None,
                "source": "vector",
            })
        return results
    except Exception as e:
        return [{"source": "vector", "error": str(e)}]


async def keyword_search(
    keywords: list[str], source_dir: str = SOURCE_DIR, max_results: int = 15,
) -> list[dict]:
    """Search source code using inverted index (built once at startup)."""
    results = []
    seen_files = {}  # file_path → best rank
    _file_cache = {}  # rel_path → file content (per-call cache)

    root = Path(source_dir)
    if not root.exists():
        return []

    kw_index = get_keyword_index()

    for kw in keywords[:10]:  # limit to 10 keywords
        try:
            # Tokenize keyword and look up each token in the index
            kw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", kw)
            if not kw_tokens:
                continue

            # Gather candidate matches from index lookup
            # Each token returns a list of (rel_path, line_no, line_text)
            candidate_sets: list[list[tuple[str, int, str]]] = []
            for token in kw_tokens:
                hits = kw_index.get(token.lower(), [])
                if hits:
                    candidate_sets.append(hits)

            if not candidate_sets:
                continue

            # Intersect: a match is valid only if ALL tokens appear on the same line
            # Start with the smallest set for efficiency
            candidate_sets.sort(key=len)
            base_set = candidate_sets[0]
            for extra_set in candidate_sets[1:]:
                # Build lookup: {(rel_path, line_no)} for fast intersection
                extra_lookup = {(r, ln) for r, ln, _ in extra_set}
                base_set = [
                    (r, ln, lt) for r, ln, lt in base_set
                    if (r, ln) in extra_lookup
                ]

            # Verify full keyword regex on matched lines (catch partial / case mismatches)
            kw_safe = re.escape(kw)
            matches = []
            for rel_path, line_no, line_text in base_set:
                if re.search(kw_safe, line_text, re.IGNORECASE):
                    matches.append((rel_path, line_no, line_text))
                    if len(matches) >= 5:
                        break

            # Read surrounding context from disk (same as before)
            for rel_path, line_no, line_text in matches:
                if rel_path not in seen_files or seen_files[rel_path] > len(results):
                    seen_files[rel_path] = len(results)
                    fpath = root / rel_path
                    try:
                        if rel_path not in _file_cache:
                            _file_cache[rel_path] = fpath.read_text(encoding="utf-8", errors="replace")
                        lines = _file_cache[rel_path].split("\n")
                        start = max(0, line_no - 5)
                        end = min(len(lines), line_no + 10)
                        snippet = "\n".join(lines[start:end])[:800]
                    except Exception:
                        snippet = line_text

                    results.append({
                        "file_path": rel_path,
                        "file_name": Path(rel_path).name,
                        "language": "c" if rel_path.endswith((".c", ".h")) else "cpp",
                        "start_line": start + 1,
                        "end_line": end,
                        "text": snippet,
                        "score": 1.0,  # exact match
                        "source": "keyword",
                        "matched_keyword": kw,
                    })
                    if len(results) >= max_results:
                        break
        except Exception:
            continue
        if len(results) >= max_results:
            break

    return results


def rrf_fuse(
    vector_results: list[dict],
    keyword_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion: merge vector + keyword search results."""
    scores = defaultdict(float)
    meta = {}  # file_path → result dict

    for rank, r in enumerate(vector_results):
        key = r.get("file_path", f"vec_{rank}")
        scores[key] += 1.0 / (k + rank + 1)
        if key not in meta or r.get("score"):
            meta[key] = r

    for rank, r in enumerate(keyword_results):
        key = r.get("file_path", f"kw_{rank}")
        scores[key] += 1.0 / (k + rank + 1)
        # Prefer keyword results (they have context snippets)
        if key not in meta or r.get("matched_keyword"):
            meta[key] = r
        # Merge source info
        if "matched_keyword" in r:
            meta[key]["matched_keyword"] = r.get("matched_keyword", "")

    # Sort by RRF score descending
    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    fused = []
    for key in sorted_keys:
        item = dict(meta[key])
        item["rrf_score"] = round(scores[key], 6)
        fused.append(item)

    return fused


async def hybrid_search(
    exact_keywords: list[str],
    semantic_keywords: list[str],
    summary: str,
    top_k: int = 15,
) -> list[dict]:
    """Step 3: Hybrid search — keyword grep + vector search, fused with RRF."""
    # Run both searches in parallel
    kw_task = keyword_search(exact_keywords, max_results=top_k)

    # Build vector query from semantic keywords + summary
    vector_query_parts = semantic_keywords + [summary]
    vector_query = " ".join(vector_query_parts[:10])
    vec_task = vector_search(vector_query, top_k=top_k)

    kw_results, vec_results = await asyncio.gather(kw_task, vec_task)

    # Handle errors
    if kw_results and isinstance(kw_results[0], dict) and "error" in kw_results[0]:
        kw_results = []
    if vec_results and isinstance(vec_results[0], dict) and "error" in vec_results[0]:
        vec_results = []

    # Fuse results
    fused = rrf_fuse(vec_results, kw_results)

    return fused[:top_k], len(kw_results), len(vec_results)


# =========================================================================
# STEP 4: Deep Analysis (Cloud LLM — streaming)
# =========================================================================
# =========================================================================

# Full Pipeline (streaming)
# =========================================================================
def _step_event(step: int, state: str, elapsed: float = None, detail: str = "",
                max_tokens: int = None, timeout: int = None):
    """Emit an explicit pipeline_step SSE event for the frontend."""
    evt = {"type": "pipeline_step", "step": step, "state": state}
    if elapsed is not None:
        evt["elapsed"] = round(elapsed, 1)
    if detail:
        evt["detail"] = detail
    if max_tokens is not None:
        evt["max_tokens"] = max_tokens
    if timeout is not None:
        evt["timeout"] = timeout
    return json.dumps(evt) + "\n"


async def full_rca_stream(
    log_text: str, bug_desc: str, api_key: str = "", top_k: int = 15,
    batch_size: int = 20, max_tokens: int = 0, timeout: int = 0,
) -> AsyncGenerator[str, None]:
    """Full 5-step RCA pipeline with streaming output."""
    t0 = time.time()
    step_start = t0

    # Resolve max_tokens / timeout from llm-config if not explicitly provided
    llm_cfg = load_llm_config()
    _max_tokens = max_tokens if max_tokens > 0 else llm_cfg.get("max_tokens", 4096)
    _timeout = timeout if timeout > 0 else llm_cfg.get("timeout", 300)

    # ── Step 0: Rule-based log deduplication ──────────────────────
    yield _step_event(0, "active", elapsed=0)
    yield json.dumps({
        "type": "status",
        "text": f"📝 Step 0/5: Log 去重壓縮（{len(log_text):,} chars）...",
    }) + "\n"

    condensed_log = condense_log(log_text, bug_desc)
    now = time.time()
    original_lines = len(log_text.split('\n'))
    condensed_lines = len(condensed_log.split('\n'))
    reduction = condensed_lines / max(original_lines, 1) * 100

    yield _step_event(0, "done", elapsed=now - step_start)
    yield json.dumps({
        "type": "step0_result",
        "data": {
            "original_lines": original_lines,
            "condensed_lines": condensed_lines,
            "reduction_pct": round(100 - reduction, 1),
            "condensed_log": condensed_log[:50000],
        },
    }) + "\n"
    yield json.dumps({
        "type": "status",
        "text": (
            f"  ✅ Log 精簡完成：{len(log_text):,} → {len(condensed_log):,} chars"
            f"（縮減 {100 - reduction:.1f}%）"
        ),
    }) + "\n"

    # Use condensed log for all subsequent steps
    log_text = condensed_log
    step_start = time.time()

    # ── Step 1: Regex extraction ──────────────────────────────────
    yield _step_event(1, "active", elapsed=time.time() - t0)
    yield json.dumps({
        "type": "status",
        "text": "🔧 Step 1/5: 從精簡 Log 中萃取結構化資訊（Error codes, Functions, Files）...",
    }) + "\n"

    structured = extract_structured_log(log_text)

    now = time.time()
    total_extracted = sum(len(v) for v in structured.values() if isinstance(v, list))
    yield _step_event(1, "done", elapsed=now - step_start)
    yield json.dumps({
        "type": "step1_result",
        "data": {
            "error_codes": structured["error_codes"],
            "function_names": structured["function_names"],
            "file_paths": structured["file_paths"],
            "exceptions": structured["exceptions"],
            "memory_addresses": structured.get("memory_addresses", []),
            "error_lines": structured["error_lines"][:200],
            "error_lines_count": len(structured["error_lines"]),
        },
    }) + "\n"
    yield json.dumps({
        "type": "status",
        "text": f"  ✅ 萃取到 {total_extracted} 個結構化元素",
    }) + "\n"
    step_start = time.time()

    # ── Step 2: LLM semantic expansion ───────────────────────────
    yield _step_event(2, "active", elapsed=time.time() - t0)
    yield json.dumps({
        "type": "status",
        "text": "🧠 Step 2/5: 語意擴充（同義詞、相關模組）...",
    }) + "\n"

    expanded = await llm_expand_keywords(structured, bug_desc, api_key, max_tokens=_max_tokens, timeout=_timeout)

    # Emit token usage for Step 2
    step2_usage = expanded.get("usage", {})
    if step2_usage:
        yield json.dumps({"type": "token_usage", "step": 2, **step2_usage, "max_tokens": _max_tokens}) + "\n"

    now = time.time()
    yield _step_event(2, "done", elapsed=now - step_start,
                      detail=f"{len(expanded['exact'])} exact, {len(expanded['semantic'])} semantic")
    yield json.dumps({
        "type": "step2_result",
        "data": {
            "summary": expanded["summary"],
            "exact": expanded["exact"],
            "semantic": expanded["semantic"],
        },
    }) + "\n"
    yield json.dumps({
        "type": "status",
        "text": (
            f"  ✅ 精確關鍵字 {len(expanded['exact'])} 個，"
            f"語意關鍵字 {len(expanded['semantic'])} 個"
        ),
    }) + "\n"
    step_start = time.time()

    # ── Step 3: Hybrid search ────────────────────────────────────
    yield _step_event(3, "active", elapsed=time.time() - t0)
    yield json.dumps({
        "type": "status",
        "text": "🔎 Step 3/5: Hybrid Search（Keyword grep + Vector 語意搜尋 → RRF 融合）...",
    }) + "\n"

    fused_results, kw_count, vec_count = await hybrid_search(
        exact_keywords=expanded["exact"],
        semantic_keywords=expanded["semantic"],
        summary=expanded["summary"],
        top_k=top_k,
    )

    now = time.time()
    yield _step_event(3, "done", elapsed=now - step_start,
                      detail=f"KW:{kw_count} Vec:{vec_count} RRF:{len(fused_results)}")
    yield json.dumps({
        "type": "step3_result",
        "data": {
            "keyword_matches": kw_count,
            "vector_matches": vec_count,
            "fused_results": fused_results[:top_k],
        },
    }) + "\n"
    yield json.dumps({
        "type": "status",
        "text": (
            f"  ✅ Keyword: {kw_count} 筆 / Vector: {vec_count} 筆 / "
            f"RRF 融合: {len(fused_results)} 筆"
        ),
    }) + "\n"
    step_start = time.time()

    # ── Step 4: Cloud LLM deep analysis ──────────────────────────
    yield _step_event(4, "active", elapsed=time.time() - t0)
    llm_cfg = load_llm_config()

    results_to_analyze = fused_results[:top_k]
    total_results = len(results_to_analyze)

    # Build context parts once
    def _build_context(batch: list[dict]) -> str:
        parts = []
        for i, r in enumerate(batch, 1):
            parts.append(
                f"### Result {i}: {r['file_path']} (RRF: {r.get('rrf_score', 'N/A')})\n"
                f"```{r.get('language', 'c')}\n{r['text']}\n```"
            )
        return "\n\n".join(parts)

    safe_summary = sanitize_for_cloud(expanded["summary"])
    safe_desc = sanitize_for_cloud(bug_desc)
    safe_exact = json.dumps(expanded['exact'], ensure_ascii=False)
    safe_semantic = json.dumps(expanded['semantic'], ensure_ascii=False)

    _batch_analysis_prompt = """你是 Avision 軟體部門的資深 RCA 工程師。

## Bug 描述
{desc}

## 問題摘要
{summary}

## Regex 萃取的精確關鍵字
{exact}

## 語意擴充關鍵字
{semantic}

## 相關程式碼片段（第 {batch_idx}/{total_batches} 批，RRF 融合排序）
{context}

## 任務
請針對以上程式碼片段分析，找出：
1. **可能的根本原因**：解釋這段程式碼與 Bug 的關聯
2. **受影響檔案與行號**：列出需要修改的具體位置
3. **修復建議**：提供具體的程式碼修改建議

用繁體中文回覆，程式碼用 markdown code block。保持簡潔聚焦，不要重述 Bug 描述。"""

    _synthesis_prompt = """你是 Avision 軟體部門的資深 RCA 工程師，負責統整多批程式碼分析結果。

## Bug 描述
{desc}

## 問題摘要
{summary}

## Regex 萃取的精確關鍵字
{exact}

## 語意擴充關鍵字
{semantic}

## 分批分析結果（共 {total_batches} 批）
{batch_results}

## 任務
請統整以上所有批次的分析結果，產出完整的 Root Cause Analysis 報告：
1. **根本原因**：綜合所有證據，說明為什麼會出現這個 Bug
2. **受影響檔案與行號**：列出所有需要修改的具體位置（去重合併）
3. **修復建議**：提供具體的程式碼修改建議（按優先級排序）
4. **驗證方法**：如何確認修復有效

用繁體中文回覆，程式碼用 markdown code block。如果不同批次有矛盾的分析，以 RRF 分數較高的批次為準。"""

    try:
        if batch_size > 0 and total_results > batch_size:
            # ── Phase A: Batch analysis (sync, no streaming to frontend) ──
            # NOTE: call_llm_sync may take >100s per batch; Cloudflare tunnel
            # drops idle SSE connections after ~100s, so we wrap each call with
            # periodic heartbeat comments to keep the connection alive.
            import math
            total_batches = math.ceil(total_results / batch_size)
            batch_reports = []
            _sync_timeout = min(_timeout, 300)  # per-batch timeout cap at 5 min

            async def _batch_stream(prompt_str, label):
                """Stream batch analysis: forward thinking to frontend, collect content silently."""
                batch_chunks = []
                async for chunk in call_llm_stream(
                    llm_cfg["base_url"],
                    api_key or llm_cfg.get("api_key", ""),
                    llm_cfg["model"],
                    [{"role": "user", "content": prompt_str}],
                    temperature=0.3,
                    max_tokens=min(_max_tokens, 4096),
                    timeout=_sync_timeout,
                ):
                    if chunk.startswith("{"):
                        try:
                            evt = json.loads(chunk)
                            if evt.get("type") == "thinking":
                                yield chunk  # forward thinking to frontend
                            elif evt.get("type") == "content":
                                batch_chunks.append(evt.get("text", ""))
                            elif evt.get("type") == "token_usage":
                                yield chunk  # forward token usage
                        except json.JSONDecodeError:
                            pass
                # Yield collected content as internal event
                yield json.dumps({"type": "_batch_result", "text": "".join(batch_chunks)}) + "\n"

            for b_idx in range(total_batches):
                start = b_idx * batch_size
                batch = results_to_analyze[start:start + batch_size]
                context = sanitize_for_cloud(_build_context(batch))

                yield json.dumps({
                    "type": "status",
                    "text": f"🧩 Step 4: 分析第 {b_idx + 1}/{total_batches} 批（{len(batch)} 個檔案）...",
                }) + "\n"

                prompt = _batch_analysis_prompt.format(
                    desc=safe_desc, summary=safe_summary,
                    exact=safe_exact, semantic=safe_semantic,
                    context=context,
                    batch_idx=b_idx + 1, total_batches=total_batches,
                )

                # Stream batch: thinking goes to frontend, content collected silently
                batch_text = ""
                async for chunk in _batch_stream(prompt, f"batch {b_idx+1}/{total_batches}"):
                    if chunk.startswith("{") and '"_batch_result"' in chunk:
                        batch_text = json.loads(chunk)["text"]
                    else:
                        yield chunk  # forward thinking/token_usage to frontend
                batch_reports.append(
                    f"### 第 {b_idx + 1} 批（{len(batch)} 個檔案，RRF #{start + 1}~#{start + len(batch)}）\n\n{batch_text}"
                )

            # ── Phase B: Synthesis (stream to frontend) ──
            all_batch_text = "\n\n---\n\n".join(batch_reports)
            synthesis_context = sanitize_for_cloud(all_batch_text)

            yield json.dumps({
                "type": "status",
                "text": f"🔄 Step 4: 統整 {total_batches} 批分析結果...",
            }) + "\n"

            synthesis_prompt = _synthesis_prompt.format(
                desc=safe_desc, summary=safe_summary,
                exact=safe_exact, semantic=safe_semantic,
                batch_results=synthesis_context,
                total_batches=total_batches,
            )
            messages = [{"role": "user", "content": synthesis_prompt}]

            async for chunk in call_llm_stream(
                llm_cfg["base_url"],
                api_key or llm_cfg.get("api_key", ""),
                llm_cfg["model"],
                messages, temperature=0.3, max_tokens=_max_tokens, timeout=_timeout,
            ):
                yield chunk

        else:
            # ── No batching: original single-call flow ──
            yield json.dumps({
                "type": "status",
                "text": "🚀 Step 4/5: 雲端 LLM 深度分析中...",
            }) + "\n"

            code_context = _build_context(results_to_analyze)
            safe_context = sanitize_for_cloud(code_context)

            prompt = f"""你是 Avision 軟體部門的資深 RCA 工程師。

## Bug 描述
{safe_desc}

## 問題摘要
{safe_summary}

## Regex 萃取的精確關鍵字
{safe_exact}

## 語意擴充關鍵字
{safe_semantic}

## 相關程式碼片段（RRF 融合排序）
{safe_context}

## 任務
請進行 Root Cause Analysis：
1. **根本原因**：為什麼會出現這個 Bug
2. **受影響檔案與行號**：需修改的具體位置
3. **修復建議**：具體的程式碼修改建議
4. **驗證方法**：如何確認修復有效

用繁體中文回覆，程式碼用 markdown code block。"""

            messages = [{"role": "user", "content": prompt}]

            async for chunk in call_llm_stream(
                llm_cfg["base_url"],
                api_key or llm_cfg.get("api_key", ""),
                llm_cfg["model"],
                messages, temperature=0.3, max_tokens=_max_tokens, timeout=_timeout,
            ):
                yield chunk

    except Exception as e:
        yield json.dumps({
            "type": "status",
            "text": f"⚠️ Step 4 分析中斷: {e}",
        }) + "\n"

    total_elapsed = time.time() - t0
    yield _step_event(4, "done", elapsed=time.time() - step_start,
                      detail=f"總耗時 {total_elapsed:.1f}s")
    yield json.dumps({
        "type": "status",
        "text": f"🎉 全部完成！總耗時 {total_elapsed:.1f} 秒",
    }) + "\n"
    yield json.dumps({"type": "done"}) + "\n"
