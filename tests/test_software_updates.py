"""Tests for software update detection endpoints."""

import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_updates_endpoint_structure(client: AsyncClient):
    """Test that updates endpoint returns valid structure."""
    response = await client.get("/api/status/updates")
    assert response.status_code == 200
    
    data = response.json()
    
    # Check top-level structure
    assert "repos" in data
    assert "has_updates" in data
    assert "checked_at" in data
    assert isinstance(data["repos"], list)
    assert isinstance(data["has_updates"], bool)
    assert isinstance(data["checked_at"], str)


@pytest.mark.asyncio
async def test_updates_repo_info_structure(client: AsyncClient):
    """Test RepoUpdateInfo structure in response."""
    response = await client.get("/api/status/updates")
    assert response.status_code == 200
    
    data = response.json()
    
    # Should have at least lobs-mission-control
    assert len(data["repos"]) >= 1
    
    repo = data["repos"][0]
    
    # Check required fields
    assert "name" in repo
    assert "path" in repo
    assert "local_commit" in repo
    assert "local_message" in repo
    assert "local_date" in repo
    assert "behind" in repo
    assert "ahead" in repo
    assert "has_update" in repo
    assert "branch" in repo
    
    # Check optional fields exist (might be null)
    assert "remote_commit" in repo
    assert "remote_message" in repo
    assert "remote_date" in repo
    assert "error" in repo
    
    # Check types
    assert isinstance(repo["name"], str)
    assert isinstance(repo["path"], str)
    assert isinstance(repo["behind"], int)
    assert isinstance(repo["ahead"], int)
    assert isinstance(repo["has_update"], bool)
    assert isinstance(repo["branch"], str)


@pytest.mark.asyncio
async def test_updates_with_client_commit(client: AsyncClient):
    """Test updates endpoint with client_commit parameter."""
    # This should work even with an invalid commit - it should handle errors gracefully
    response = await client.get("/api/status/updates?client_commit=abc1234")
    assert response.status_code == 200
    
    data = response.json()
    assert "repos" in data
    
    # If the commit doesn't exist, should report an error
    repo = data["repos"][0]
    # Either it resolved the commit, or it reported an error
    assert repo["local_commit"] or repo["error"]


@pytest.mark.asyncio
async def test_updates_fetch_error_handling(client: AsyncClient):
    """Test that fetch errors are properly reported."""
    # Mock _run_git to simulate fetch failure
    from app.routers import status
    
    original_run_git = status._run_git
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # If it's a fetch command, simulate failure
        if "fetch" in args:
            return 1, "fatal: unable to access remote repository"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get("/api/status/updates")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should have an error about fetch failing
        assert repo["error"] is not None
        assert "Fetch failed" in repo["error"]


@pytest.mark.asyncio
async def test_updates_remote_commit_error_handling(client: AsyncClient):
    """Test that missing remote commit is properly reported."""
    from app.routers import status
    
    original_run_git = status._run_git
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # If it's trying to get remote commit, simulate it not existing
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 1, "fatal: ref origin/main not found"
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get("/api/status/updates")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should have an error about not finding origin branch
        assert repo["error"] is not None
        assert "origin/" in repo["error"] or "fetch may have failed" in repo["error"]


@pytest.mark.asyncio
async def test_updates_client_commit_not_found(client: AsyncClient):
    """Test handling when server doesn't know about client commit."""
    from app.routers import status
    
    original_run_git = status._run_git
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Remote commit exists
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 0, "def5678"
        # Client commit doesn't exist
        if "rev-parse" in args and "unknowncommit" in args:
            return 1, "fatal: bad revision 'unknowncommit'"
        # Log commands work
        if "log" in args:
            return 0, "Some commit message"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get("/api/status/updates?client_commit=unknowncommit")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should have an error about client commit not found
        assert repo["error"] is not None
        assert "Client commit not found" in repo["error"] or "unknown commit" in repo["error"]


@pytest.mark.asyncio
async def test_updates_up_to_date(client: AsyncClient):
    """Test response when client is up to date."""
    from app.routers import status
    
    original_run_git = status._run_git
    same_commit = "abc1234567890"
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Both local and remote return same commit
        if "rev-parse" in args:
            return 0, same_commit
        # Log commands
        if "log" in args:
            return 0, "Latest commit"
        # rev-list shows no difference
        if "rev-list" in args:
            return 0, "0\t0"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get(f"/api/status/updates?client_commit={same_commit[:7]}")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should show no updates
        assert repo["has_update"] == False
        assert repo["behind"] == 0
        assert repo["ahead"] == 0
        assert repo["error"] is None


@pytest.mark.asyncio
async def test_updates_behind(client: AsyncClient):
    """Test response when client is behind remote."""
    from app.routers import status
    
    original_run_git = status._run_git
    client_commit = "abc1234"
    remote_commit = "def5678"
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Client commit
        if "rev-parse" in args and client_commit in str(args):
            return 0, client_commit + "567890"
        # Remote commit
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 0, remote_commit + "901234"
        # Log commands
        if "log" in args:
            return 0, "Some commit"
        # rev-list shows 3 commits behind
        if "rev-list" in args:
            return 0, "0\t3"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get(f"/api/status/updates?client_commit={client_commit}")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should show updates available
        assert repo["has_update"] == True
        assert repo["behind"] == 3
        assert repo["ahead"] == 0
        assert repo["error"] is None


@pytest.mark.asyncio
async def test_updates_ahead(client: AsyncClient):
    """Test response when client is ahead of remote."""
    from app.routers import status
    
    original_run_git = status._run_git
    client_commit = "abc1234"
    remote_commit = "def5678"
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Client commit
        if "rev-parse" in args and client_commit in str(args):
            return 0, client_commit + "567890"
        # Remote commit
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 0, remote_commit + "901234"
        # Log commands
        if "log" in args:
            return 0, "Some commit"
        # rev-list shows 2 commits ahead
        if "rev-list" in args:
            return 0, "2\t0"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get(f"/api/status/updates?client_commit={client_commit}")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should show client is ahead
        assert repo["has_update"] == False  # No update to pull
        assert repo["behind"] == 0
        assert repo["ahead"] == 2
        assert repo["error"] is None


@pytest.mark.asyncio
async def test_updates_diverged(client: AsyncClient):
    """Test response when client and remote have diverged."""
    from app.routers import status
    
    original_run_git = status._run_git
    client_commit = "abc1234"
    remote_commit = "def5678"
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Client commit
        if "rev-parse" in args and client_commit in str(args):
            return 0, client_commit + "567890"
        # Remote commit
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 0, remote_commit + "901234"
        # Log commands
        if "log" in args:
            return 0, "Some commit"
        # rev-list shows both ahead and behind
        if "rev-list" in args:
            return 0, "2\t3"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get(f"/api/status/updates?client_commit={client_commit}")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should show divergence
        assert repo["has_update"] == True  # Behind, so update available
        assert repo["behind"] == 3
        assert repo["ahead"] == 2
        assert repo["error"] is None


@pytest.mark.asyncio
async def test_updates_rev_list_failure_fallback(client: AsyncClient):
    """Test fallback behavior when rev-list command fails."""
    from app.routers import status
    
    original_run_git = status._run_git
    client_commit = "abc1234"
    remote_commit = "def5678"
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Commits exist but are different
        if "rev-parse" in args and client_commit in str(args):
            return 0, client_commit + "567890"
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 0, remote_commit + "901234"
        # Log commands
        if "log" in args:
            return 0, "Some commit"
        # rev-list fails
        if "rev-list" in args:
            return 1, "fatal: some error"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get(f"/api/status/updates?client_commit={client_commit}")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should fall back to assuming update exists
        assert repo["has_update"] == True
        assert repo["behind"] == 1  # Fallback value
        assert repo["error"] is None


@pytest.mark.asyncio
async def test_updates_case_insensitive_comparison(client: AsyncClient):
    """Test that commit hash comparison is case-insensitive."""
    from app.routers import status
    
    original_run_git = status._run_git
    commit_lower = "abc1234567890"
    commit_upper = "ABC1234567890"
    
    async def mock_run_git(cwd: str, *args: str, timeout: int = 15):
        # Fetch succeeds
        if "fetch" in args:
            return 0, ""
        # Return uppercase for remote
        if "rev-parse" in args and any("origin/" in arg for arg in args):
            return 0, commit_upper
        # Return lowercase for client
        if "rev-parse" in args:
            return 0, commit_lower
        # Log commands
        if "log" in args:
            return 0, "Same commit"
        # rev-list would show same
        if "rev-list" in args:
            return 0, "0\t0"
        # Otherwise use original
        return await original_run_git(cwd, *args, timeout=timeout)
    
    with patch.object(status, '_run_git', side_effect=mock_run_git):
        response = await client.get(f"/api/status/updates?client_commit={commit_lower[:7]}")
        assert response.status_code == 200
        
        data = response.json()
        repo = data["repos"][0]
        
        # Should recognize as same commit (case-insensitive)
        assert repo["has_update"] == False
        assert repo["behind"] == 0
        assert repo["ahead"] == 0


@pytest.mark.asyncio
async def test_updates_without_client_commit(client: AsyncClient):
    """Test updates endpoint without client_commit parameter (uses server's HEAD)."""
    response = await client.get("/api/status/updates")
    assert response.status_code == 200
    
    data = response.json()
    
    # Should still work, comparing server's HEAD to origin
    assert len(data["repos"]) >= 1
    repo = data["repos"][0]
    
    # Should have commit info (either from server's HEAD or an error)
    assert repo["local_commit"] or repo["error"]


@pytest.mark.asyncio
async def test_updates_not_a_git_repo(client: AsyncClient):
    """Test response when tracked path is not a git repo."""
    from app.routers import status
    
    # Temporarily change tracked path to a non-git directory
    original_repos = status.TRACKED_REPOS.copy()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        status.TRACKED_REPOS = {"test-repo": tmpdir}
        
        try:
            response = await client.get("/api/status/updates")
            assert response.status_code == 200
            
            data = response.json()
            repo = data["repos"][0]
            
            # Should have an error about not being a git repo
            assert repo["error"] is not None
            assert "Not a git repo" in repo["error"]
        finally:
            # Restore original
            status.TRACKED_REPOS = original_repos


@pytest.mark.asyncio
async def test_self_update_endpoint_exists(client: AsyncClient):
    """Test that self-update endpoint exists (actual update logic requires valid repo)."""
    # Just verify the endpoint exists and returns valid response structure
    response = await client.post("/api/status/updates/self-update")
    assert response.status_code == 200
    
    data = response.json()
    
    # Check response structure
    assert "success" in data
    assert "pull_output" in data
    assert "build_output" in data
    assert isinstance(data["success"], bool)
    assert isinstance(data["pull_output"], str)
    assert isinstance(data["build_output"], str)


# Integration test - requires actual git repo
@pytest.mark.integration
@pytest.mark.asyncio
async def test_updates_real_git_operations(client: AsyncClient):
    """Integration test with real git operations (requires actual repo)."""
    # This test will only pass if running in the actual lobs-mission-control repo
    response = await client.get("/api/status/updates")
    assert response.status_code == 200
    
    data = response.json()
    
    # If we're in a real git repo, should have valid data
    if data["repos"] and not data["repos"][0].get("error"):
        repo = data["repos"][0]
        assert repo["local_commit"]
        assert repo["branch"]
        # May or may not have remote data depending on network
