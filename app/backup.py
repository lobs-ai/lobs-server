"""Database backup system with SQLite backups and optional git integration."""

import asyncio
import logging
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class BackupManager:
    """Manages automatic and manual database backups."""
    
    def __init__(self):
        self.backup_dir = Path(settings.BACKUP_DIR)
        self.db_path = Path(settings.DATABASE_PATH)
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        
    async def start(self) -> None:
        """Start the backup scheduler."""
        if not settings.BACKUP_ENABLED:
            logger.info("Backup system disabled via config")
            return
        
        # Ensure backup directory exists
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize git repo if enabled
        if settings.BACKUP_GIT_ENABLED:
            self._init_git_repo()
        
        # Run initial backup
        try:
            await self.create_backup()
            logger.info("Initial backup completed")
        except Exception as e:
            logger.error(f"Initial backup failed: {e}", exc_info=True)
        
        # Start scheduled backups
        self.task = asyncio.create_task(self._backup_loop())
        logger.info(
            f"Backup scheduler started: interval={settings.BACKUP_INTERVAL_HOURS}h, "
            f"retention={settings.BACKUP_RETENTION_COUNT}"
        )
    
    async def stop(self) -> None:
        """Stop the backup scheduler."""
        if self.task:
            self._stop_event.set()
            try:
                await asyncio.wait_for(self.task, timeout=5.0)
            except asyncio.TimeoutError:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            logger.info("Backup scheduler stopped")
    
    async def _backup_loop(self) -> None:
        """Background task that runs backups on schedule."""
        interval_seconds = settings.BACKUP_INTERVAL_HOURS * 3600
        
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval_seconds
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                # Time to run a backup
                try:
                    await self.create_backup()
                except Exception as e:
                    logger.error(f"Scheduled backup failed: {e}", exc_info=True)
    
    async def create_backup(self) -> Path:
        """
        Create a new database backup.
        
        Returns:
            Path to the created backup file
        """
        # Generate backup filename
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        backup_file = self.backup_dir / f"lobs-{timestamp}.db"
        
        # Run backup in thread pool to avoid blocking
        await asyncio.to_thread(self._backup_db, backup_file)
        
        logger.info(f"Database backed up to {backup_file.name}")
        
        # Clean up old backups
        await self._cleanup_old_backups()
        
        # Git commit if enabled
        if settings.BACKUP_GIT_ENABLED:
            await asyncio.to_thread(self._git_commit, backup_file)
        
        return backup_file
    
    def _backup_db(self, backup_file: Path) -> None:
        """
        Perform SQLite backup using the backup API.
        
        This is the most reliable way to backup SQLite while it's in use.
        """
        # Connect to source database
        src_conn = sqlite3.connect(str(self.db_path))
        
        # Connect to backup database
        dst_conn = sqlite3.connect(str(backup_file))
        
        try:
            # Perform backup
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
    
    async def _cleanup_old_backups(self) -> None:
        """Remove old backups beyond retention count."""
        # Get all backup files sorted by modification time (oldest first)
        backups = sorted(
            self.backup_dir.glob("lobs-*.db"),
            key=lambda p: p.stat().st_mtime
        )
        
        # Delete oldest backups if we exceed retention count
        to_delete = len(backups) - settings.BACKUP_RETENTION_COUNT
        if to_delete > 0:
            for backup_file in backups[:to_delete]:
                backup_file.unlink()
                logger.info(f"Deleted old backup: {backup_file.name}")
    
    def _init_git_repo(self) -> None:
        """Initialize git repository in backup directory if not exists."""
        git_dir = self.backup_dir / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(
                    ["git", "init"],
                    cwd=self.backup_dir,
                    check=True,
                    capture_output=True,
                )
                logger.info(f"Initialized git repository in {self.backup_dir}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to init git repo: {e.stderr.decode()}")
    
    def _git_commit(self, backup_file: Path) -> None:
        """Commit backup file to git."""
        try:
            # Add the backup file
            subprocess.run(
                ["git", "add", backup_file.name],
                cwd=self.backup_dir,
                check=True,
                capture_output=True,
            )
            
            # Commit with timestamp
            commit_msg = f"Backup {backup_file.name}"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=self.backup_dir,
                check=True,
                capture_output=True,
            )
            
            logger.info(f"Committed backup to git: {backup_file.name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git commit failed: {e.stderr.decode()}")
    
    async def list_backups(self) -> list[dict]:
        """
        List all available backups.
        
        Returns:
            List of backup info dicts with name, size, and timestamp
        """
        backups = []
        for backup_file in sorted(
            self.backup_dir.glob("lobs-*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True  # Newest first
        ):
            stat = backup_file.stat()
            backups.append({
                "filename": backup_file.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return backups
    
    async def get_status(self) -> dict:
        """
        Get backup system status.
        
        Returns:
            Status dict with last backup time, next scheduled, count, and total size
        """
        backups = await self.list_backups()
        
        total_size = sum(b["size"] for b in backups)
        
        # Calculate next backup time
        next_backup = None
        if self.task and not self.task.done():
            # Estimate based on last backup time and interval
            if backups:
                last_backup_time = datetime.fromisoformat(backups[0]["created_at"])
                interval_seconds = settings.BACKUP_INTERVAL_HOURS * 3600
                next_backup = last_backup_time.timestamp() + interval_seconds
                next_backup = datetime.fromtimestamp(next_backup).isoformat()
        
        return {
            "enabled": settings.BACKUP_ENABLED,
            "backup_count": len(backups),
            "total_size": total_size,
            "last_backup": backups[0]["created_at"] if backups else None,
            "next_backup": next_backup,
            "interval_hours": settings.BACKUP_INTERVAL_HOURS,
            "retention_count": settings.BACKUP_RETENTION_COUNT,
            "git_enabled": settings.BACKUP_GIT_ENABLED,
        }
    
    async def restore_backup(self, filename: str, confirm: bool = False) -> None:
        """
        Restore database from a backup.
        
        Args:
            filename: Backup filename to restore from
            confirm: Must be True to proceed (safety check)
        
        Raises:
            ValueError: If confirm is not True or backup not found
        """
        if not confirm:
            raise ValueError("Must set confirm=true to restore from backup")
        
        backup_file = self.backup_dir / filename
        if not backup_file.exists():
            raise ValueError(f"Backup not found: {filename}")
        
        # Create a backup of current database before restoring
        await self.create_backup()
        
        # Copy backup over current database
        await asyncio.to_thread(self._restore_db, backup_file)
        
        logger.warning(f"Database restored from {filename}")
    
    def _restore_db(self, backup_file: Path) -> None:
        """Copy backup file to database location."""
        import shutil
        shutil.copy2(backup_file, self.db_path)


# Global backup manager instance
backup_manager = BackupManager()
