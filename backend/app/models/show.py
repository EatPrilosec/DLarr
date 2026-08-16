from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from backend.app.core.database import Base


class Show(Base):
    __tablename__ = "shows"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sonarr_series_id = Column(Integer, unique=True, index=True, nullable=False)
    title = Column(String(255), nullable=False, index=True)
    clean_title = Column(String(255), nullable=True)
    sort_title = Column(String(255), nullable=True)
    year = Column(Integer, nullable=True)
    status = Column(String(50), nullable=True)
    overview = Column(Text, nullable=True)
    poster_url = Column(String(500), nullable=True)
    tvdb_id = Column(Integer, nullable=True, index=True)
    tmdb_id = Column(Integer, nullable=True, index=True)
    imdb_id = Column(String(50), nullable=True, index=True)
    tvmaze_id = Column(Integer, nullable=True, index=True)
    path = Column(String(500), nullable=True)
    monitored = Column(Boolean, default=True)
    
    # AI verification status across the entire show
    audit_status = Column(String(50), default="NOT_AUDITED")  # NOT_AUDITED, PASSED, HAS_WARNINGS, FAILED
    last_audited_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    episodes = relationship("Episode", back_populates="show", cascade="all, delete-orphan", order_by="Episode.season_number, Episode.episode_number")
    source_metadata = relationship("EpisodeSourceMetadata", back_populates="show", cascade="all, delete-orphan")


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    show_id = Column(Integer, ForeignKey("shows.id", ondelete="CASCADE"), nullable=False, index=True)
    sonarr_episode_id = Column(Integer, unique=True, index=True, nullable=False)
    season_number = Column(Integer, nullable=False, index=True)
    episode_number = Column(Integer, nullable=False, index=True)
    absolute_episode_number = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False)
    overview = Column(Text, nullable=True)
    air_date = Column(String(50), nullable=True)
    has_file = Column(Boolean, default=False)
    monitored = Column(Boolean, default=True)

    # AI Matching & Verification Status
    ai_verification_status = Column(String(50), default="PENDING")  # PENDING, EXACT_MATCH, AI_MATCHED, FLAGGED_MISMATCH, NO_MATCH
    ai_confidence_score = Column(Float, default=0.0)
    ai_audit_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    show = relationship("Show", back_populates="episodes")
    source_variations = relationship("EpisodeSourceMetadata", back_populates="episode", cascade="all, delete-orphan")


class EpisodeSourceMetadata(Base):
    __tablename__ = "episode_source_metadata"
    __table_args__ = (UniqueConstraint("episode_id", "source_name", name="uix_episode_source"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    episode_id = Column(Integer, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    show_id = Column(Integer, ForeignKey("shows.id", ondelete="CASCADE"), nullable=False, index=True)
    
    source_name = Column(String(50), nullable=False, index=True)  # sonarr, tmdb, tvmaze, omdb, subdl, opensubtitles
    source_show_id = Column(String(100), nullable=True)
    source_episode_id = Column(String(100), nullable=True)
    
    # Source numbering variations (often differs from Sonarr)
    source_season_number = Column(Integer, nullable=True)
    source_episode_number = Column(Integer, nullable=True)
    source_absolute_number = Column(Integer, nullable=True)

    title = Column(String(255), nullable=True)
    alternate_titles = Column(Text, nullable=True)  # JSON-encoded array of string aliases
    overview = Column(Text, nullable=True)
    air_date = Column(String(50), nullable=True)
    runtime_mins = Column(Integer, nullable=True)

    # Transcript / Subtitle text for content-based matching
    has_transcript = Column(Boolean, default=False)
    transcript_preview = Column(Text, nullable=True)  # First ~500 chars of dialog
    transcript_full = Column(Text, nullable=True)     # Full stripped dialog text
    subtitle_language = Column(String(20), nullable=True)
    subtitle_format = Column(String(20), nullable=True)

    # Match metadata
    match_method = Column(String(50), default="EXACT_TITLE")  # EXACT_TITLE, AI_LLM_CONFIRMED, MANUAL, UNMATCHED
    match_confidence = Column(Float, default=1.0)
    raw_metadata = Column(Text, nullable=True)  # JSON dump of source payload

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    episode = relationship("Episode", back_populates="source_variations")
    show = relationship("Show", back_populates="source_metadata")
