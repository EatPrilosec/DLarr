from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class EpisodeSourceMetadataBase(BaseModel):
    source_name: str
    source_show_id: Optional[str] = None
    source_episode_id: Optional[str] = None
    source_season_number: Optional[int] = None
    source_episode_number: Optional[int] = None
    source_absolute_number: Optional[int] = None
    title: Optional[str] = None
    alternate_titles: Optional[str] = None
    overview: Optional[str] = None
    air_date: Optional[str] = None
    runtime_mins: Optional[int] = None
    has_transcript: bool = False
    transcript_preview: Optional[str] = None
    transcript_full: Optional[str] = None
    subtitle_language: Optional[str] = None
    subtitle_format: Optional[str] = None
    match_method: str = "EXACT_TITLE"
    match_confidence: float = 1.0
    raw_metadata: Optional[str] = None


class EpisodeSourceMetadataRead(EpisodeSourceMetadataBase):
    id: int
    episode_id: int
    show_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EpisodeBase(BaseModel):
    sonarr_episode_id: int
    season_number: int
    episode_number: int
    absolute_episode_number: Optional[int] = None
    title: str
    overview: Optional[str] = None
    air_date: Optional[str] = None
    has_file: bool = False
    monitored: bool = True
    ai_verification_status: str = "PENDING"
    ai_confidence_score: float = 0.0
    ai_audit_notes: Optional[str] = None


class EpisodeRead(EpisodeBase):
    id: int
    show_id: int
    created_at: datetime
    updated_at: datetime
    source_variations: List[EpisodeSourceMetadataRead] = []

    model_config = ConfigDict(from_attributes=True)


class EpisodeUpdateOverride(BaseModel):
    ai_verification_status: Optional[str] = None
    ai_confidence_score: Optional[float] = None
    ai_audit_notes: Optional[str] = None


class ManualMatchRequest(BaseModel):
    source_name: str
    source_episode_id: Optional[str] = None
    source_season_number: Optional[int] = None
    source_episode_number: Optional[int] = None
    title: Optional[str] = None
    overview: Optional[str] = None
    air_date: Optional[str] = None
    raw_metadata: Optional[str] = None


class MarkNoMatchRequest(BaseModel):
    source_name: str
    reason: Optional[str] = "USER_MANUAL_NO_MATCH"
