import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "DLarr"
    APP_VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    PORT: int = 6752
    HOST: str = "0.0.0.0"

    # Storage paths
    DATA_DIR: str = os.getenv("DLARR_DATA_DIR", "/config")
    
    # Auto-fallback to local app directory if /config is not writable / in local dev
    @property
    def resolved_data_dir(self) -> Path:
        p = Path(self.DATA_DIR)
        try:
            p.mkdir(parents=True, exist_ok=True)
            # Test write permissions
            test_file = p / ".write_test"
            test_file.touch()
            test_file.unlink()
            return p
        except Exception:
            local_dir = Path(__file__).resolve().parent.parent.parent / "data"
            local_dir.mkdir(parents=True, exist_ok=True)
            return local_dir

    @property
    def database_url(self) -> str:
        db_path = self.resolved_data_dir / "dlarr.db"
        return f"sqlite+aiosqlite:///{db_path}"

    model_config = SettingsConfigDict(
        env_prefix="DLARR_",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
