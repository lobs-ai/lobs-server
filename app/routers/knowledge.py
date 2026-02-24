"""Knowledge endpoints for lobs-shared-memory repository."""

from fastapi import APIRouter, HTTPException
from pathlib import Path
import hashlib
from datetime import datetime, timezone
from typing import Any
import os
import subprocess

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Knowledge base path
KNOWLEDGE_BASE_PATH = Path.home() / "lobs-shared-memory"


def get_content_hash(content: str) -> str:
    """Generate SHA256 hash of content (first 12 chars)."""
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def classify_file_type(path: Path) -> str:
    """Classify file based on directory structure."""
    parts = path.parts
    
    if "research" in parts:
        return "research"
    elif "decisions" in parts or "ADR" in parts:
        return "decision"
    elif "design" in parts:
        return "design"
    elif "docs" in parts:
        return "doc"
    else:
        return "doc"  # Default


def extract_title(path: Path) -> str:
    """Extract title from filename or first heading."""
    try:
        # Try to read first heading from file
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
        
        # Fall back to filename without extension
        return path.stem.replace("-", " ").replace("_", " ").title()
    except Exception:
        return path.stem.replace("-", " ").replace("_", " ").title()


def validate_path(relative_path: str) -> Path:
    """Validate path doesn't escape repository root."""
    # Resolve the path
    full_path = (KNOWLEDGE_BASE_PATH / relative_path).resolve()
    
    # Check it's within the knowledge base
    try:
        full_path.relative_to(KNOWLEDGE_BASE_PATH.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path: outside repository")
    
    return full_path


def build_entry(file_path: Path, relative_path: str) -> dict[str, Any]:
    """Build a knowledge entry from a file."""
    stat = file_path.stat()
    
    # Read content for hash
    try:
        content = file_path.read_text(encoding="utf-8")
        content_hash = get_content_hash(content)
    except Exception:
        content_hash = "error"
    
    return {
        "id": content_hash,
        "path": relative_path,
        "title": extract_title(file_path),
        "type": classify_file_type(file_path),
        "tags": [],
        "summary": None,
        "created_by": None,
        "is_collection": False,
        "parent_path": str(Path(relative_path).parent) if Path(relative_path).parent != Path(".") else None,
        "content_hash": content_hash,
        "file_created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
        "file_updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("")
async def browse_knowledge(
    path: str | None = None,
    type: str | None = None,
    tags: str | None = None,
    search: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Browse and search knowledge entries.
    
    Query params:
    - path: Filter by directory path
    - type: Filter by type (research/decision/doc/design)
    - tags: Filter by tags (comma-separated, not yet implemented)
    - search: Full-text search in filenames (simple substring match)
    - limit: Max entries to return (default 100)
    """
    if not KNOWLEDGE_BASE_PATH.exists():
        raise HTTPException(status_code=503, detail="Knowledge base not found")
    
    # Determine search root
    if path:
        search_root = validate_path(path)
        if not search_root.exists():
            raise HTTPException(status_code=404, detail="Path not found")
    else:
        search_root = KNOWLEDGE_BASE_PATH
    
    # Collect all markdown files
    entries = []
    
    for md_file in search_root.rglob("*.md"):
        # Skip .git directory
        if ".git" in md_file.parts:
            continue
        
        # Get relative path from knowledge base root
        try:
            relative_path = str(md_file.relative_to(KNOWLEDGE_BASE_PATH))
        except ValueError:
            continue
        
        # Apply filters
        file_type = classify_file_type(md_file)
        
        if type and file_type != type:
            continue
        
        if search and search.lower() not in md_file.name.lower():
            continue
        
        # Build entry
        entry = build_entry(md_file, relative_path)
        entries.append(entry)
        
        if len(entries) >= limit:
            break
    
    # Sort by modification time (newest first)
    entries.sort(key=lambda e: e["file_updated_at"], reverse=True)
    
    return {
        "entries": entries[:limit],
        "path": path,
        "total": len(entries),
    }


@router.get("/feed")
async def knowledge_feed(
    limit: int = 50,
    since: str | None = None,
) -> dict[str, Any]:
    """Get recent knowledge entries sorted by modification time.
    
    Query params:
    - limit: Max entries to return (default 50)
    - since: ISO timestamp, only return files modified after this time
    """
    if not KNOWLEDGE_BASE_PATH.exists():
        raise HTTPException(status_code=503, detail="Knowledge base not found")
    
    # Parse since timestamp if provided
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid since timestamp")
    
    # Collect all markdown files with their mtimes
    files_with_mtime = []
    
    for md_file in KNOWLEDGE_BASE_PATH.rglob("*.md"):
        # Skip .git directory
        if ".git" in md_file.parts:
            continue
        
        stat = md_file.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        
        # Apply since filter
        if since_dt and mtime < since_dt:
            continue
        
        files_with_mtime.append((md_file, mtime))
    
    # Sort by mtime descending
    files_with_mtime.sort(key=lambda x: x[1], reverse=True)
    
    # Build entries
    entries = []
    for md_file, _ in files_with_mtime[:limit]:
        try:
            relative_path = str(md_file.relative_to(KNOWLEDGE_BASE_PATH))
            entry = build_entry(md_file, relative_path)
            entries.append(entry)
        except ValueError:
            continue
    
    return {
        "entries": entries,
        "total": len(files_with_mtime),
    }


@router.get("/content")
async def get_content(path: str) -> dict[str, Any]:
    """Read file content.
    
    Query param:
    - path: Relative path from lobs-shared-memory root
    
    Security: Path validation prevents directory traversal.
    """
    if not path:
        raise HTTPException(status_code=400, detail="Path parameter required")
    
    full_path = validate_path(path)
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    
    try:
        content = full_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
    
    return {
        "path": path,
        "content": content,
    }


@router.post("/sync")
async def sync_knowledge() -> dict[str, Any]:
    """Trigger git pull in lobs-shared-memory repository."""
    if not KNOWLEDGE_BASE_PATH.exists():
        raise HTTPException(status_code=503, detail="Knowledge base not found")
    
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=str(KNOWLEDGE_BASE_PATH),
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        return {
            "status": "ok",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git pull timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git pull failed: {e}")
