from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db, AsyncSessionLocal
from backend.app.models.show import Show
from backend.app.models.setting import Setting
from backend.app.models.job import Job
from backend.app.services.audit_engine import AuditEngine

router = APIRouter(prefix="/audit", tags=["audit"])


async def run_audit_task(show_id: int, job_id: int):
    async with AsyncSessionLocal() as db:
        # Load config
        stmt = select(Setting)
        res = await db.execute(stmt)
        settings_map = {r.key: r.value for r in res.scalars().all()}

        job_stmt = select(Job).where(Job.id == job_id)
        job_res = await db.execute(job_stmt)
        job = job_res.scalars().first()

        try:
            if job:
                job.status = "RUNNING"
                job.progress = 20.0
                job.message = "Running full-show consistency audit..."
                await db.commit()

            result = await AuditEngine.audit_show_consistency(
                db=db,
                show_id=show_id,
                config=settings_map,
                job=job
            )

            if job:
                job.status = "COMPLETED"
                job.progress = 100.0
                job.message = f"Audit completed: {result.get('status')} ({result.get('flagged_count')} flagged)"
                await db.commit()
        except Exception as e:
            if job:
                job.status = "FAILED"
                job.message = f"Audit failed: {str(e)}"
                await db.commit()


@router.post("/{show_id}", response_model=Dict[str, Any])
async def trigger_show_audit(
    show_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Show).where(Show.id == show_id)
    res = await db.execute(stmt)
    show = res.scalars().first()
    if not show:
        raise HTTPException(status_code=404, detail="Show not found")

    job = Job(
        show_id=show.id,
        job_type="AUDIT_SHOW",
        status="PENDING",
        progress=0.0,
        message=f"Queued AI audit for '{show.title}'..."
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(run_audit_task, show.id, job.id)

    return {
        "success": True,
        "message": f"AI audit started for '{show.title}'",
        "job_id": job.id
    }
