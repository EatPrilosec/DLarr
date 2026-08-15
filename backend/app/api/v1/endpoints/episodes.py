from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.core.database import get_db
from backend.app.models.show import Episode, EpisodeSourceMetadata
from backend.app.schemas.episode import EpisodeRead, EpisodeUpdateOverride

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
