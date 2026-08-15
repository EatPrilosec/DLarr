# Stage 1: Build Vite React Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Python Backend Runtime
FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend Code
COPY backend/ /app/backend/

# Copy built frontend assets to FastAPI static directory
COPY --from=frontend-builder /app/backend/app/static /app/backend/app/static

# Create configuration directory
RUN mkdir -p /config

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV DLARR_DATA_DIR=/config
ENV DLARR_PORT=6752
ENV DLARR_HOST=0.0.0.0

EXPOSE 6752

# Start server
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "6752"]
