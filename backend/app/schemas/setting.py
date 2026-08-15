from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class SettingItem(BaseModel):
    key: str
    value: str
    description: Optional[str] = None


class AppSettings(BaseModel):
    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_primary_model: str = "llama3.1:8b"
    ollama_fallback_model: str = "mistral:7b"
    
    # Sonarr
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    
    # Metadata Providers
    tmdb_api_key: str = ""
    tvmaze_api_key: str = ""
    omdb_api_key: str = ""
    
    # Transcript Providers
    subdl_api_key: str = ""
    opensubtitles_api_key: str = ""
    opensubtitles_user_agent: str = "DLarr v0.1"


class ConnectionTestRequest(BaseModel):
    service: str  # ollama, sonarr, tmdb, tvmaze, omdb, subdl, opensubtitles
    config: Dict[str, Any]


class ConnectionTestResponse(BaseModel):
    service: str
    success: bool
    message: str
    details: Optional[Dict[str, Any]] = None
    available_models: Optional[List[str]] = None
