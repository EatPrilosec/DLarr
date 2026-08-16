from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from backend.app.core.config import settings
from backend.app.core.database import init_db
from backend.app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database schema
    await init_db()

    # 1. Sync concurrency manager with saved settings in DB
    # 2. Automatically resume interrupted RUNNING / PENDING jobs from previous server sessions
    import json
    import asyncio
    from backend.app.core.database import AsyncSessionLocal
    from backend.app.models.setting import Setting
    from backend.app.models.job import Job
    from backend.app.services.concurrency_manager import concurrency_manager
    from sqlalchemy import select
    from datetime import datetime

    async with AsyncSessionLocal() as db:
        try:
            # Load settings
            stmt = select(Setting)
            res = await db.execute(stmt)
            data = {r.key: r.value for r in res.scalars().all()}
            max_jobs = int(data.get("max_concurrent_jobs", 1)) if str(data.get("max_concurrent_jobs", "")).isdigit() else 1
            max_ollama = int(data.get("max_concurrent_ollama_requests", 1)) if str(data.get("max_concurrent_ollama_requests", "")).isdigit() else 1
            concurrency_manager.update_limits(max_jobs, max_ollama)

            # Auto-resume interrupted jobs
            from backend.app.api.v1.endpoints.shows import run_import_pipeline
            stmt_stale = select(Job).where(Job.status.in_(["RUNNING", "PENDING"]))
            res_stale = await db.execute(stmt_stale)
            stale_jobs = res_stale.scalars().all()

            for job in stale_jobs:
                params = {}
                if job.payload:
                    try:
                        params = json.loads(job.payload)
                    except Exception:
                        pass

                sonarr_series_id = params.get("sonarr_series_id")
                if not sonarr_series_id and job.show_id:
                    from backend.app.models.show import Show
                    res_sh = await db.execute(select(Show).where(Show.id == job.show_id))
                    sh = res_sh.scalars().first()
                    if sh:
                        sonarr_series_id = sh.sonarr_series_id

                if sonarr_series_id:
                    job.status = "PENDING"
                    job.progress = 0.0
                    job.message = "Automatically resumed after server restart."
                    job.logs = (job.logs or "") + "\n[SERVER RESTART] Auto-resumed after system restart."
                    job.finished_at = None

                    asyncio.create_task(
                        run_import_pipeline(
                            show_id=params.get("show_id", job.show_id or 0),
                            sonarr_series_id=sonarr_series_id,
                            job_id=job.id,
                            scan_mode=params.get("scan_mode", "full"),
                            sources=params.get("sources", ["tmdb", "tvmaze", "omdb"]),
                            season_number=params.get("season_number"),
                            episode_id=params.get("episode_id")
                        )
                    )
                else:
                    job.status = "CANCELLED"
                    job.message = "Cancelled due to server restart (missing parameters)."
                    job.finished_at = datetime.utcnow()

            await db.commit()
        except Exception:
            pass

    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Health endpoint
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION
    }

# Static file hosting (Frontend SPA)
static_dir = Path(__file__).resolve().parent / "static"

if static_dir.exists() and (static_dir / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        if full_path.startswith("api"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        file_path = static_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
