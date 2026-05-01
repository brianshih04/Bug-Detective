#!/usr/bin/env python3
"""
Build a chunk-level searchable index of the InfernoStart01 source code.

Splits C/C++ files into function-level and block-level chunks.
Each chunk is independently searchable and embeddable, giving much
better search precision than whole-file indexing.

Chunk types:
  function    — A complete function implementation
  macro_group — Consecutive #define / #undef lines
  struct      — A struct/union definition block
  enum        — An enum definition block
  typedef     — A typedef block
  prototype   — Remaining declarations (headers only)
  block       — Fallback: anything not classified above
"""

import json
import os
import re
import sys
from pathlib import Path

SOURCE_ROOT = Path(os.environ.get("INFERNO_ROOT", "/mnt/d/Projects/infernoStart01"))
OUTPUT_PATH = Path(os.environ.get("INDEX_OUTPUT", "data/code-index.json"))

INCLUDE_EXT = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"}
SKIP_DIRS = {
    "node_modules", ".git", ".next", "out", "build", "__pycache__",
    "CMakeFiles", ".claude", ".od", ".tmp",
}

# Minimum content length to index a chunk
MIN_CHUNK_CHARS = 30


# =============================================================================
# Comment / string stripping (preserves character positions)
# =============================================================================

def strip_comments_and_strings(text: str) -> str:
    """Replace C/C++ comments and string literals with spaces."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '/' and i + 1 < n:
            if text[i + 1] == '/':
                # Line comment
                while i < n and text[i] != '\n':
                    out.append(' '); i += 1
                continue
            if text[i + 1] == '*':
                # Block comment
                out.append('  '); i += 2
                while i < n:
                    if text[i:i+2] == '*/':
                        out.append('  '); i += 2; break
                    out.append('\n' if text[i] == '\n' else ' '); i += 1
                continue
        if c == '"':
            out.append(' '); i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n:
                    out.append('  '); i += 2
                else:
                    out.append(' '); i += 1
            if i < n:
                out.append(' '); i += 1
            continue
        if c == "'":
            out.append(' '); i += 1
            while i < n and text[i] != "'":
                if text[i] == '\\' and i + 1 < n:
                    out.append('  '); i += 2
                else:
                    out.append(' '); i += 1
            if i < n:
                out.append(' '); i += 1
            continue
        out.append(c); i += 1
    return ''.join(out)


# =============================================================================
# Symbol extraction
# =============================================================================

def extract_symbols(text: str) -> list[str]:
    """Extract searchable identifiers from code content."""
    syms = set()
    # CamelCase identifiers (types, class names, module names)
    syms.update(re.findall(r'\b[A-Z][a-zA-Z0-9_]{2,}\b', text))
    # ALL_CAPS identifiers (macros, defines, enums, error codes)
    syms.update(re.findall(r'\b[A-Z][A-Z0-9_]{2,}\b', text))
    # Function calls: identifier followed by (
    syms.update(re.findall(r'\b([a-z_]\w{2,})\s*\(', text))
    # String literals (error messages, log tags)
    for m in re.finditer(r'"([^"]{3,})"', text):
        syms.add(m.group(1))
    # Hex values (hardware addresses, error codes)
    syms.update(re.findall(r'\b0x[0-9A-Fa-f]{2,}\b', text))
    # Numeric constants that look like error/status codes
    syms.update(re.findall(r'\b(?<!\w)(20\d{4}|50\d{4}|30\d{4}|10\d{4})\b', text))
    return list(syms)


# =============================================================================
# Function name extraction
# =============================================================================

_COMMON_C_TYPES = r'\b(?:static|extern|inline|virtual|const|volatile|unsigned|signed|long|short|struct|enum|union|void|int|char|float|double|bool|BOOLEAN|UINT8|UINT16|UINT32|UINT64|INT8|INT16|INT32|INT64|size_t|BOOL|TRUE|FALSE|BYTE|WORD|DWORD|HANDLE|STATUS)\b'
_MFP_TYPES = r'\b(?:ErrCode|ErrorCode|Result|RetCode)\b'

def func_name_from_lines(stripped_lines: list[str], start: int, end: int) -> str:
    """Try to extract a function name from lines before the opening brace."""
    # Join up to 8 lines before brace
    sig_text = ' '.join(l.strip() for l in stripped_lines[start:max(start, end - 8):end])
    # Remove type keywords
    cleaned = re.sub(_COMMON_C_TYPES, '', sig_text, flags=re.IGNORECASE)
    cleaned = re.sub(_MFP_TYPES, '', cleaned)
    cleaned = re.sub(r'[*&]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Find pattern: word(
    m = re.search(r'(\w+)\s*\(', cleaned)
    return m.group(1) if m else ""


# =============================================================================
# Chunk extraction — implementation files (.c, .cpp, …)
# =============================================================================

def extract_impl_chunks(content: str) -> list[dict]:
    """Split a .c/.cpp file into function-level chunks."""
    lines = content.split('\n')
    stripped = strip_comments_and_strings(content)
    stripped_lines = stripped.split('\n')
    n = len(stripped_lines)

    chunks = []
    brace_depth = 0
    sig_start = -1          # Line index where the signature begins
    covered_lines = set()   # Lines already claimed by a chunk

    i = 0
    while i < n:
        sl = stripped_lines[i]
        for ch in sl:
            if ch == '{':
                if brace_depth == 0:
                    # Look backwards for signature start (up to 8 lines)
                    sig_start = i
                    for k in range(i - 1, max(i - 9, -1), -1):
                        prev = stripped_lines[k].strip()
                        if (not prev
                            or prev.endswith(';')
                            or prev.endswith('}')
                            or (prev.startswith('#') and not prev.startswith('#define'))):
                            sig_start = k + 1
                            break
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth <= 0 and sig_start >= 0:
                    brace_depth = 0
                    # Completed a top-level block
                    chunk_text = '\n'.join(lines[sig_start:i + 1])
                    if len(chunk_text.strip()) >= MIN_CHUNK_CHARS:
                        first = stripped_lines[sig_start].strip()
                        if first.startswith('typedef'):
                            ctype = 'typedef'
                        elif re.match(r'^(struct|union)\b', first):
                            ctype = first.split()[0]
                        elif first.startswith('enum'):
                            ctype = 'enum'
                        else:
                            ctype = 'function'

                        name = func_name_from_lines(stripped_lines, sig_start, i + 1)
                        chunks.append({
                            "type": ctype,
                            "name": name or f"{ctype}_L{sig_start + 1}",
                            "startLine": sig_start + 1,
                            "endLine": i + 1,
                            "content": chunk_text,
                            "symbols": extract_symbols(chunk_text),
                        })
                        for ln in range(sig_start, i + 1):
                            covered_lines.add(ln)
                    sig_start = -1
        i += 1

    # Collect consecutive #define groups not inside any function
    i = 0
    while i < n:
        sl = stripped_lines[i].strip()
        if sl.startswith('#define') and i not in covered_lines:
            start = i
            while i < n and stripped_lines[i].strip().startswith('#define'):
                i += 1
            def_text = '\n'.join(lines[start:i])
            if len(def_text.strip()) >= MIN_CHUNK_CHARS:
                chunks.append({
                    "type": "macro_group",
                    "name": f"defines_L{start + 1}",
                    "startLine": start + 1,
                    "endLine": i,
                    "content": def_text,
                    "symbols": extract_symbols(def_text),
                })
        else:
            i += 1

    return chunks


# =============================================================================
# Chunk extraction — header files (.h, .hpp, …)
# =============================================================================

def extract_header_chunks(content: str) -> list[dict]:
    """Split a header file into struct/enum/typedef/define blocks."""
    lines = content.split('\n')
    stripped = strip_comments_and_strings(content)
    stripped_lines = stripped.split('\n')
    n = len(stripped_lines)

    chunks = []
    brace_depth = 0
    block_start = -1
    covered_lines = set()

    # Pass 1: Extract brace-delimited blocks (struct, enum, typedef, inline funcs)
    i = 0
    while i < n:
        sl = stripped_lines[i]
        for ch in sl:
            if ch == '{':
                if brace_depth == 0:
                    block_start = i
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth <= 0 and block_start >= 0:
                    brace_depth = 0
                    # Look back for the keyword that starts this block
                    kw_start = block_start
                    for k in range(block_start - 1, max(block_start - 4, -1), -1):
                        prev = stripped_lines[k].strip()
                        if not prev or prev.endswith(';') or prev.endswith('}'):
                            kw_start = k + 1
                            break

                    first = stripped_lines[kw_start].strip()
                    chunk_text = '\n'.join(lines[kw_start:i + 1])
                    if len(chunk_text.strip()) >= MIN_CHUNK_CHARS:
                        if first.startswith('typedef'):
                            ctype = 'typedef'
                        elif re.match(r'^(struct|union)\b', first):
                            ctype = first.split()[0]
                        elif first.startswith('enum'):
                            ctype = 'enum'
                        else:
                            ctype = 'block'

                        # Extract name
                        name = ""
                        # struct Foo { ... } or typedef struct { ... } Foo;
                        m = re.search(r'(?:struct|union|enum)\s+(\w+)', first)
                        if m:
                            name = m.group(1)
                        m2 = re.search(r'\}\s*(\w+)\s*;', chunk_text)
                        if m2 and not name:
                            name = m2.group(1)
                        if not name:
                            name = f"{ctype}_L{kw_start + 1}"

                        chunks.append({
                            "type": ctype,
                            "name": name,
                            "startLine": kw_start + 1,
                            "endLine": i + 1,
                            "content": chunk_text,
                            "symbols": extract_symbols(chunk_text),
                        })
                        for ln in range(kw_start, i + 1):
                            covered_lines.add(ln)
                    block_start = -1
        i += 1

    # Pass 2: Collect consecutive #define groups
    i = 0
    while i < n:
        sl = stripped_lines[i].strip()
        if sl.startswith('#define') and i not in covered_lines:
            start = i
            while i < n and stripped_lines[i].strip().startswith('#define'):
                i += 1
            def_text = '\n'.join(lines[start:i])
            if len(def_text.strip()) >= MIN_CHUNK_CHARS:
                chunks.append({
                    "type": "macro_group",
                    "name": f"defines_L{start + 1}",
                    "startLine": start + 1,
                    "endLine": i,
                    "content": def_text,
                    "symbols": extract_symbols(def_text),
                })
                for ln in range(start, i):
                    covered_lines.add(ln)
        else:
            i += 1

    # Pass 3: Collect remaining declarations as "prototype" chunks
    # Group consecutive uncovered non-empty lines
    i = 0
    while i < n:
        if i not in covered_lines and stripped_lines[i].strip():
            start = i
            while i < n and (i not in covered_lines) and (stripped_lines[i].strip() or (i + 1 < n and (i + 1) not in covered_lines)):
                if not stripped_lines[i].strip():
                    # Keep going if the next line is also uncovered
                    next_covered = (i + 1) in covered_lines
                    if next_covered:
                        break
                    i += 1
                    continue
                i += 1
            proto_text = '\n'.join(lines[start:i])
            if len(proto_text.strip()) >= MIN_CHUNK_CHARS:
                # Only include if it looks like declarations (has ; or ())
                if ';' in proto_text or '(' in proto_text:
                    chunks.append({
                        "type": "prototype",
                        "name": f"decls_L{start + 1}",
                        "startLine": start + 1,
                        "endLine": i,
                        "content": proto_text,
                        "symbols": extract_symbols(proto_text),
                    })
                    for ln in range(start, i):
                        covered_lines.add(ln)
        else:
            i += 1

    return chunks


# =============================================================================
# Index builder
# =============================================================================

def should_index(filepath: Path) -> bool:
    parts = filepath.parts
    return (filepath.suffix.lower() in INCLUDE_EXT
            and not any(s in parts for s in SKIP_DIRS))


def build_index() -> dict:
    files = []
    all_chunks = []
    total_lines = 0
    chunk_id = 0

    print(f"Scanning {SOURCE_ROOT} ...")

    for filepath in sorted(SOURCE_ROOT.rglob("*")):
        if not filepath.is_file() or not should_index(filepath):
            continue
        try:
            rel_path = str(filepath.relative_to(SOURCE_ROOT)).replace("\\", "/")
            content = filepath.read_text(encoding="utf-8", errors="replace")
            # Strip BOM
            if content and content[0] == '\ufeff':
                content = content[1:]
            file_lines = len(content.split('\n'))
            total_lines += file_lines

            is_header = filepath.suffix.lower() in ('.h', '.hpp', '.hh', '.hxx')
            chunks = extract_header_chunks(content) if is_header else extract_impl_chunks(content)

            file_entry = {
                "path": rel_path,
                "lines": file_lines,
                "chunkCount": len(chunks),
                "chunkIndices": [],
            }

            for c in chunks:
                c["id"] = chunk_id
                c["path"] = rel_path
                all_chunks.append(c)
                file_entry["chunkIndices"].append(chunk_id)
                chunk_id += 1

            files.append(file_entry)

        except Exception as e:
            print(f"  Warning: {filepath}: {e}")

    return {
        "sourceRoot": str(SOURCE_ROOT),
        "totalFiles": len(files),
        "totalChunks": len(all_chunks),
        "totalLines": total_lines,
        "files": files,
        "chunks": all_chunks,
    }


def main():
    print(f"Building chunk-level index from: {SOURCE_ROOT}")
    t0 = __import__('time').time()

    index = build_index()

    elapsed = __import__('time').time() - t0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    size_mb = os.path.getsize(OUTPUT_PATH) / 1024 / 1024
    avg_chunks = index['totalChunks'] / max(index['totalFiles'], 1)
    print(f"\nDone in {elapsed:.1f}s!")
    print(f"  {index['totalFiles']:,} files  →  {index['totalChunks']:,} chunks  ({avg_chunks:.1f} chunks/file)")
    print(f"  {index['totalLines']:,} total lines")
    print(f"  Output: {OUTPUT_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
