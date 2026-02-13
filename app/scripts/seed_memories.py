"""Seed memories from workspace files."""

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from sqlalchemy import select

from app.database import AsyncSessionLocal, init_db
from app.models import Memory


def extract_title_from_content(content: str, filename: str) -> str:
    """Extract title from first # heading or use filename."""
    lines = content.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('# '):
            return line[2:].strip()
    
    # Fallback to filename without extension
    return Path(filename).stem


def extract_date_from_filename(filename: str) -> datetime | None:
    """Extract date from filename like '2026-02-12.md' or '2026-02-12-1234.md'."""
    # Try YYYY-MM-DD.md pattern
    match = re.match(r'(\d{4}-\d{2}-\d{2})\.md$', filename)
    if match:
        try:
            return datetime.strptime(match.group(1), '%Y-%m-%d')
        except ValueError:
            pass
    
    # Try YYYY-MM-DD-HHMM.md pattern (extract just the date part)
    match = re.match(r'(\d{4}-\d{2}-\d{2})-\d{4}\.md$', filename)
    if match:
        try:
            return datetime.strptime(match.group(1), '%Y-%m-%d')
        except ValueError:
            pass
    
    return None


async def seed_memories():
    """Import memory files from workspace."""
    await init_db()
    
    async with AsyncSessionLocal() as db:
        # Check if memories table already has data
        result = await db.execute(select(Memory).limit(1))
        if result.scalar_one_or_none():
            print("Memories table is not empty. Skipping seed.")
            return
        
        workspace_path = Path.home() / ".openclaw" / "workspace"
        imported_count = 0
        
        # Import MEMORY.md (long_term memory)
        memory_md_path = workspace_path / "MEMORY.md"
        if memory_md_path.exists():
            content = memory_md_path.read_text()
            title = extract_title_from_content(content, "MEMORY.md")
            
            memory = Memory(
                path="MEMORY.md",
                title=title,
                content=content,
                memory_type="long_term",
                date=None
            )
            db.add(memory)
            imported_count += 1
            print(f"Imported: MEMORY.md")
        
        # Import daily memories from memory/*.md
        memory_dir = workspace_path / "memory"
        if memory_dir.exists() and memory_dir.is_dir():
            for file_path in sorted(memory_dir.glob("*.md")):
                content = file_path.read_text()
                filename = file_path.name
                title = extract_title_from_content(content, filename)
                date = extract_date_from_filename(filename)
                
                # Determine memory type
                if date:
                    memory_type = "daily"
                else:
                    memory_type = "custom"
                
                memory = Memory(
                    path=f"memory/{filename}",
                    title=title,
                    content=content,
                    memory_type=memory_type,
                    date=date
                )
                db.add(memory)
                imported_count += 1
                print(f"Imported: memory/{filename}")
        
        await db.commit()
        print(f"\nTotal imported: {imported_count} memories")


if __name__ == "__main__":
    asyncio.run(seed_memories())
