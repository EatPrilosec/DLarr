import json
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.models.setting import Setting
from backend.app.schemas.setting import AppSettings, ConnectionTestRequest, ConnectionTestResponse
from backend.app.services.ollama_client import OllamaClient
from backend.app.services.sonarr_client import SonarrClient
from backend.app.services.tmdb_client import TMDBClient
from backend.app.services.tvmaze_client import TVmazeClient
from backend.app.services.omdb_client import OMDbClient
from backend.app.services.subdl_client import SubDLClient
from backend.app.services.opensubtitles_client import OpenSubtitlesClient
from backend.app.services.concurrency_manager import concurrency_manager

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
async def get_settings(db: AsyncSession = Depends(get_db)):
    stmt = select(Setting)
    res = await db.execute(stmt)
    records = res.scalars().all()
    data = {r.key: r.value for r in records}
    
    max_jobs = int(data.get("max_concurrent_jobs", 1)) if str(data.get("max_concurrent_jobs", "")).isdigit() else 1
    max_ollama = int(data.get("max_concurrent_ollama_requests", 1)) if str(data.get("max_concurrent_ollama_requests", "")).isdigit() else 1
    batch_size = int(data.get("ai_batch_size", 10)) if str(data.get("ai_batch_size", "")).isdigit() else 10

    # Ensure concurrency manager is synced with loaded settings
    concurrency_manager.update_limits(max_jobs, max_ollama)

    # Parse fallback models
    fallback_models: List[str] = []
    raw_fallbacks = data.get("ollama_fallback_models")
    if raw_fallbacks:
        try:
            parsed = json.loads(raw_fallbacks)
            if isinstance(parsed, list):
                fallback_models = [str(m).strip() for m in parsed if str(m).strip()]
        except Exception:
            fallback_models = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]

    if not fallback_models and data.get("ollama_fallback_model"):
        fallback_models = [data["ollama_fallback_model"].strip()]

    if not fallback_models:
        fallback_models = ["Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M"]

    primary_model = data.get("ollama_primary_model", "gemma4:e2b")

    # Return with defaults
    return AppSettings(
        ollama_url=data.get("ollama_url", "http://localhost:11434"),
        ollama_primary_model=primary_model,
        ollama_fallback_models=fallback_models,
        ollama_fallback_model=fallback_models[0],
        ai_batch_size=max(1, batch_size),
        sonarr_url=data.get("sonarr_url", ""),
        sonarr_api_key=data.get("sonarr_api_key", ""),
        tmdb_api_key=data.get("tmdb_api_key", ""),
        tvmaze_api_key=data.get("tvmaze_api_key", ""),
        omdb_api_key=data.get("omdb_api_key", ""),
        subdl_api_key=data.get("subdl_api_key", ""),
        opensubtitles_api_key=data.get("opensubtitles_api_key", ""),
        opensubtitles_user_agent=data.get("opensubtitles_user_agent", "DLarr v0.1"),
        max_concurrent_jobs=max_jobs,
        max_concurrent_ollama_requests=max_ollama
    )


@router.post("", response_model=AppSettings)
async def update_settings(payload: AppSettings, db: AsyncSession = Depends(get_db)):
    # Validate fallback models (never 0)
    clean_fallbacks = [m.strip() for m in payload.ollama_fallback_models if m and m.strip()]
    if not clean_fallbacks:
        clean_fallbacks = ["Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M"]
    
    payload.ollama_fallback_models = clean_fallbacks
    payload.ollama_fallback_model = clean_fallbacks[0]
    payload.ai_batch_size = max(1, payload.ai_batch_size)

    settings_dict = payload.model_dump()
    for k, v in settings_dict.items():
        val_str = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
        stmt = select(Setting).where(Setting.key == k)
        res = await db.execute(stmt)
        record = res.scalars().first()
        if record:
            record.value = val_str
        else:
            db.add(Setting(key=k, value=val_str))
    await db.commit()

    # Dynamically update concurrency manager semaphores
    concurrency_manager.update_limits(payload.max_concurrent_jobs, payload.max_concurrent_ollama_requests)
    return payload


@router.post("/test-connection", response_model=ConnectionTestResponse)
async def test_connection(req: ConnectionTestRequest):
    svc = req.service.lower()
    cfg = req.config

    if svc == "ollama":
        url = cfg.get("ollama_url", "http://localhost:11434")
        res = await OllamaClient.test_connection(url)
        return ConnectionTestResponse(
            service="ollama",
            success=res.get("success", False),
            message=res.get("message", ""),
            available_models=res.get("available_models", []),
            details=res.get("details")
        )

    elif svc == "sonarr":
        url = cfg.get("sonarr_url", "")
        key = cfg.get("sonarr_api_key", "")
        res = await SonarrClient.test_connection(url, key)
        return ConnectionTestResponse(
            service="sonarr",
            success=res.get("success", False),
            message=res.get("message", ""),
            details=res.get("details")
        )

    elif svc == "tmdb":
        key = cfg.get("tmdb_api_key", "")
        res = await TMDBClient.test_connection(key)
        return ConnectionTestResponse(
            service="tmdb",
            success=res.get("success", False),
            message=res.get("message", "")
        )

    elif svc == "tvmaze":
        res = await TVmazeClient.test_connection()
        return ConnectionTestResponse(
            service="tvmaze",
            success=res.get("success", False),
            message=res.get("message", "")
        )

    elif svc == "omdb":
        key = cfg.get("omdb_api_key", "")
        res = await OMDbClient.test_connection(key)
        return ConnectionTestResponse(
            service="omdb",
            success=res.get("success", False),
            message=res.get("message", "")
        )

    elif svc == "subdl":
        key = cfg.get("subdl_api_key", "")
        res = await SubDLClient.test_connection(key)
        return ConnectionTestResponse(
            service="subdl",
            success=res.get("success", False),
            message=res.get("message", "")
        )

    elif svc == "opensubtitles":
        key = cfg.get("opensubtitles_api_key", "")
        ua = cfg.get("opensubtitles_user_agent", "DLarr v0.1")
        res = await OpenSubtitlesClient.test_connection(key, ua)
        return ConnectionTestResponse(
            service="opensubtitles",
            success=res.get("success", False),
            message=res.get("message", "")
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown service: {svc}")
