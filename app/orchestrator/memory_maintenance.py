"""Daily memory maintenance for all OpenClaw agent workspaces.

Two-phase approach:
1. **Deterministic cleanup** (no LLM): session cleanup, propagate shared context
2. **Intelligent curation** (LLM worker): review each agent's memory files,
   curate MEMORY.md to be light and high-signal, leave source files intact

Philosophy:
- MEMORY.md should be small, curated, high-signal — it loads every session
- memory/*.md files (dated or not) are source material — never deleted
- Important info gets extracted INTO MEMORY.md; originals stay as-is
- Cross-agent propagation: main workspace shares key context with workers

Integrates with memory_sync.py for DB consistency after file changes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

logger = logging.getLogger(__name__)

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

MAX_SESSION_AGE_HOURS = 1

# Sections in main MEMORY.md to propagate to all worker agents.
PROPAGATION_HEADERS = [
    "## Architecture Overview",
    "## Repos & Projects",
    "## Key Decisions & Notes",
    "## Rafe",
]

# Threshold above which we consider MEMORY.md potentially bloated and worth curating.
# This is NOT a target — the curator keeps all important info regardless of length.
CURATION_THRESHOLD_LINES = 500


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
    """Extract named sections from markdown content."""
    sections: dict[str, str] = {}
    lines = content.splitlines()

    for target_header in headers:
        target_level = target_header.count("#")
        in_section = False
        section_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                if stripped == target_header or stripped.startswith(target_header):
                    in_section = True
                    section_lines = [line]
                    continue
                if in_section:
                    level = len(stripped) - len(stripped.lstrip("#"))
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

    Injects under a managed "## Shared Context (from Lobs)" section
    that is fully replaced on each run.
    """
    report: dict[str, Any] = {"agents_updated": [], "skipped": [], "errors": []}

    main_memory = AGENT_WORKSPACES["main"] / "MEMORY.md"
    if not main_memory.exists():
        report["skipped"].append("main MEMORY.md not found")
        return report

    main_content = main_memory.read_text()
    sections = _extract_sections(main_content, PROPAGATION_HEADERS)

    if not sections:
        report["skipped"].append("no propagation sections found")
        return report

    shared_block_parts = [
        "## Shared Context (from Lobs)",
        "",
        f"*Auto-propagated on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        "Do not edit — overwritten daily by memory maintenance.*",
        "",
    ]
    for _header, content in sections.items():
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

            if SHARED_MARKER_START in existing:
                start_idx = existing.index(SHARED_MARKER_START)
                rest = existing[start_idx + len(SHARED_MARKER_START):]
                match = SHARED_MARKER_END_PATTERN.search(rest)
                if match:
                    end_idx = start_idx + len(SHARED_MARKER_START) + match.start()
                    existing = existing[:start_idx].rstrip() + "\n\n" + existing[end_idx:]
                else:
                    existing = existing[:start_idx].rstrip()

            new_content = existing.rstrip() + "\n\n" + shared_block
            memory_md.write_text(new_content)
            report["agents_updated"].append(agent)

        except Exception as e:
            report["errors"].append(f"{agent}: {e}")
            logger.error("Failed to propagate to %s: %s", agent, e, exc_info=True)

    return report


def _gather_memory_inventory(workspace_path: Path) -> dict[str, Any]:
    """Gather inventory of all memory files in a workspace for the curator."""
    inventory: dict[str, Any] = {"memory_md": None, "memory_files": []}

    memory_md = workspace_path / "MEMORY.md"
    if memory_md.exists():
        content = memory_md.read_text()
        inventory["memory_md"] = {
            "lines": len(content.splitlines()),
            "chars": len(content),
            "content": content,
        }

    memory_dir = workspace_path / "memory"
    if memory_dir.exists():
        for f in sorted(memory_dir.glob("*.md")):
            content = f.read_text()
            inventory["memory_files"].append({
                "name": f.name,
                "lines": len(content.splitlines()),
                "chars": len(content),
                "content": content[:3000],  # truncate for prompt size
            })

    return inventory


async def _spawn_curator_worker(
    agent_name: str,
    workspace_path: Path,
    inventory: dict[str, Any],
) -> dict[str, Any] | None:
    """Spawn an OpenClaw worker to intelligently curate an agent's MEMORY.md.

    The worker reads all memory files, decides what's important, and rewrites
    MEMORY.md to be concise and high-signal. Source files are never modified.
    """
    memory_md_content = inventory["memory_md"]["content"] if inventory["memory_md"] else ""
    memory_md_lines = inventory["memory_md"]["lines"] if inventory["memory_md"] else 0

    # Build file listing for prompt
    file_summaries = []
    for mf in inventory["memory_files"]:
        file_summaries.append(f"- {mf['name']} ({mf['lines']} lines): {mf['content'][:500]}")

    file_listing = "\n".join(file_summaries) if file_summaries else "(no memory files)"

    prompt = f"""## Memory Curation Task: {agent_name}

You are curating MEMORY.md for the **{agent_name}** agent workspace at `{workspace_path}`.

### Current state:
- MEMORY.md: {memory_md_lines} lines
- Memory files in memory/: {len(inventory['memory_files'])} files

### Memory files (source material, DO NOT modify these):
{file_listing}

### Current MEMORY.md:
```
{memory_md_content[:8000]}
```

### Instructions:

1. **Read** the current MEMORY.md (the full file at `{workspace_path}/MEMORY.md`) and all files in `{workspace_path}/memory/`
2. **Keep** everything that's genuinely important and useful:
   - Architecture decisions, patterns, and conventions
   - Key lessons learned and real gotchas that prevent bugs
   - Project structure and important paths
   - User preferences and working style
   - Active context that affects daily work
3. **Remove only** clear bloat:
   - Stale/outdated information that's no longer true
   - Verbose tutorials or step-by-step examples (summarize to a few sentences — the detail is still in the memory/ source files)
   - Duplicate information (same thing said multiple ways)
   - Low-signal noise that doesn't help with real work
   - Information already in the "Shared Context (from Lobs)" section (no need to duplicate)
4. **Write** the curated MEMORY.md to `{workspace_path}/MEMORY.md`
   - Use concise bullet points over long paragraphs
   - Structure with clear headers
   - Preserve the "## Shared Context (from Lobs)" section exactly as-is (it's managed separately)
   - Length doesn't matter — keep all important info. Just don't keep junk.
5. **Do NOT** modify any files in memory/ — those are source material and must stay intact

### Important:
- The "## Shared Context (from Lobs)" section is auto-managed. Copy it verbatim.
- When in doubt, keep it. Err on the side of preserving useful information.
- The original detailed info remains in the memory/ files for semantic search.
"""

    try:
        import uuid

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_spawn",
                    "sessionKey": f"{GATEWAY_SESSION_KEY}-spawn-{uuid.uuid4().hex[:8]}",
                    "args": {
                        "task": prompt,
                        "model": "sonnet",
                        "runTimeoutSeconds": 300,
                        "cleanup": "delete",
                        "label": f"memory-curator-{agent_name}",
                    },
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            data = await resp.json()

            details = data.get("result", {}).get("details", {})
            if data.get("ok") and details.get("status") == "accepted":
                return {
                    "status": "spawned",
                    "run_id": details.get("runId"),
                    "session_key": details.get("childSessionKey"),
                }
            else:
                logger.warning(
                    "Failed to spawn curator for %s: %s", agent_name, data
                )
                return {"status": "failed", "error": str(data)}

    except Exception as e:
        logger.error("Error spawning curator for %s: %s", agent_name, e, exc_info=True)
        return {"status": "error", "error": str(e)}


async def run_memory_maintenance(routine=None) -> dict[str, Any]:
    """Full memory maintenance pass.

    Phase 1 (deterministic): session cleanup + context propagation
    Phase 2 (intelligent): spawn curator workers for bloated MEMORY.md files
    """
    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "propagation": {},
        "sessions": {},
        "curation": {},
        "summary": {},
    }

    # Phase 1: Deterministic cleanup
    prop_report = propagate_shared_context()
    results["propagation"] = prop_report

    session_report = cleanup_stale_sessions()
    results["sessions"] = session_report

    # Phase 2: Spawn curator workers for agents with bloated MEMORY.md
    curators_spawned = 0
    for agent_name, ws_path in AGENT_WORKSPACES.items():
        if not ws_path.exists():
            continue

        inventory = _gather_memory_inventory(ws_path)
        if not inventory["memory_md"]:
            continue

        md_lines = inventory["memory_md"]["lines"]
        # Only curate if over target or if there are non-dated source files
        # that might contain useful info not yet in MEMORY.md
        has_source_material = any(
            not re.match(r"^\d{4}-\d{2}-\d{2}", mf["name"])
            for mf in inventory["memory_files"]
        )

        if md_lines > CURATION_THRESHOLD_LINES or has_source_material:
            spawn_result = await _spawn_curator_worker(agent_name, ws_path, inventory)
            results["curation"][agent_name] = spawn_result
            if spawn_result and spawn_result.get("status") == "spawned":
                curators_spawned += 1
        else:
            results["curation"][agent_name] = {"status": "ok", "lines": md_lines}

    # Trigger memory_sync to keep DB consistent
    try:
        from app.database import AsyncSessionLocal
        from app.services.memory_sync import sync_agent_memories

        async with AsyncSessionLocal() as db:
            sync_result = await sync_agent_memories(db)
            await db.commit()
            results["memory_sync"] = sync_result
    except Exception as e:
        logger.warning("Memory sync failed (non-fatal): %s", e)
        results["memory_sync"] = {"error": str(e)}

    results["summary"] = {
        "agents_propagated": len(prop_report.get("agents_updated", [])),
        "sessions_removed": session_report["total_removed"],
        "curators_spawned": curators_spawned,
    }

    logger.info(
        "[MEMORY_MAINTENANCE] Complete: propagated=%d sessions=%d curators=%d",
        len(prop_report.get("agents_updated", [])),
        session_report["total_removed"],
        curators_spawned,
    )

    return results
