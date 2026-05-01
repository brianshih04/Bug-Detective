#!/bin/bash
# deploy.sh — Bug Detective 部署到 DGX Spark (host mode, 不用 Docker)
set -e

PROJECT_DIR="/home/avuser/bug-detective"
INFERNO_ROOT="/home/avuser/infernoStart01"

echo "=== Bug Detective Deploy ==="

# 1. Install Node.js deps
cd "$PROJECT_DIR"
echo "[1/5] Installing Node.js dependencies..."
npm install --omit=dev 2>/dev/null || npm install

# 2. Install Python deps (for embedding search)
echo "[2/5] Installing Python dependencies..."
pip3 install --user numpy onnxruntime-gpu tokenizers huggingface_hub 2>/dev/null || \
  pip3 install numpy onnxruntime-gpu tokenizers huggingface_hub

# 3. Build code index
echo "[3/5] Building code index..."
python3 scripts/build-index.py

# 4. Build embedding index (GPU)
echo "[4/5] Building embedding index..."
python3 scripts/build-embeddings.py --rebuild

# 5. Start server
echo "[5/5] Starting server on port 17580..."
export INFERNO_ROOT="$INFERNO_ROOT"
nohup node --env-file=.env server.mjs > /home/avuser/bug-detective/server.log 2>&1 &
echo $! > /home/avuser/bug-detective/server.pid
echo "Server started with PID $(cat /home/avuser/bug-detective/server.pid)"
echo ""
echo "=== Done! ==="
echo "API: http://localhost:17580"
echo "Log: $PROJECT_DIR/server.log"
