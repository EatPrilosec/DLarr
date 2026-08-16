import json
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.core.database import get_db
from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.schemas.episode import EpisodeRead, EpisodeUpdateOverride, ManualMatchRequest, MarkNoMatchRequest
from backend.app.schemas.show import EpisodeRescanRequest

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.get("/{episode_id}", response_model=EpisodeRead)
async def get_episode_detail(episode_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Episode)
        .where(Episode.id == episode_id)
        .options(selectinload(Episode.source_variations))
    )
    res = await db.execute(stmt)
    ep = res.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")
    return ep


@router.post("/{episode_id}/manual-match", response_model=EpisodeRead)
async def manual_match_episode(
    episode_id: int,
    payload: ManualMatchRequest,
    db: AsyncSession = Depends(get_db)
):
    """Manually assign an external source episode mapping to a canonical Sonarr episode."""
    stmt = (
        select(Episode)
        .where(Episode.id == episode_id)
        .options(selectinload(Episode.source_variations))
    )
    res = await db.execute(stmt)
    ep = res.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    source_norm = payload.source_name.lower().strip()
    stmt_meta = select(EpisodeSourceMetadata).where(
        EpisodeSourceMetadata.episode_id == ep.id,
        EpisodeSourceMetadata.source_name == source_norm
    )
    res_meta = await db.execute(stmt_meta)
    meta = res_meta.scalars().first()

    if not meta:
        meta = EpisodeSourceMetadata(
            episode_id=ep.id,
            show_id=ep.show_id,
            source_name=source_norm,
            source_show_id=None,
            source_episode_id=payload.source_episode_id or f"manual_{ep.id}",
            source_season_number=payload.source_season_number,
            source_episode_number=payload.source_episode_number,
            title=payload.title or ep.title,
            overview=payload.overview or ep.overview,
            air_date=payload.air_date or ep.air_date,
            match_method="MANUAL_MATCH",
            match_confidence=1.0,
            raw_metadata=payload.raw_metadata or json.dumps({"manual": True})
        )
        db.add(meta)
    else:
        meta.source_episode_id = payload.source_episode_id or meta.source_episode_id
        meta.source_season_number = payload.source_season_number
        meta.source_episode_number = payload.source_episode_number
        meta.title = payload.title or meta.title
        meta.overview = payload.overview or meta.overview
        meta.air_date = payload.air_date or meta.air_date
        meta.match_method = "MANUAL_MATCH"
        meta.match_confidence = 1.0

    ep.ai_verification_status = "MANUAL_VERIFIED"
    ep.ai_confidence_score = 100.0
    ep.ai_audit_notes = f"Manually mapped to {payload.source_name} S{payload.source_season_number}E{payload.source_episode_number} by user."

    await db.commit()
    await db.refresh(ep)
    return ep


@router.post("/{episode_id}/mark-no-match", response_model=EpisodeRead)
async def mark_episode_no_match(
    episode_id: int,
    payload: MarkNoMatchRequest,
    db: AsyncSession = Depends(get_db)
):
    """Marks an episode as definitely not existing on a specific external source."""
    stmt = (
        select(Episode)
        .where(Episode.id == episode_id)
        .options(selectinload(Episode.source_variations))
    )
    res = await db.execute(stmt)
    ep = res.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    source_norm = payload.source_name.lower().strip()
    stmt_meta = select(EpisodeSourceMetadata).where(
        EpisodeSourceMetadata.episode_id == ep.id,
        EpisodeSourceMetadata.source_name == source_norm
    )
    res_meta = await db.execute(stmt_meta)
    meta = res_meta.scalars().first()

    if not meta:
        meta = EpisodeSourceMetadata(
            episode_id=ep.id,
            show_id=ep.show_id,
            source_name=source_norm,
            source_episode_id=f"no_match_{ep.id}",
            title="No Matching Episode",
            overview="Marked as not existing on this source provider by user.",
            match_method="NO_MATCH",
            match_confidence=0.0,
            raw_metadata=json.dumps({"reason": payload.reason})
        )
        db.add(meta)
    else:
        meta.match_method = "NO_MATCH"
        meta.match_confidence = 0.0
        meta.title = "No Matching Episode"

    ep.ai_verification_status = "MANUAL_VERIFIED"
    ep.ai_audit_notes = f"Marked as NO_MATCH on {payload.source_name} by user."

    await db.commit()
    await db.refresh(ep)
    return ep


@router.post("/{episode_id}/rescan")
async def rescan_single_episode(
    episode_id: int,
    background_tasks: BackgroundTasks,
    payload: Optional[EpisodeRescanRequest] = None,
    db: AsyncSession = Depends(get_db)
):
    """Triggers a targeted AI rescan for a single episode."""
    from backend.app.models.job import Job
    from backend.app.api.v1.endpoints.shows import run_import_pipeline

    stmt = select(Episode).where(Episode.id == episode_id)
    res = await db.execute(stmt)
    ep = res.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    stmt_show = select(Show).where(Show.id == ep.show_id)
    res_show = await db.execute(stmt_show)
    show = res_show.scalars().first()
    if not show:
        raise HTTPException(status_code=404, detail="Associated show not found")

    selected_sources = payload.sources if payload and payload.sources else ["tmdb", "tvmaze", "omdb"]

    job_payload = {
        "sonarr_series_id": show.sonarr_series_id,
        "show_id": show.id,
        "scan_mode": "full",
        "sources": selected_sources,
        "season_number": ep.season_number,
        "episode_id": ep.id
    }

    job = Job(
        show_id=show.id,
        job_type="AI_MATCHING",
        status="PENDING",
        progress=0.0,
        message=f"Queued rescan for S{ep.season_number:02d}E{ep.episode_number:02d} - '{ep.title}'...",
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
        scan_mode="full",
        sources=selected_sources,
        season_number=ep.season_number,
        episode_id=ep.id
    )

    return {
        "success": True,
        "message": f"Rescan started for S{ep.season_number:02d}E{ep.episode_number:02d}.",
        "job_id": job.id
    }


@router.patch("/{episode_id}", response_model=EpisodeRead)
async def update_episode_status(
    episode_id: int,
    payload: EpisodeUpdateOverride,
    db: AsyncSession = Depends(get_db)
):
    """Allows manual override or verification status update for an episode."""
    stmt = (
        select(Episode)
        .where(Episode.id == episode_id)
        .options(selectinload(Episode.source_variations))
    )
    res = await db.execute(stmt)
    ep = res.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    if payload.ai_verification_status is not None:
        ep.ai_verification_status = payload.ai_verification_status
    if payload.ai_confidence_score is not None:
        ep.ai_confidence_score = payload.ai_confidence_score
    if payload.ai_audit_notes is not None:
        ep.ai_audit_notes = payload.ai_audit_notes

    await db.commit()
    await db.refresh(ep)
    return ep
