#!/usr/bin/env python3
"""Ingestion pipeline: Tree-sitter AST parsing → LlamaIndex CodeSplitter → Qdrant."""
import os
import sys
import time
import hashlib
from pathlib import Path
from typing import Optional

from llama_index.core import Document, VectorStoreIndex, StorageContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
import httpx

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.config import SOURCE_DIR, QDRANT_URL, COLLECTION_NAME, EMBEDDING_MODEL, EMBEDDING_DIM, OLLAMA_URL

# --- File extensions to index ---
C_EXTENSIONS = {".c", ".h"}
CPP_EXTENSIONS = {".cpp", ".hpp", ".cc", ".cxx", ".hxx", ".hh", ".ipp"}
ALL_EXTENSIONS = C_EXTENSIONS | CPP_EXTENSIONS

# --- Embedding setup ---
def get_embedding_dim() -> int:
    """Get embedding dimension for the model."""
    return EMBEDDING_DIM

def get_language(path: Path) -> str:
    """Detect C or C++ from file extension."""
    ext = path.suffix.lower()
    if ext in CPP_EXTENSIONS:
        return "cpp"
    return "c"

def walk_source_files(source_dir: str) -> list[Path]:
    """Walk directory and collect source files."""
    root = Path(source_dir)
    if not root.exists():
        print(f"ERROR: Source directory {source_dir} not found")
        sys.exit(1)
    
    files = []
    # Skip common non-source directories
    skip_dirs = {".git", "node_modules", "__pycache__", "build", "dist", "vendor", "third_party", "external"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Check skip dirs
        if any(part in skip_dirs for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in ALL_EXTENSIONS:
            files.append(path)
    return sorted(files)

def create_documents(files: list[Path], source_dir: str) -> list[Document]:
    """Create LlamaIndex Documents with metadata."""
    source_root = Path(source_dir)
    documents = []
    skipped = 0
    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  Skip {fpath}: {e}")
            skipped += 1
            continue
        
        if not content.strip():
            skipped += 1
            continue
        
        rel_path = str(fpath.relative_to(source_root))
        lang = get_language(fpath)
        
        # Count lines for metadata
        lines = content.count('\n') + 1
        
        doc = Document(
            text=content,
            metadata={
                "file_path": rel_path,
                "file_name": fpath.name,
                "language": lang,
                "line_count": lines,
                "repo": "infernoStart01",
            },
            excluded_llm_metadata_keys=["line_count"],
            excluded_embed_metadata_keys=["line_count", "repo"],
        )
        documents.append(doc)
    
    return documents, skipped

def build_index(documents: list[Document], batch_size: int = 100):
    """Build Qdrant vector index from documents."""
    print(f"\nInitializing embedding model: {EMBEDDING_MODEL}")
    embed_model = OllamaEmbedding(
        model_name=EMBEDDING_MODEL,
        base_url=OLLAMA_URL,
        embed_dim=EMBEDDING_DIM,
    )
    
    print(f"Connecting to Qdrant: {QDRANT_URL}")
    qdrant_client = QdrantClient(url=QDRANT_URL)
    
    # Check if collection exists, delete and recreate
    collections = qdrant_client.get_collections().collections
    existing = [c.name for c in collections]
    if COLLECTION_NAME in existing:
        print(f"Deleting existing collection '{COLLECTION_NAME}'...")
        qdrant_client.delete_collection(COLLECTION_NAME)
    
    # Create vector store
    vector_store = QdrantVectorStore(
        client=qdrant_client,
        collection_name=COLLECTION_NAME,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    # Code splitter (SentenceSplitter — avoids tree-sitter compatibility issues)
    splitter = SentenceSplitter(
        chunk_size=15000,
        chunk_overlap=500,
        separator="\n",
    )
    
    print(f"Splitting {len(documents)} documents...")
    nodes = splitter.get_nodes_from_documents(documents, show_progress=True)
    print(f"Created {len(nodes)} chunks")
    
    # Build index
    print("Building vector index (this may take a while)...")
    start = time.time()
    
    index = VectorStoreIndex(
        nodes,
        storage_context=storage_context,
        embed_model=embed_model,
        show_progress=True,
    )
    
    elapsed = time.time() - start
    print(f"\nIndexing complete in {elapsed:.1f}s")
    
    # Print stats
    collection_info = qdrant_client.get_collection(COLLECTION_NAME)
    print(f"Collection: {COLLECTION_NAME}")
    print(f"  Vectors: {collection_info.points_count}")
    print(f"  Status: {collection_info.status}")
    
    return index

def main():
    print("=" * 60)
    print("Bug-Detective: LlamaIndex Ingestion Pipeline")
    print("=" * 60)
    
    # Walk source files
    print(f"\nScanning source directory: {SOURCE_DIR}")
    files = walk_source_files(SOURCE_DIR)
    print(f"Found {len(files)} source files")
    
    if not files:
        print("No files to index!")
        sys.exit(1)
    
    # Create documents
    print(f"\nCreating documents...")
    documents, skipped = create_documents(files, SOURCE_DIR)
    print(f"Created {len(documents)} documents (skipped {skipped})")
    
    # Build index
    build_index(documents)
    
    print("\n✅ Done!")

if __name__ == "__main__":
    main()
