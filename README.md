# DLarr

**DLarr** is a Dockerized web application designed to find missing episodes in Sonarr that are freely available across the internet using AI-enhanced title, description, and transcript comparison.

Running on **Port 6752**, DLarr connects with **Sonarr**, **Ollama**, **TMDB**, **TVmaze**, **OMDb**, and transcript/subtitle sources (**SubDL**, **OpenSubtitles**) to build a rich multi-source episode database and audit episode consistency with LLMs.

---

## Features (Phase 1)

1. **WebUI Configuration & Connection Hub**:
   - **Ollama AI**: Primary and Fallback model configuration (e.g., `llama3.1:8b`, `qwen2.5:7b`, `mistral:7b`) with live connection and model availability testing.
   - **Sonarr**: URL and API key connection tests with automatic series discovery.
   - **Metadata Providers**: TMDB (v3), TVmaze (public REST), OMDb (IMDb) API keys and live connectivity tests.
   - **Transcript / Subtitle Sources**: SubDL and OpenSubtitles API integration for dialog extraction and content-based matching.

2. **Non-Destructive Multi-Source Episode Database**:
   - Uses Sonarr as the canonical episode baseline (Season #, Episode #, Title, Overview).
   - Ingests and stores variations from TMDB, TVmaze, OMDb, and SubDL independently—preserving differing titles, aliases, numbering offsets, and dialogue transcripts without overwriting.

3. **Two-Tier Matching Engine**:
   - **Tier 1 (Fast-Path)**: Normalized exact title and season/episode matching.
   - **Tier 2 (Ollama Semantic Matching)**: Queries Ollama with structured JSON schema when titles, numberings, or multi-part splits differ.
   - **Model Fallback**: Automatically fails over to the configured Fallback Model if the Primary Model times out or returns unparseable output.

4. **Full-Show Database Consistency & Mismatch Audit Pass**:
   - Once all episodes are ingested, Ollama audits the entire sequence to detect duplicate assignments, off-by-one broadcast order shifts, and plot discrepancies.

---

## Quick Start with Docker

### Using Docker Compose

```yaml
services:
  dlarr:
    image: ghcr.io/your-username/dlarr:latest
    container_name: dlarr
    restart: unless-stopped
    ports:
      - "6752:6752"
    volumes:
      - /path/to/dlarr/config:/config
    environment:
      - DLARR_PORT=6752
      - DLARR_DATA_DIR=/config
      - TZ=UTC
```

Run:
```bash
docker compose up -d
```
Access the WebUI at `http://localhost:6752`.

---

## Local Development

### 1. Backend (FastAPI)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 6752 --reload
```

### 2. Frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev
```

### 3. Run Tests
```bash
pytest backend/tests
```

---

## GitHub Actions & CI/CD

The repository includes a ready `.github/workflows/docker-build-publish.yml` workflow that automatically builds multi-architecture images (`linux/amd64`, `linux/arm64`) and publishes to GitHub Container Registry (GHCR) on push to `main` and version tags.
