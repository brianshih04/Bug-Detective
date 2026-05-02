FROM python:3.12-slim

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App files
COPY backend/ ./backend/
COPY public/ ./public/
COPY scripts/ ./scripts/
COPY data/ ./data/

EXPOSE 17580

CMD ["python", "-m", "uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "17580"]
