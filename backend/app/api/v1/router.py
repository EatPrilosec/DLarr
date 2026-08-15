from fastapi import APIRouter
from backend.app.api.v1.endpoints import settings, shows, episodes, jobs, audit

api_router = APIRouter()

api_router.include_router(settings.router)
api_router.include_router(shows.router)
api_router.include_router(episodes.router)
api_router.include_router(jobs.router)
api_router.include_router(audit.router)
