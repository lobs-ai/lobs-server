"""Configuration settings for lobs-server."""

import os
from pathlib import Path

class Settings:
    """Application settings."""
    
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/lobs.db")
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DATABASE_PATH}"
    
    # API
    API_PREFIX: str = "/api"
    CORS_ORIGINS: list[str] = ["*"]
    
    # Pagination
    DEFAULT_LIMIT: int = 100
    MAX_LIMIT: int = 1000
    
    # Orchestrator
    ORCHESTRATOR_ENABLED: bool = os.getenv("ORCHESTRATOR_ENABLED", "true").lower() in ("true", "1", "yes")
    ORCHESTRATOR_POLL_INTERVAL: int = int(os.getenv("ORCHESTRATOR_POLL_INTERVAL", "10"))
    ORCHESTRATOR_MAX_WORKERS: int = int(os.getenv("ORCHESTRATOR_MAX_WORKERS", "3"))
    
    def ensure_data_dir(self):
        """Ensure data directory exists."""
        db_path = Path(self.DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

settings = Settings()
