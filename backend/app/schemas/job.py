from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    id: int
    show_id: Optional[int] = None
    job_type: str
    status: str
    progress: float
    message: Optional[str] = None
    logs: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
