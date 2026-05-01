#!/usr/bin/env python3
"""
Embedding search CLI — called by server.mjs for semantic code search.

Operates on chunk-level embeddings (function/block granularity).
Returns chunk results, grouped by file with assembled context.

Usage:
  python scripts/embed-search.py "query text" [--top 20]

Outputs JSON to stdout:
  { "results": [ { "path", "score", "chunks": [ { "name", "type", "startLine", "endLine", "content" } ], "symbols", "lines" }, ... ] }
"""

import json, sys, os, time
import numpy as np
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
EMBEDDING_PATH = PROJECT_DIR / "data" / "embeddings.npy"
META_PATH = PROJECT_DIR / "data" / "embeddings-meta.json"
INDEX_PATH = PROJECT_DIR / "data" / "code-index.json"

# Global caches (persists across calls when used as module)
_session = None
_tokenizer = None
_embeddings = None
_meta = None
_code_index = None

VLLM_EMBED_URL = os.environ.get("VLLM_EMBED_URL", "")


def load_data():
    """Load embeddings + metadata + code index."""
    global _embeddings, _meta, _code_index
    if _embeddings is not None:
        return _embeddings, _meta, _code_index

    _embeddings = np.load(EMBEDDING_PATH)  # float16 (n_chunks, 1024)
    with open(META_PATH) as f:
        _meta = json.load(f)
    with open(INDEX_PATH) as f:
        _code_index = json.load(f)
    return _embeddings, _meta, _code_index


def embed_query_vllm(text):
    """Embed via vLLM REST API (GPU). Returns normalized float16 vector."""
    import urllib.request
    url = VLLM_EMBED_URL.rstrip("/")
    payload = json.dumps({"model": "BAAI/bge-m3", "input": [text]}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    emb = np.array(data["data"][0]["embedding"], dtype=np.float32)
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm
    return emb.astype(np.float16)


def embed_query_onnx(text):
    """Embed via local ONNX runtime (CPU fallback). Returns normalized float16 vector."""
    global _session, _tokenizer
    if _session is None:
        from huggingface_hub import hf_hub_download
        from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel
        from tokenizers import Tokenizer

        opts = SessionOptions()
        opts.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 8
        opts.inter_op_num_threads = 2

        model_path = hf_hub_download("Xenova/bge-m3", "onnx/model_int8.onnx")
        tokenizer_path = hf_hub_download("Xenova/bge-m3", "tokenizer.json")

        _session = InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(tokenizer_path)

    encoded = _tokenizer.encode(text)
    input_ids = np.array([encoded.ids], dtype=np.int64)
    attn_mask = np.array([encoded.attention_mask], dtype=np.int64)

    outputs = _session.run(None, {"input_ids": input_ids, "attention_mask": attn_mask})
    hidden = outputs[0]  # (1, seq, 1024)
    mask_exp = attn_mask[:, :, np.newaxis].astype(np.float32)
    emb = (hidden * mask_exp).sum(axis=1) / mask_exp.sum(axis=1)
    emb = emb / np.where(np.linalg.norm(emb, axis=1, keepdims=True) > 0,
                         np.linalg.norm(emb, axis=1, keepdims=True), 1)
    return emb.astype(np.float16)


def embed_query(text):
    """Embed a single query text via vLLM (GPU) or ONNX (CPU fallback)."""
    if VLLM_EMBED_URL:
        try:
            return embed_query_vllm(text)
        except Exception as e:
            print(f"[embed-search] vLLM failed ({e}), falling back to ONNX", file=sys.stderr)
    return embed_query_onnx(text)


def search(query, top_k=20):
    """Semantic search: embed query, cosine similarity against chunk embeddings.
    
    Returns file-level results with matching chunks attached.
    Multiple chunks from the same file are grouped together.
    """
    embeddings, meta, code_index = load_data()
    query_emb = embed_query(query)

    # Cosine similarity (embeddings are already normalized)
    scores = (embeddings.astype(np.float32) @ query_emb.astype(np.float32).T).flatten()
    top_indices = np.argsort(scores)[::-1][:top_k]

    # Build chunk metadata lookup
    chunk_meta = meta.get("chunks", [])
    source_root = code_index.get("sourceRoot", "")

    # Group chunks by file
    file_chunks = {}  # path -> list of (score, chunk_info)
    all_symbols = {}  # path -> set of symbols
    file_lines = {}   # path -> total lines

    # Load file metadata for line counts
    for f in code_index.get("files", []):
        file_lines[f["path"]] = f.get("lines", 0)

    for idx in top_indices:
        score = float(scores[idx])
        if score < 0.1:
            continue  # skip irrelevant

        if idx >= len(chunk_meta):
            continue

        cm = chunk_meta[idx]
        path = cm["path"]

        # Build chunk info with content from code index
        chunk_info = {
            "name": cm.get("name", ""),
            "type": cm.get("type", "block"),
            "startLine": cm.get("startLine", 0),
            "endLine": cm.get("endLine", 0),
            "score": round(score, 4),
        }

        # Get verbatim content from code index
        for chunk in code_index.get("chunks", []):
            if chunk["id"] == cm["id"]:
                chunk_info["content"] = chunk.get("content", "")
                for sym in chunk.get("symbols", []):
                    if path not in all_symbols:
                        all_symbols[path] = set()
                    all_symbols[path].add(sym)
                break

        if path not in file_chunks:
            file_chunks[path] = []
        file_chunks[path].append(chunk_info)

    # Convert to results list
    results = []
    for path, chunks in sorted(file_chunks.items(), key=lambda x: max(c["score"] for c in x[1]), reverse=True):
        best_score = max(c["score"] for c in chunks)
        results.append({
            "path": path,
            "score": round(best_score, 4),
            "lines": file_lines.get(path, 0),
            "symbols": list(all_symbols.get(path, []))[:30],
            "chunks": chunks,
        })

    return results


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: embed-search.py <query> [--top N]"}))
        sys.exit(1)

    query = sys.argv[1]
    top_k = 20
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        top_k = int(sys.argv[idx + 1])

    t0 = time.time()
    results = search(query, top_k)
    elapsed = (time.time() - t0) * 1000

    print(json.dumps({
        "query": query,
        "results": results,
        "totalIndexed": len(json.load(open(META_PATH)).get("chunks", [])) if os.path.exists(META_PATH) else 0,
        "searchTimeMs": round(elapsed, 1),
    }))


if __name__ == "__main__":
    main()
