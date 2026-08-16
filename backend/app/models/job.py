from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from backend.app.core.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    show_id = Column(Integer, nullable=True, index=True)
    job_type = Column(String(50), nullable=False)  # IMPORT_SHOW, SYNC_METADATA, AI_MATCHING, AUDIT_SHOW
    status = Column(String(50), default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    progress = Column(Float, default=0.0)  # 0.0 to 100.0
    message = Column(String(255), nullable=True)
    logs = Column(Text, nullable=True)  # Detailed log lines
    payload = Column(Text, nullable=True)  # Serialized JSON execution parameters for restart/resume
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
