#!/usr/bin/env python3
"""
Build embedding index for source code chunks.

Supports two backends:
  1. vLLM REST API (GPU) — preferred, set VLLM_EMBED_URL env var
  2. ONNX Runtime (CPU) — fallback

Outputs:
  data/embeddings.npy         — numpy float16 array (n_chunks × dim)
  data/embeddings-meta.json   — chunk metadata

Usage: python scripts/build-embeddings.py [--rebuild]
"""

import json, os, sys, time, argparse
import numpy as np
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent

INDEX_PATH = PROJECT_DIR / "data" / "code-index.json"
EMBEDDING_PATH = PROJECT_DIR / "data" / "embeddings.npy"
META_PATH = PROJECT_DIR / "data" / "embeddings-meta.json"

VLLM_EMBED_URL = os.environ.get("VLLM_EMBED_URL", "")


def load_index():
    with open(INDEX_PATH) as f:
        return json.load(f)


# =============================================================================
# vLLM GPU embedding (batch)
# =============================================================================

def batch_embed_vllm(texts, batch_size=64):
    """Embed texts via vLLM REST API in batches. Returns float16 normalized array."""
    import urllib.request
    url = VLLM_EMBED_URL.rstrip("/")
    all_embeddings = []
    MAX_CHARS = 3000  # Truncate to avoid exceeding model max token length

    # Truncate texts that are too long
    truncated_texts = [t[:MAX_CHARS] if len(t) > MAX_CHARS else t for t in texts]

    for i in range(0, len(truncated_texts), batch_size):
        batch = truncated_texts[i:i + batch_size]
        payload = json.dumps({"model": "BAAI/bge-m3", "input": batch}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        batch_embs = np.array(
            [d["embedding"] for d in data["data"]],
            dtype=np.float32
        )
        # Normalize
        norms = np.linalg.norm(batch_embs, axis=1, keepdims=True)
        batch_embs = batch_embs / np.where(norms > 0, norms, 1)
        all_embeddings.append(batch_embs)

    return np.vstack(all_embeddings).astype(np.float16)


# =============================================================================
# ONNX CPU embedding (fallback)
# =============================================================================

def build_embedder_onnx():
    from huggingface_hub import hf_hub_download
    from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel
    from tokenizers import Tokenizer

    model_path = hf_hub_download("Xenova/bge-m3", "onnx/model_int8.onnx")
    tokenizer_path = hf_hub_download("Xenova/bge-m3", "tokenizer.json")

    opts = SessionOptions()
    opts.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = 8
    opts.inter_op_num_threads = 2

    session = InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(tokenizer_path)
    return session, tokenizer


def batch_encode_onnx(tokenizer, texts, max_length=512):
    encoded = tokenizer.encode_batch(texts)
    all_ids = [e.ids for e in encoded]
    all_masks = [e.attention_mask for e in encoded]
    max_len = max(len(ids) for ids in all_ids)
    max_len = min(max_len, max_length)
    input_ids = np.zeros((len(texts), max_len), dtype=np.int64)
    attn_mask = np.zeros((len(texts), max_len), dtype=np.int64)
    for i, (ids, mask) in enumerate(zip(all_ids, all_masks)):
        sl = ids[:max_len]
        input_ids[i, :len(sl)] = sl
        attn_mask[i, :len(sl)] = mask[:max_len]
    return input_ids, attn_mask


def batch_embed_onnx(session, tokenizer, texts, batch_size=16):
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        input_ids, attn_mask = batch_encode_onnx(tokenizer, batch)
        outputs = session.run(None, {"input_ids": input_ids, "attention_mask": attn_mask})
        hidden = outputs[0]
        mask_exp = attn_mask[:, :, np.newaxis].astype(np.float32)
        emb = (hidden * mask_exp).sum(axis=1) / mask_exp.sum(axis=1)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        emb = emb / np.where(norms > 0, norms, 1)
        all_embeddings.append(emb.astype(np.float16))
    return np.vstack(all_embeddings)


# =============================================================================
# Chunk text building
# =============================================================================

def build_chunk_text(chunk):
    """Build embedding text: path + type/name + symbols + verbatim content."""
    parts = [chunk["path"]]
    name = chunk.get("name", "")
    ctype = chunk.get("type", "block")
    if name and not name.startswith(f"{ctype}_"):
        parts.append(f"{ctype}:{name}")
    syms = chunk.get("symbols", [])
    if syms:
        parts.append(", ".join(syms[:30]))
    content = chunk.get("content", "")
    if content:
        parts.append(content[:800])  # Keep first 800 chars for embedding
    return " | ".join(parts)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild")
    parser.add_argument("--batch-size", type=int, default=0, help="Override batch size")
    args = parser.parse_args()

    if EMBEDDING_PATH.exists() and not args.rebuild:
        print(f"Embedding index already exists: {EMBEDDING_PATH}")
        print("Use --rebuild to force rebuild")
        return

    print("Loading chunk-level code index...")
    index = load_index()
    chunks = index.get("chunks", [])
    if not chunks:
        print("ERROR: No chunks found in index. Run build-index.py first.")
        sys.exit(1)
    print(f"  {len(chunks)} chunks from {index['totalFiles']} files")

    # Build chunk texts
    print("Building chunk texts...")
    chunk_texts = [build_chunk_text(c) for c in chunks]

    # Choose backend
    use_vllm = bool(VLLM_EMBED_URL)

    if use_vllm:
        print(f"Using vLLM GPU embedding: {VLLM_EMBED_URL}")
        vllm_batch = args.batch_size or 64  # vLLM can handle larger batches
        total_batches = (len(chunk_texts) + vllm_batch - 1) // vllm_batch
        print(f"Embedding {len(chunk_texts)} chunks (batch_size={vllm_batch}, {total_batches} batches)...")

        t0 = time.time()
        all_embeddings = []

        for i in range(0, len(chunk_texts), vllm_batch):
            batch = chunk_texts[i:i + vllm_batch]
            emb = batch_embed_vllm(batch, batch_size=len(batch))
            all_embeddings.append(emb)
            batch_num = i // vllm_batch + 1
            if batch_num % 20 == 0 or batch_num == total_batches:
                elapsed = time.time() - t0
                pct = batch_num / total_batches * 100
                eta = elapsed / batch_num * (total_batches - batch_num)
                print(f"  [{pct:5.1f}%] batch {batch_num}/{total_batches} — {elapsed:.1f}s elapsed, ETA {eta:.0f}s")

        all_embeddings = np.vstack(all_embeddings)
    else:
        print("Using ONNX CPU embedding (fallback)")
        print("Loading BGE-M3 ONNX model...")
        t_load = time.time()
        session, tokenizer = build_embedder_onnx()
        print(f"  Loaded in {time.time()-t_load:.1f}s")

        print("Warming up...")
        _ = batch_embed_onnx(session, tokenizer, ["warmup text"])

        onnx_batch = args.batch_size or 16
        total_batches = (len(chunk_texts) + onnx_batch - 1) // onnx_batch
        print(f"Embedding {len(chunk_texts)} chunks (batch_size={onnx_batch}, {total_batches} batches)...")

        t0 = time.time()
        for i in range(0, len(chunk_texts), onnx_batch):
            batch = chunk_texts[i:i + onnx_batch]
            emb = batch_embed_onnx(session, tokenizer, batch)
            all_embeddings.append(emb)
            batch_num = i // onnx_batch + 1
            if batch_num % 20 == 0 or batch_num == total_batches:
                elapsed = time.time() - t0
                pct = batch_num / total_batches * 100
                eta = elapsed / batch_num * (total_batches - batch_num)
                print(f"  [{pct:5.1f}%] batch {batch_num}/{total_batches} — {elapsed:.1f}s elapsed, ETA {eta:.0f}s")

        all_embeddings = np.vstack(all_embeddings)

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s ({elapsed/len(chunk_texts)*1000:.1f}ms/chunk)")
    print(f"  Embedding shape: {all_embeddings.shape} dtype={all_embeddings.dtype}")

    # Save embeddings
    print(f"Saving embeddings to {EMBEDDING_PATH}...")
    np.save(EMBEDDING_PATH, all_embeddings)
    emb_size = EMBEDDING_PATH.stat().st_size / 1024 / 1024
    print(f"  Embeddings: {emb_size:.1f} MB")

    # Save metadata
    meta = {
        "model": "BAAI/bge-m3" if use_vllm else "Xenova/bge-m3-int8",
        "backend": "vllm-gpu" if use_vllm else "onnx-cpu",
        "dimensions": int(all_embeddings.shape[1]),
        "indexedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "totalChunks": len(chunks),
        "totalFiles": index["totalFiles"],
        "chunks": [
            {
                "id": c["id"],
                "path": c["path"],
                "name": c.get("name", ""),
                "type": c.get("type", "block"),
                "startLine": c.get("startLine", 0),
                "endLine": c.get("endLine", 0),
                "symbols": c.get("symbols", [])[:30],
            }
            for c in chunks
        ],
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f)
    meta_size = META_PATH.stat().st_size / 1024 / 1024
    print(f"  Metadata: {meta_size:.1f} MB")
    print(f"✅ Embedding index build complete! ({len(chunks)} chunks, {emb_size:.1f} MB)")


if __name__ == "__main__":
    main()
