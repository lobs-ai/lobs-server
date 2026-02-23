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
    
    # Task Improvements roadmap
    DEFAULT_INBOX_PROJECT_ID: str = os.getenv("DEFAULT_INBOX_PROJECT_ID", "inbox")
    GITHUB_SYNC_PUSH_ENABLED: bool = os.getenv("GITHUB_SYNC_PUSH_ENABLED", "false").lower() in ("true", "1", "yes")

    # Backup
    BACKUP_ENABLED: bool = os.getenv("BACKUP_ENABLED", "true").lower() in ("true", "1", "yes")
    BACKUP_INTERVAL_HOURS: int = int(os.getenv("BACKUP_INTERVAL_HOURS", "6"))
    BACKUP_RETENTION_COUNT: int = int(os.getenv("BACKUP_RETENTION_COUNT", "30"))
    BACKUP_DIR: str = os.getenv("BACKUP_DIR", "./data/backups")
    BACKUP_GIT_ENABLED: bool = os.getenv("BACKUP_GIT_ENABLED", "false").lower() in ("true", "1", "yes")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "console").lower()  # "console" or "json"
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    
    # Webhook Security
    MAX_WEBHOOK_PAYLOAD_BYTES: int = int(os.getenv("MAX_WEBHOOK_PAYLOAD_BYTES", "1048576"))  # 1MB
    WEBHOOK_SANITIZE_HTML: bool = os.getenv("WEBHOOK_SANITIZE_HTML", "true").lower() in ("true", "1", "yes")
    
    def ensure_data_dir(self):
        """Ensure data directory exists."""
        db_path = Path(self.DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

settings = Settings()
