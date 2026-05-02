"""Shared fixtures for bug-detective tests."""
import sys
import unittest.mock
from pathlib import Path

# Ensure project root is importable (for `from backend.X import Y`)
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Mock heavy/optional dependencies BEFORE backend imports.
# MagicMock auto-creates any attribute, so `from X import Y` always works.
_mock_packages = [
    "qdrant_client",
    "llama_index",
    "llama_index.core",
    "llama_index.core.retrievers",
    "llama_index.core.schema",
    "llama_index.core.query_engine",
    "llama_index.core.response",
    "llama_index.core.settings",
    "llama_index.core.vector_stores",
    "llama_index.vector_stores",
    "llama_index.vector_stores.qdrant",
    "llama_index.embeddings",
    "llama_index.embeddings.ollama",
    "llama_index.indices",
    "llama_index.indices.vector_store",
]
for _pkg in _mock_packages:
    if _pkg not in sys.modules:
        sys.modules[_pkg] = unittest.mock.MagicMock()
