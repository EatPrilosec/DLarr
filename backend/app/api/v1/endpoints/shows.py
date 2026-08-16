import json
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from backend.app.core.database import get_db, AsyncSessionLocal
from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.models.setting import Setting
from backend.app.models.job import Job
from backend.app.schemas.show import (
    ShowRead, ShowDetailRead, ShowCreate, SonarrShowLookup,
    ShowImportRequest, ShowRescanRequest
)
from backend.app.services.sonarr_client import SonarrClient
from backend.app.services.matching_engine import MatchingEngine
from backend.app.services.audit_engine import AuditEngine

router = APIRouter(prefix="/shows", tags=["shows"])


async def run_import_pipeline(
    show_id: int,
    sonarr_series_id: int,
    job_id: int,
    scan_mode: str = "full",
    sources: Optional[List[str]] = None,
    season_number: Optional[int] = None,
    episode_id: Optional[int] = None
):
    """Background task to run multi-source ingestion and Ollama matching with concurrency control."""
    from backend.app.services.concurrency_manager import concurrency_manager

    concurrency_manager.register_task(job_id, asyncio.current_task())
    try:
        async with concurrency_manager.job_slot():
            if concurrency_manager.is_cancelled(job_id):
                return

            async with AsyncSessionLocal() as db:
                # Load config
                stmt = select(Setting)
                res = await db.execute(stmt)
                settings_map = {r.key: r.value for r in res.scalars().all()}

                # Load job
                job_stmt = select(Job).where(Job.id == job_id)
                job_res = await db.execute(job_stmt)
                job = job_res.scalars().first()

                try:
                    if job:
                        job.status = "RUNNING"
                        job.progress = 2.0
                        job.message = "Initializing show metadata..."
                        await db.commit()

                    # Execute Matching Engine
                    show = await MatchingEngine.process_show_ingestion(
                        db=db,
                        sonarr_series_id=sonarr_series_id,
                        config=settings_map,
                        job=job,
                        scan_mode=scan_mode,
                        selected_sources=sources,
                        target_season_number=season_number,
                        target_episode_id=episode_id
                    )

                    if job and not concurrency_manager.is_cancelled(job_id):
                        job.status = "COMPLETED"
                        job.progress = 100.0
                        job.message = "Show ingestion and matching completed successfully."
                        job.finished_at = datetime.utcnow()
                        await db.commit()

                except asyncio.CancelledError:
                    if job:
                        job.status = "CANCELLED"
                        job.message = "Job cancelled by user."
                        job.logs = (job.logs or "") + "\n[JOB CANCELLED] Cancelled."
                        job.finished_at = datetime.utcnow()
                        await db.commit()
                except Exception as e:
                    if job:
                        job.status = "FAILED"
                        job.message = f"Error during ingestion: {str(e)}"
                        job.logs = (job.logs or "") + f"\n[FATAL ERROR] {str(e)}"
                        job.finished_at = datetime.utcnow()
                        await db.commit()
    finally:
        concurrency_manager.unregister_task(job_id)


@router.get("", response_model=List[ShowRead])
async def list_shows(db: AsyncSession = Depends(get_db)):
    stmt = select(Show).options(
        selectinload(Show.episodes).selectinload(Episode.source_variations)
    ).order_by(Show.title)
    res = await db.execute(stmt)
    shows = res.scalars().all()

    result = []
    for s in shows:
        ep_count = len(s.episodes)
        matched_sources: Dict[str, int] = {}
        for ep in s.episodes:
            for v in ep.source_variations:
                if v.match_method not in ("NONE", "NO_MATCH"):
                    matched_sources[v.source_name] = matched_sources.get(v.source_name, 0) + 1

        s_dict = {
            "id": s.id,
            "sonarr_series_id": s.sonarr_series_id,
            "title": s.title,
            "clean_title": s.clean_title,
            "sort_title": s.sort_title,
            "year": s.year,
            "status": s.status,
            "overview": s.overview,
            "poster_url": s.poster_url,
            "tvdb_id": s.tvdb_id,
            "tmdb_id": s.tmdb_id,
            "imdb_id": s.imdb_id,
            "tvmaze_id": s.tvmaze_id,
            "path": s.path,
            "monitored": s.monitored,
            "audit_status": s.audit_status,
            "last_audited_at": s.last_audited_at,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "episode_count": ep_count,
            "mapped_sources_summary": matched_sources
        }
        result.append(s_dict)
    return result


@router.get("/lookup", response_model=List[SonarrShowLookup])
async def lookup_sonarr_shows(db: AsyncSession = Depends(get_db)):
    """Fetches all series from the user's configured Sonarr instance."""
    stmt = select(Setting)
    res = await db.execute(stmt)
    settings_map = {r.key: r.value for r in res.scalars().all()}

    sonarr_url = settings_map.get("sonarr_url")
    sonarr_key = settings_map.get("sonarr_api_key")

    if not sonarr_url or not sonarr_key:
        raise HTTPException(
            status_code=400,
            detail="Sonarr URL and API Key must be configured in Settings first."
        )

    sonarr_series = await SonarrClient.get_series(sonarr_url, sonarr_key)

    stmt_imported = select(Show.sonarr_series_id)
    res_imported = await db.execute(stmt_imported)
    imported_ids = set(res_imported.scalars().all())

    results = []
    for s in sonarr_series:
        s_id = s.get("id")
        poster = None
        for img in s.get("images", []):
            if img.get("coverType") == "poster":
                poster = img.get("remoteUrl") or img.get("url")

        results.append(SonarrShowLookup(
            id=s_id,
            title=s.get("title", "Unknown"),
            year=s.get("year"),
            tvdb_id=s.get("tvdbId"),
            imdb_id=s.get("imdbId"),
            tmdb_id=s.get("tmdbId"),
            overview=s.get("overview"),
            poster_url=poster,
            episode_count=s.get("statistics", {}).get("episodeCount", 0),
            monitored=s.get("monitored", True),
            path=s.get("path"),
            is_imported=(s_id in imported_ids)
        ))

    return results


@router.post("/import")
async def import_show(
    payload: ShowImportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Triggers background import and AI matching for a Sonarr show with scan options."""
    job_payload = {
        "sonarr_series_id": payload.sonarr_series_id,
        "show_id": 0,
        "scan_mode": payload.scan_mode or "full",
        "sources": payload.sources or ["tmdb", "tvmaze", "omdb"],
        "season_number": None,
        "episode_id": None
    }

    job = Job(
        job_type="IMPORT_SHOW",
        status="PENDING",
        progress=0.0,
        message=f"Queued import for Sonarr series ID {payload.sonarr_series_id} ({payload.scan_mode} scan)...",
        payload=json.dumps(job_payload)
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(
        run_import_pipeline,
        show_id=0,
        sonarr_series_id=payload.sonarr_series_id,
        job_id=job.id,
        scan_mode=payload.scan_mode or "full",
        sources=payload.sources or ["tmdb", "tvmaze", "omdb"]
    )

    return {
        "success": True,
        "message": "Show import started in background.",
        "job_id": job.id
    }


@router.post("/{show_id}/rescan")
async def rescan_show(
    show_id: int,
    payload: ShowRescanRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Triggers a rescan of an existing show with configurable scan options."""
    stmt = select(Show).where(Show.id == show_id)
    res = await db.execute(stmt)
    show = res.scalars().first()
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    job_payload = {
        "sonarr_series_id": show.sonarr_series_id,
        "show_id": show.id,
        "scan_mode": payload.scan_mode or "full",
        "sources": payload.sources or ["tmdb", "tvmaze", "omdb"],
        "season_number": payload.season_number,
        "episode_id": payload.episode_id
    }

    scope_str = "Full Show"
    if payload.episode_id is not None:
        scope_str = f"Episode #{payload.episode_id}"
    elif payload.season_number is not None:
        scope_str = f"Season {payload.season_number}"

    job = Job(
        show_id=show.id,
        job_type="AI_MATCHING",
        status="PENDING",
        progress=0.0,
        message=f"Queued rescan for '{show.title}' [{scope_str}]...",
        payload=json.dumps(job_payload)
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(
        run_import_pipeline,
        show_id=show.id,
        sonarr_series_id=show.sonarr_series_id,
        job_id=job.id,
        scan_mode=payload.scan_mode or "full",
        sources=payload.sources or ["tmdb", "tvmaze", "omdb"],
        season_number=payload.season_number,
        episode_id=payload.episode_id
    )

    return {
        "success": True,
        "message": f"Rescan started for '{show.title}'.",
        "job_id": job.id
    }


@router.post("/{show_id}/seasons/{season_number}/rescan")
async def rescan_season(
    show_id: int,
    season_number: int,
    payload: ShowRescanRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Triggers a rescan for a specific season of an existing show."""
    payload.season_number = season_number
    return await rescan_show(show_id, payload, background_tasks, db)


@router.get("/{show_id}", response_model=ShowDetailRead)
async def get_show_detail(show_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Show)
        .where(Show.id == show_id)
        .options(
            selectinload(Show.episodes).selectinload(Episode.source_variations)
        )
    )
    res = await db.execute(stmt)
    show = res.scalars().first()
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")
    return show


@router.delete("/{show_id}")
async def delete_show(show_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import delete
    stmt = select(Show).where(Show.id == show_id)
    res = await db.execute(stmt)
    show = res.scalars().first()
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    await db.execute(delete(EpisodeSourceMetadata).where(EpisodeSourceMetadata.show_id == show_id))
    await db.execute(delete(Episode).where(Episode.show_id == show_id))
    await db.delete(show)
    await db.commit()
    return {"success": True, "message": f"Show '{show.title}' deleted"}
