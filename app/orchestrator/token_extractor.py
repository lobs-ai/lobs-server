"""Extract token usage from OpenClaw session JSONL transcripts.

Each message in the transcript may contain a `usage` block with:
- input, output, cacheRead, cacheWrite, totalTokens
- cost: {input, output, cacheRead, cacheWrite, total}

This module aggregates across all messages in a session to produce totals.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionTokenUsage:
    """Aggregated token usage for a completed session."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    message_count: int = 0
    model: str | None = None
    provider: str | None = None
    per_message: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return self.total_tokens > 0 or self.message_count > 0


def extract_usage_from_transcript(session_key: str) -> SessionTokenUsage | None:
    """Extract aggregated token usage from a session's JSONL transcript.
    
    Args:
        session_key: Session key in format "agent:<agentId>:subagent:<uuid>"
        
    Returns:
        SessionTokenUsage with aggregated data, or None if transcript not found.
    """
    transcript = _find_transcript(session_key)
    if not transcript:
        return None
    
    return _parse_transcript(transcript)


def _find_transcript(session_key: str) -> pathlib.Path | None:
    """Find the JSONL transcript file for a session key."""
    parts = session_key.split(":")
    if len(parts) < 2:
        return None
    
    agent_id = parts[1]
    session_uuid = parts[3] if len(parts) >= 4 else None
    
    base = pathlib.Path.home() / ".openclaw" / "agents" / agent_id / "sessions"
    if not base.exists():
        return None
    
    if session_uuid:
        candidate = base / f"{session_uuid}.jsonl"
        if candidate.exists():
            return candidate
    
    # Fall back to most recent
    transcripts = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return transcripts[0] if transcripts else None


def _parse_transcript(path: pathlib.Path) -> SessionTokenUsage:
    """Parse a JSONL transcript and aggregate token usage."""
    usage = SessionTokenUsage()
    
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                if entry.get("type") != "message":
                    continue
                
                msg = entry.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                
                msg_usage = msg.get("usage")
                if not msg_usage:
                    continue
                
                # Extract token counts
                input_t = int(msg_usage.get("input", 0))
                output_t = int(msg_usage.get("output", 0))
                cache_read = int(msg_usage.get("cacheRead", 0))
                cache_write = int(msg_usage.get("cacheWrite", 0))
                total = int(msg_usage.get("totalTokens", 0))
                
                # Extract cost
                cost_block = msg_usage.get("cost", {})
                cost = float(cost_block.get("total", 0.0)) if isinstance(cost_block, dict) else 0.0
                
                usage.input_tokens += input_t
                usage.output_tokens += output_t
                usage.cache_read_tokens += cache_read
                usage.cache_write_tokens += cache_write
                usage.total_tokens += total or (input_t + output_t)
                usage.estimated_cost_usd += cost
                usage.message_count += 1
                
                # Track model/provider from last message
                if msg.get("model"):
                    usage.model = msg["model"]
                if msg.get("provider"):
                    usage.provider = msg["provider"]
                
                usage.per_message.append({
                    "input": input_t,
                    "output": output_t,
                    "cache_read": cache_read,
                    "cost": cost,
                    "model": msg.get("model"),
                })
    except Exception as e:
        logger.warning("Failed to parse transcript %s: %s", path, e)
    
    return usage
