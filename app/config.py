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
    
    def ensure_data_dir(self):
        """Ensure data directory exists."""
        db_path = Path(self.DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

settings = Settings()
