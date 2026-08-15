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

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
async def get_settings(db: AsyncSession = Depends(get_db)):
    stmt = select(Setting)
    res = await db.execute(stmt)
    records = res.scalars().all()
    data = {r.key: r.value for r in records}
    
    # Return with defaults
    return AppSettings(
        ollama_url=data.get("ollama_url", "http://localhost:11434"),
        ollama_primary_model=data.get("ollama_primary_model", "llama3.1:8b"),
        ollama_fallback_model=data.get("ollama_fallback_model", "mistral:7b"),
        sonarr_url=data.get("sonarr_url", ""),
        sonarr_api_key=data.get("sonarr_api_key", ""),
        tmdb_api_key=data.get("tmdb_api_key", ""),
        tvmaze_api_key=data.get("tvmaze_api_key", ""),
        omdb_api_key=data.get("omdb_api_key", ""),
        subdl_api_key=data.get("subdl_api_key", ""),
        opensubtitles_api_key=data.get("opensubtitles_api_key", ""),
        opensubtitles_user_agent=data.get("opensubtitles_user_agent", "DLarr v0.1")
    )


@router.post("", response_model=AppSettings)
async def update_settings(payload: AppSettings, db: AsyncSession = Depends(get_db)):
    settings_dict = payload.model_dump()
    for k, v in settings_dict.items():
        stmt = select(Setting).where(Setting.key == k)
        res = await db.execute(stmt)
        record = res.scalars().first()
        if record:
            record.value = str(v)
        else:
            db.add(Setting(key=k, value=str(v)))
    await db.commit()
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
