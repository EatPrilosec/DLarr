from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from backend.app.schemas.episode import EpisodeRead


class ShowBase(BaseModel):
    sonarr_series_id: int
    title: str
    clean_title: Optional[str] = None
    sort_title: Optional[str] = None
    year: Optional[int] = None
    status: Optional[str] = None
    overview: Optional[str] = None
    poster_url: Optional[str] = None
    tvdb_id: Optional[int] = None
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    tvmaze_id: Optional[int] = None
    path: Optional[str] = None
    monitored: bool = True
    audit_status: str = "NOT_AUDITED"


class ShowCreate(BaseModel):
    sonarr_series_id: int


class ShowRead(ShowBase):
    id: int
    last_audited_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    episode_count: Optional[int] = 0
    mapped_sources_summary: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class ShowDetailRead(ShowRead):
    episodes: List[EpisodeRead] = []


class SonarrShowLookup(BaseModel):
    id: int
    title: str
    year: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    tmdb_id: Optional[int] = None
    overview: Optional[str] = None
    poster_url: Optional[str] = None
    episode_count: Optional[int] = 0
    monitored: bool = True
    path: Optional[str] = None
    is_imported: bool = False
