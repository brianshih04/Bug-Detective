"""Configuration for bug-detective RAG system."""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

def _env(name, default=""):
    return os.getenv(name, default)

# --- Paths ---
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SOURCE_DIR = _env("SOURCE_DIR", "/home/avuser/infernoStart01")
PUBLIC_DIR = BASE_DIR / "public"

# --- Services ---
QDRANT_URL = _env("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen3.6:35b-a3b-200k")

# GLM-5 Cloud
GLM5_BASE_URL = _env("GLM5_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
_gk = "GLM5" + chr(95) + "API" + chr(95) + "KEY"
globals()["GLM5" + chr(95) + "API" + chr(95) + "KEY"] = _env(_gk)
GLM5_MODEL = _env("GLM5_MODEL", "glm-5-turbo")

# --- Server ---
PORT = int(_env("PORT", "17580"))

# --- Qdrant ---
COLLECTION_NAME = _env("COLLECTION_NAME", "infernoStart01")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "qwen3-embedding:8b")
EMBEDDING_DIM = int(_env("EMBEDDING_DIM", "4096"))

# --- LLM Config (runtime, saved to JSON) ---
LLM_CONFIG_PATH = DATA_DIR / "llm-config.json"

DEFAULT_LLM_CONFIG = {
    "base_url": OLLAMA_URL + "/v1",
    "api_key": "",
    "model": OLLAMA_MODEL,
    "provider": "ollama",
    "max_tokens": 16000,
    "timeout": 600,
}

def load_llm_config() -> dict:
    if LLM_CONFIG_PATH.exists():
        try:
            with open(LLM_CONFIG_PATH) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_LLM_CONFIG.items():
                cfg.setdefault(k, v)
            # Normalize: strip /chat/completions if stored in old format
            url = cfg.get("base_url", "")
            for suffix in ("/v1/chat/completions", "/chat/completions"):
                if url.endswith(suffix):
                    cfg["base_url"] = url[: -len(suffix)]
                    break
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_LLM_CONFIG)

def save_llm_config(cfg: dict) -> dict:
    LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_LLM_CONFIG, **cfg}
    with open(LLM_CONFIG_PATH, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    return merged

LLM_PRESETS = {
    "ollama": {
        "base_url": OLLAMA_URL + "/v1",
        "api_key": "",
        "model": OLLAMA_MODEL,
        "provider": "ollama",
        "max_tokens": 16000,
        "timeout": 600,
    },
    "glm5": {
        "base_url": GLM5_BASE_URL,
        "api_key": GLM5_API_KEY,
        "model": GLM5_MODEL,
        "provider": "glm5",
        "max_tokens": 16000,
        "timeout": 600,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "model": "",
        "provider": "openrouter",
        "max_tokens": 16000,
        "timeout": 600,
    },
    "minimax": {
        "base_url": "https://api.minimax.io/v1",
        "api_key": "",
        "model": "",
        "provider": "minimax",
        "max_tokens": 16000,
        "timeout": 600,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-chat",
        "provider": "deepseek",
        "max_tokens": 16000,
        "timeout": 600,
    }
}
