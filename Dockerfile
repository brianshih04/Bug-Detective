FROM node:24-slim

WORKDIR /app

# Node.js deps
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev 2>/dev/null || npm install --omit=dev

# App files
COPY server.mjs .
COPY public/ ./public/
COPY scripts/ ./scripts/
COPY data/ ./data/

# Python for embedding search
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir \
    numpy onnxruntime-gpu tokenizers huggingface_hub

EXPOSE 17580

CMD ["node", "--env-file=.env", "server.mjs"]
