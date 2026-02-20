"""Daily memory maintenance for all OpenClaw agent workspaces.

Audits and cleans up agent memory files according to OpenClaw best practices:
- memory/ files should be YYYY-MM-DD.md dated daily logs
- Non-dated files in memory/ get consolidated into MEMORY.md
- Stale spawn/autoassign sessions get cleaned up
- Cross-agent memory propagation: important info from main workspace
  gets injected into worker agent MEMORY.md files

Integrates with memory_sync.py for DB consistency after file changes.

Runs as a daily phase in the control loop and as a RoutineRunner hook.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Agent workspace paths
HOME = Path.home()
AGENT_WORKSPACES = {
    "main": HOME / ".openclaw" / "workspace",
    "programmer": HOME / ".openclaw" / "workspace-programmer",
    "researcher": HOME / ".openclaw" / "workspace-researcher",
    "architect": HOME / ".openclaw" / "workspace-architect",
    "reviewer": HOME / ".openclaw" / "workspace-reviewer",
    "writer": HOME / ".openclaw" / "workspace-writer",
}

WORKER_AGENTS = [k for k in AGENT_WORKSPACES if k != "main"]

SESSIONS_BASE = HOME / ".openclaw" / "agents"

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
MAX_SESSION_AGE_HOURS = 1

# Sections in main MEMORY.md that should propagate to all worker agents.
# These are headers whose content gets injected into worker MEMORY.md
# under a "## Shared Context (from Lobs)" section.
PROPAGATION_HEADERS = [
    "## Architecture Overview",
    "## Repos & Projects",
    "## Key Decisions & Notes",
    "## Rafe",
]


def _is_dated_file(filename: str) -> bool:
    """Check if a filename starts with YYYY-MM-DD."""
    return bool(DATE_PATTERN.match(filename))


def audit_workspace(workspace_path: Path) -> dict[str, Any]:
    """Audit a single workspace for memory best practices."""
    report: dict[str, Any] = {
        "workspace": str(workspace_path),
        "exists": workspace_path.exists(),
        "non_dated_files": [],
        "daily_file_count": 0,
        "memory_md_lines": 0,
        "actions_taken": [],
    }

    if not workspace_path.exists():
        return report

    memory_dir = workspace_path / "memory"
    memory_md = workspace_path / "MEMORY.md"

    if memory_md.exists():
        lines = memory_md.read_text().splitlines()
        report["memory_md_lines"] = len(lines)

    if memory_dir.exists():
        for f in sorted(memory_dir.glob("*.md")):
            if not _is_dated_file(f.name):
                report["non_dated_files"].append(f.name)
            else:
                report["daily_file_count"] += 1

    return report


def consolidate_non_dated_files(workspace_path: Path) -> list[str]:
    """Move content from non-dated memory/ files into MEMORY.md, then delete them.

    Returns list of files consolidated.
    """
    memory_dir = workspace_path / "memory"
    memory_md = workspace_path / "MEMORY.md"
    consolidated = []

    if not memory_dir.exists():
        return consolidated

    non_dated = [f for f in sorted(memory_dir.glob("*.md")) if not _is_dated_file(f.name)]
    if not non_dated:
        return consolidated

    existing_content = ""
    if memory_md.exists():
        existing_content = memory_md.read_text()

    additions = []
    for f in non_dated:
        content = f.read_text().strip()
        if not content:
            f.unlink()
            consolidated.append(f.name)
            continue

        section_name = f.stem.replace("-", " ").replace("_", " ").title()
        additions.append(f"\n## {section_name}\n\n{content}\n")
        consolidated.append(f.name)
        f.unlink()

    if additions:
        new_content = existing_content.rstrip() + "\n" + "\n".join(additions)
        memory_md.write_text(new_content)

    return consolidated


def cleanup_stale_sessions() -> dict[str, Any]:
    """Remove stale spawn/autoassign sessions from all agents."""
    report: dict[str, Any] = {"agents_cleaned": {}, "total_removed": 0}
    now_ms = time.time() * 1000

    if not SESSIONS_BASE.exists():
        return report

    for agent_dir in SESSIONS_BASE.iterdir():
        if not agent_dir.is_dir():
            continue

        sessions_file = agent_dir / "sessions" / "sessions.json"
        if not sessions_file.exists():
            continue

        try:
            data = json.loads(sessions_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        cleaned = {}
        removed = 0

        for key, val in data.items():
            is_spawn = "spawn-" in key or "autoassign-" in key
            updated = val.get("updatedAt", 0)
            age_hours = (now_ms - updated) / 3600000

            if is_spawn and age_hours > MAX_SESSION_AGE_HOURS:
                removed += 1
                sid = val.get("sessionId", "")
                if sid:
                    for transcript in (agent_dir / "sessions").glob(f"{sid}*.jsonl"):
                        try:
                            transcript.unlink()
                        except OSError:
                            pass
            else:
                cleaned[key] = val

        if removed > 0:
            sessions_file.write_text(json.dumps(cleaned, indent=2))
            report["agents_cleaned"][agent_dir.name] = removed
            report["total_removed"] += removed

    return report


def _extract_sections(content: str, headers: list[str]) -> dict[str, str]:
    """Extract named sections from markdown content.

    Returns a dict of header -> section content (including the header line).
    """
    sections: dict[str, str] = {}
    lines = content.splitlines()

    for target_header in headers:
        target_level = target_header.count("#")
        in_section = False
        section_lines: list[str] = []

        for line in lines:
            if line.strip().startswith("#"):
                # Check if this is our target header
                if line.strip() == target_header or line.strip().startswith(target_header):
                    in_section = True
                    section_lines = [line]
                    continue
                # Check if we hit another header at same or higher level
                if in_section:
                    level = len(line.strip()) - len(line.strip().lstrip("#"))
                    if level <= target_level:
                        in_section = False
                        continue
            if in_section:
                section_lines.append(line)

        if section_lines:
            sections[target_header] = "\n".join(section_lines)

    return sections


def propagate_shared_context() -> dict[str, Any]:
    """Propagate important sections from main MEMORY.md to worker agents.

    Reads designated sections from the main workspace MEMORY.md and injects
    them into each worker agent's MEMORY.md under a managed
    "## Shared Context (from Lobs)" section.

    This section is fully replaced on each run so it stays current.
    """
    report: dict[str, Any] = {"agents_updated": [], "skipped": [], "errors": []}

    main_memory = AGENT_WORKSPACES["main"] / "MEMORY.md"
    if not main_memory.exists():
        report["skipped"].append("main MEMORY.md not found")
        return report

    main_content = main_memory.read_text()
    sections = _extract_sections(main_content, PROPAGATION_HEADERS)

    if not sections:
        report["skipped"].append("no propagation sections found in main MEMORY.md")
        return report

    # Build the shared context block
    shared_block_parts = [
        "## Shared Context (from Lobs)",
        "",
        f"*Auto-propagated on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        "Do not edit — this section is overwritten daily by memory maintenance.*",
        "",
    ]
    for header, content in sections.items():
        shared_block_parts.append(content)
        shared_block_parts.append("")

    shared_block = "\n".join(shared_block_parts).rstrip() + "\n"

    SHARED_MARKER_START = "## Shared Context (from Lobs)"
    SHARED_MARKER_END_PATTERN = re.compile(
        r"^## (?!Shared Context \(from Lobs\))", re.MULTILINE
    )

    for agent in WORKER_AGENTS:
        ws = AGENT_WORKSPACES[agent]
        if not ws.exists():
            report["skipped"].append(agent)
            continue

        memory_md = ws / "MEMORY.md"
        try:
            existing = memory_md.read_text() if memory_md.exists() else ""

            # Remove old shared context section if present
            if SHARED_MARKER_START in existing:
                start_idx = existing.index(SHARED_MARKER_START)
                # Find the next top-level header after our section
                rest = existing[start_idx + len(SHARED_MARKER_START):]
                match = SHARED_MARKER_END_PATTERN.search(rest)
                if match:
                    end_idx = start_idx + len(SHARED_MARKER_START) + match.start()
                    existing = existing[:start_idx].rstrip() + "\n\n" + existing[end_idx:]
                else:
                    # Our section goes to end of file
                    existing = existing[:start_idx].rstrip()

            # Append shared block at the end
            new_content = existing.rstrip() + "\n\n" + shared_block
            memory_md.write_text(new_content)
            report["agents_updated"].append(agent)

        except Exception as e:
            report["errors"].append(f"{agent}: {e}")
            logger.error("Failed to propagate to %s: %s", agent, e, exc_info=True)

    return report


async def run_memory_maintenance(routine=None) -> dict[str, Any]:
    """Full memory maintenance pass across all agent workspaces.

    Entry point for RoutineRunner hook or direct call from control loop.
    """
    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workspaces": {},
        "propagation": {},
        "sessions": {},
        "summary": {},
    }

    total_consolidated = 0

    for agent_name, ws_path in AGENT_WORKSPACES.items():
        if not ws_path.exists():
            continue

        audit = audit_workspace(ws_path)
        consolidated = consolidate_non_dated_files(ws_path)
        total_consolidated += len(consolidated)

        results["workspaces"][agent_name] = {
            "audit": audit,
            "consolidated": consolidated,
        }

    # Propagate shared context from main to workers
    prop_report = propagate_shared_context()
    results["propagation"] = prop_report

    # Clean stale sessions
    session_report = cleanup_stale_sessions()
    results["sessions"] = session_report

    # Trigger memory_sync if available (keeps DB in sync with filesystem)
    try:
        from app.database import AsyncSessionLocal
        from app.services.memory_sync import sync_agent_memories

        async with AsyncSessionLocal() as db:
            sync_result = await sync_agent_memories(db)
            await db.commit()
            results["memory_sync"] = sync_result
    except Exception as e:
        logger.warning("Memory sync after maintenance failed (non-fatal): %s", e)
        results["memory_sync"] = {"error": str(e)}

    results["summary"] = {
        "workspaces_audited": len([w for w in AGENT_WORKSPACES.values() if w.exists()]),
        "files_consolidated": total_consolidated,
        "agents_propagated": len(prop_report.get("agents_updated", [])),
        "sessions_removed": session_report["total_removed"],
    }

    logger.info(
        "[MEMORY_MAINTENANCE] Complete: consolidated=%d propagated=%d sessions=%d",
        total_consolidated,
        len(prop_report.get("agents_updated", [])),
        session_report["total_removed"],
    )

    return results
