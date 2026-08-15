import asyncio
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db, AsyncSessionLocal
from backend.app.models.job import Job
from backend.app.schemas.job import JobRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=List[JobRead])
async def list_jobs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    res = await db.execute(stmt)
    return res.scalars().all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Job).where(Job.id == job_id)
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/stream")
async def stream_job_progress(job_id: int):
    """Server-Sent Events (SSE) endpoint to stream live logs and progress to the WebUI."""
    async def event_generator():
        last_log_len = 0
        while True:
            async with AsyncSessionLocal() as db:
                stmt = select(Job).where(Job.id == job_id)
                res = await db.execute(stmt)
                job = res.scalars().first()

                if not job:
                    yield f"data: {{\"error\": \"Job not found\"}}\n\n"
                    break

                logs = job.logs or ""
                new_logs = logs[last_log_len:]
                last_log_len = len(logs)

                payload = {
                    "id": job.id,
                    "status": job.status,
                    "progress": job.progress,
                    "message": job.message,
                    "new_logs": new_logs,
                    "finished": job.status in ("COMPLETED", "FAILED", "CANCELLED")
                }
                import json
                yield f"data: {json.dumps(payload)}\n\n"

                if payload["finished"]:
                    break

            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
