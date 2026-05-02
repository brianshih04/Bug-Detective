"""Bug-Detective FastAPI Server with LlamaIndex RAG."""
import json
import os
import subprocess
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.config import (
    PORT, QDRANT_URL, COLLECTION_NAME,
    OLLAMA_URL, load_llm_config, save_llm_config, LLM_PRESETS, DATA_DIR, PUBLIC_DIR,
)
from backend.rca import hybrid_search, full_rca_stream
from backend.security import sanitize_for_cloud

# --- Pydantic models ---
class AnalyzeRequest(BaseModel):
    log_text: str
    bug_description: str = ""
    api_key: str = ""  # passed from client, never persisted
    top_k: int = 20  # number of search results for RCA
    max_tokens: int = 0  # 0 = use server default from llm-config
    timeout: int = 0  # 0 = use server default from llm-config

class SearchRequest(BaseModel):
    query: str
    top_k: int = 10

class LLMConfigRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provider: str = ""
    max_tokens: int = 4096
    timeout: int = 300

class FetchModelsRequest(BaseModel):
    base_url: str
    api_key: str = ""

class SanitizeRequest(BaseModel):
    text: str = ""

# --- App lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"Connecting to Qdrant: {QDRANT_URL}")
    app.state.qdrant = QdrantClient(url=QDRANT_URL)

    # Ensure collection exists check
    try:
        info = app.state.qdrant.get_collection(COLLECTION_NAME)
        print(f"Collection '{COLLECTION_NAME}': {info.points_count} vectors")
    except Exception:
        print(f"⚠️ Collection '{COLLECTION_NAME}' not found. Run ingest.py first!")

    yield
    # Shutdown
    print("Shutting down...")

app = FastAPI(title="Bug-Detective", version="2.0", lifespan=lifespan)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Static files ---
if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")

@app.get("/")
async def serve_index():
    index_path = PUBLIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return JSONResponse({"error": "Frontend not built"}, status_code=404)

# --- API Routes ---
def _git_version():
    """Get short git commit hash and dirty flag."""
    try:
        base = Path(__file__).parent.parent
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(base), stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(base), stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        dirty = "*" if status else ""
        return f"v2.0-{rev}{dirty}"
    except Exception:
        return "v2.0-unknown"


APP_VERSION = _git_version()


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}

@app.get("/api/repos/status")
async def repos_status(request: Request):
    try:
        qdrant: QdrantClient = request.app.state.qdrant
        info = qdrant.get_collection(COLLECTION_NAME)
        return {
            "collection": COLLECTION_NAME,
            "vectors": info.points_count,
            "status": str(info.status),
            "config": load_llm_config().get("provider", "unknown"),
        }
    except Exception as e:
        return {"collection": COLLECTION_NAME, "error": str(e), "vectors": 0}

@app.post("/api/search")
async def search(req: SearchRequest):
    results = await hybrid_search(req.query, top_k=req.top_k)
    return {"query": req.query, "results": results, "count": len(results)}

@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    """Full RCA pipeline with SSE streaming."""
    async def event_stream() -> AsyncGenerator[str, None]:
        async for chunk in full_rca_stream(req.log_text, req.bug_description, req.api_key, top_k=req.top_k, max_tokens=req.max_tokens, timeout=req.timeout):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# --- LLM Config ---
@app.get("/api/llm-config")
async def get_llm_config():
    cfg = load_llm_config()
    # Never expose full API key to frontend
    if cfg.get("api_key"):
        cfg["api_key_masked"] = cfg["api_key"][:8] + "***"
        cfg["api_key"] = ""
    return cfg

@app.put("/api/llm-config")
async def put_llm_config(req: LLMConfigRequest):
    data = req.model_dump(exclude_none=True)
    # Never persist api_key to disk — only lives in client browser memory
    data.pop("api_key", None)
    cfg = save_llm_config(data)
    # Check if there's a key on disk (from .env preset)
    if cfg.get("api_key"):
        cfg["api_key_masked"] = cfg["api_key"][:8] + "***"
        cfg["api_key"] = ""
    return cfg

@app.get("/api/llm-presets")
async def get_presets():
    presets = {}
    for k, v in LLM_PRESETS.items():
        p = dict(v)
        if p.get("api_key"):
            p["api_key_masked"] = p["api_key"][:8] + "***"
            p["api_key"] = ""
        presets[k] = p
    return presets

@app.post("/api/llm-config/preset/{provider}")
async def apply_preset(provider: str):
    if provider not in LLM_PRESETS:
        raise HTTPException(404, f"Unknown preset: {provider}")
    cfg = save_llm_config(LLM_PRESETS[provider])
    return cfg

@app.get("/api/models")
async def list_models():
    """List available models from Ollama (legacy, uses default OLLAMA_URL)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return {"models": [m["name"] for m in data.get("models", [])]}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/fetch-models")
async def fetch_models(req: FetchModelsRequest):
    """Fetch available models from any OpenAI-compatible / Ollama endpoint."""
    base_url = req.base_url.rstrip("/")
    headers = {}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"

    models = []
    source = "unknown"

    # Normalize: strip /chat/completions if present (backward compat)
    api_root = base_url
    for suffix in ("/chat/completions",):
        if api_root.endswith(suffix):
            api_root = api_root[: -len(suffix)]
            break

    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            candidates = [
                api_root + "/models",       # OpenAI-compatible (works for most)
                api_root + "/api/tags",     # Ollama
            ]
            for models_url in candidates:
                try:
                    resp = await client.get(models_url, headers=headers)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    # OpenAI-compatible format: {data: [{id, ...}]}
                    raw = data.get("data", [])
                    if raw and isinstance(raw, list) and isinstance(raw[0], dict):
                        ids = [m.get("id") or m.get("name", "") for m in raw if m.get("id") or m.get("name")]
                        if ids:
                            models = sorted(set(ids))
                            source = "ollama" if "/api/tags" in models_url else "openai_compatible"
                            break
                except Exception:
                    continue

    except httpx.TimeoutException:
        return {"models": [], "source": None, "error": "連線逾時"}
    except Exception as e:
        return {"models": [], "source": None, "error": str(e)}

    return {"models": models, "source": source}

# --- Security ---
@app.post("/api/sanitize")
async def test_sanitize(req: SanitizeRequest):
    return {"original": req.text, "sanitized": sanitize_for_cloud(req.text)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.server:app", host="0.0.0.0", port=PORT, reload=False)
