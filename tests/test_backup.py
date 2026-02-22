"""Tests for backup API endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path


@pytest.mark.asyncio
async def test_get_backup_status_success(client: AsyncClient):
    """Test getting backup status successfully."""
    with patch("app.routers.backup.backup_manager.get_status") as mock_status:
        mock_status.return_value = {
            "enabled": True,
            "backup_count": 5,
            "total_size": 1024000,
            "last_backup": "2024-02-22T10:00:00Z",
            "next_backup": "2024-02-23T10:00:00Z",
            "interval_hours": 24,
            "retention_count": 7,
            "git_enabled": True,
        }
        
        response = await client.get("/api/backup/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["backup_count"] == 5
        assert data["total_size"] == 1024000
        assert data["interval_hours"] == 24


@pytest.mark.asyncio
async def test_trigger_backup_success(client: AsyncClient):
    """Test triggering a backup successfully."""
    mock_file = MagicMock()
    mock_file.name = "backup_20240222_100000.db"
    
    with patch("app.routers.backup.backup_manager.create_backup") as mock_create:
        mock_create.return_value = mock_file
        
        response = await client.post("/api/backup/trigger")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Backup created successfully"
        assert data["filename"] == "backup_20240222_100000.db"
        mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_backup_failure(client: AsyncClient):
    """Test backup trigger handling errors."""
    with patch("app.routers.backup.backup_manager.create_backup") as mock_create:
        mock_create.side_effect = Exception("Disk full")
        
        response = await client.post("/api/backup/trigger")
        assert response.status_code == 500
        data = response.json()
        assert "Backup failed" in data["detail"]
        assert "Disk full" in data["detail"]


@pytest.mark.asyncio
async def test_list_backups_success(client: AsyncClient):
    """Test listing available backups."""
    with patch("app.routers.backup.backup_manager.list_backups") as mock_list:
        mock_list.return_value = [
            {
                "filename": "backup_20240222_100000.db",
                "size": 512000,
                "created_at": "2024-02-22T10:00:00Z",
            },
            {
                "filename": "backup_20240221_100000.db",
                "size": 511000,
                "created_at": "2024-02-21T10:00:00Z",
            },
        ]
        
        response = await client.get("/api/backup/list")
        assert response.status_code == 200
        data = response.json()
        assert len(data["backups"]) == 2
        assert data["backups"][0]["filename"] == "backup_20240222_100000.db"
        assert data["backups"][0]["size"] == 512000


@pytest.mark.asyncio
async def test_restore_backup_success(client: AsyncClient):
    """Test restoring from a backup with confirmation."""
    with patch("app.routers.backup.backup_manager.restore_backup") as mock_restore:
        mock_restore.return_value = None
        
        response = await client.post("/api/backup/restore/backup_20240222_100000.db?confirm=true")
        assert response.status_code == 200
        data = response.json()
        assert "restored from" in data["message"]
        assert "backup_20240222_100000.db" in data["message"]
        mock_restore.assert_called_once_with("backup_20240222_100000.db", confirm=True)


@pytest.mark.asyncio
async def test_restore_backup_without_confirmation(client: AsyncClient):
    """Test restore fails without confirmation."""
    with patch("app.routers.backup.backup_manager.restore_backup") as mock_restore:
        mock_restore.side_effect = ValueError("Must confirm restore operation")
        
        response = await client.post("/api/backup/restore/backup_20240222_100000.db?confirm=false")
        assert response.status_code == 400
        data = response.json()
        assert "Must confirm" in data["detail"]


@pytest.mark.asyncio
async def test_restore_backup_failure(client: AsyncClient):
    """Test restore handling errors."""
    with patch("app.routers.backup.backup_manager.restore_backup") as mock_restore:
        mock_restore.side_effect = Exception("Corrupted backup file")
        
        response = await client.post("/api/backup/restore/backup_20240222_100000.db?confirm=true")
        assert response.status_code == 500
        data = response.json()
        assert "Restore failed" in data["detail"]
        assert "Corrupted backup" in data["detail"]
