"""Backup management API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.backup import backup_manager

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupTriggerResponse(BaseModel):
    """Response for backup trigger."""
    message: str
    filename: str


class BackupStatusResponse(BaseModel):
    """Response for backup status."""
    enabled: bool
    backup_count: int
    total_size: int
    last_backup: str | None
    next_backup: str | None
    interval_hours: int
    retention_count: int
    git_enabled: bool


class BackupInfo(BaseModel):
    """Info about a single backup."""
    filename: str
    size: int
    created_at: str


class BackupListResponse(BaseModel):
    """Response for backup list."""
    backups: list[BackupInfo]


class BackupRestoreResponse(BaseModel):
    """Response for backup restore."""
    message: str


@router.get("/status", response_model=BackupStatusResponse)
async def get_backup_status():
    """
    Get backup system status.
    
    Returns last backup time, next scheduled backup, backup count, and total size.
    """
    status = await backup_manager.get_status()
    return status


@router.post("/trigger", response_model=BackupTriggerResponse)
async def trigger_backup():
    """
    Trigger an immediate backup.
    
    Creates a new backup outside the regular schedule.
    """
    try:
        backup_file = await backup_manager.create_backup()
        return {
            "message": "Backup created successfully",
            "filename": backup_file.name,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {str(e)}")


@router.get("/list", response_model=BackupListResponse)
async def list_backups():
    """
    List all available backups.
    
    Returns backups sorted by creation time (newest first) with filenames,
    sizes, and timestamps.
    """
    backups = await backup_manager.list_backups()
    return {"backups": backups}


@router.post("/restore/{filename}", response_model=BackupRestoreResponse)
async def restore_backup(filename: str, confirm: bool = False):
    """
    Restore database from a backup.
    
    **WARNING:** This will replace the current database with the backup.
    A backup of the current database is automatically created before restoring.
    
    Args:
        filename: Name of the backup file to restore from
        confirm: Must be set to true to proceed (safety check)
    """
    try:
        await backup_manager.restore_backup(filename, confirm=confirm)
        return {
            "message": f"Database restored from {filename}. Server restart recommended.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {str(e)}")
