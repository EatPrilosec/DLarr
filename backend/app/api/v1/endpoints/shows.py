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
from backend.app.schemas.show import ShowRead, ShowDetailRead, ShowCreate, SonarrShowLookup
from backend.app.services.sonarr_client import SonarrClient
from backend.app.services.matching_engine import MatchingEngine
from backend.app.services.audit_engine import AuditEngine

router = APIRouter(prefix="/shows", tags=["shows"])


async def run_import_pipeline(show_id: int, sonarr_series_id: int, job_id: int):
    """Background task to run multi-source ingestion, Ollama matching, and final audit."""
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
                job.progress = 5.0
                job.message = "Initializing multi-source metadata retrieval..."
                await db.commit()

            # 1. Matching Engine
            show = await MatchingEngine.process_show_ingestion(
                db=db,
                sonarr_series_id=sonarr_series_id,
                config=settings_map,
                job=job
            )

            # 2. Audit Engine
            if job:
                job.progress = 85.0
                job.message = "Running Ollama full-show consistency audit..."
                await db.commit()

            await AuditEngine.audit_show_consistency(
                db=db,
                show_id=show.id,
                config=settings_map,
                job=job
            )

            if job:
                job.status = "COMPLETED"
                job.progress = 100.0
                job.message = "Show ingestion and AI audit completed successfully."
                job.finished_at = datetime.utcnow()
                await db.commit()

        except Exception as e:
            if job:
                job.status = "FAILED"
                job.message = f"Error during ingestion: {str(e)}"
                job.logs = (job.logs or "") + f"\n[FATAL ERROR] {str(e)}"
                job.finished_at = datetime.utcnow()
                await db.commit()


@router.get("", response_model=List[ShowRead])
async def list_shows(db: AsyncSession = Depends(get_db)):
    stmt = select(Show).options(
        selectinload(Show.episodes).selectinload(Episode.source_variations)
    )
    res = await db.execute(stmt)
    shows = res.scalars().all()

    result = []
    for s in shows:
        ep_count = len(s.episodes)
        sources = set()
        for ep in s.episodes:
            for v in ep.source_variations:
                sources.add(v.source_name)

        show_dict = {
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
            "mapped_sources_summary": {"sources": list(sources)}
        }
        result.append(ShowRead(**show_dict))
    return result


@router.get("/sonarr-lookup", response_model=List[SonarrShowLookup])
async def lookup_sonarr_shows(db: AsyncSession = Depends(get_db)):
    """Fetch series list from connected Sonarr instance and flag already-imported ones."""
    stmt = select(Setting)
    res = await db.execute(stmt)
    settings_map = {r.key: r.value for r in res.scalars().all()}

    sonarr_url = settings_map.get("sonarr_url")
    sonarr_key = settings_map.get("sonarr_api_key")
    if not sonarr_url or not sonarr_key:
        raise HTTPException(status_code=400, detail="Sonarr URL or API key is not configured in Settings.")

    try:
        series_list = await SonarrClient.get_series(sonarr_url, sonarr_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from Sonarr: {str(e)}")

    # Get imported show IDs
    stmt_imported = select(Show.sonarr_series_id)
    res_imported = await db.execute(stmt_imported)
    imported_ids = set(res_imported.scalars().all())

    results = []
    for s in series_list:
        poster = None
        for img in s.get("images", []):
            if img.get("coverType") == "poster":
                poster = img.get("remoteUrl") or img.get("url")

        results.append(SonarrShowLookup(
            id=s.get("id"),
            title=s.get("title", "Unknown"),
            year=s.get("year"),
            tvdb_id=s.get("tvdbId"),
            imdb_id=s.get("imdbId"),
            tmdb_id=s.get("tmdbId"),
            overview=s.get("overview"),
            poster_url=poster,
            episode_count=s.get("statistics", {}).get("totalEpisodeCount", 0),
            monitored=s.get("monitored", True),
            path=s.get("path"),
            is_imported=s.get("id") in imported_ids
        ))
    return results


@router.post("/import", response_model=Dict[str, Any])
async def import_show(
    payload: ShowCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Triggers background import and AI matching for a Sonarr show."""
    # Create background job record
    job = Job(
        job_type="IMPORT_SHOW",
        status="PENDING",
        progress=0.0,
        message=f"Queued import for Sonarr series ID {payload.sonarr_series_id}..."
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Launch background task
    background_tasks.add_task(run_import_pipeline, 0, payload.sonarr_series_id, job.id)

    return {
        "success": True,
        "message": f"Show import started in background.",
        "job_id": job.id
    }


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
