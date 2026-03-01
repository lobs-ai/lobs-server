"""Gateway API interaction for worker management.

Handles all communication with OpenClaw Gateway for spawning and monitoring workers.
"""

import json
import logging
import pathlib
import time
import uuid
from typing import Any, Optional

import aiohttp

from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY
from app.orchestrator.worker_models import classify_error_type, safe_log_usage_event
from app.services.usage import resolve_route_type

logger = logging.getLogger(__name__)


class WorkerGateway:
    """Handles Gateway API interactions for worker sessions."""
    
    def __init__(self, db):
        self.db = db
    
    async def spawn_session(
        self,
        task_prompt: str,
        agent_id: str,
        model: str,
        label: str,
        routing_policy: dict[str, Any] | None = None,
    ) -> tuple[Optional[dict[str, str]], Optional[str], str]:
        """
        Call Gateway API to spawn a new session.
        
        Uses cleanup=keep so session history remains available for result
        extraction. Sessions are spawned from the internal sink session key;
        sink is a control-plane routing identity, not an execution agent.
        
        Returns:
            (Dict with runId and childSessionKey, error_string, error_type)
        """
        try:
            async with aiohttp.ClientSession() as session:
                parent_session_key = f"{GATEWAY_SESSION_KEY}-spawn-{uuid.uuid4().hex[:8]}"
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_spawn",
                        "sessionKey": parent_session_key,
                        "args": {
                            "task": task_prompt,
                            "agentId": agent_id,
                            "model": model,
                            # Local models get a shorter timeout — they tend to hang
                            # on context overflow rather than fail cleanly.
                            # Cloud models get the full 15 min window.
                            "runTimeoutSeconds": 480 if (model or "").startswith(("lmstudio/", "ollama/")) else 900,
                            "cleanup": "keep",
                            "label": label
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                )
                
                data = await resp.json()
                
                if not data.get("ok"):
                    error_msg = f"sessions_spawn_failed: {data}"
                    error_str = str(data)
                    
                    # Handle "label already in use" by cleaning up stale session and retrying once
                    if "label already in use" in error_str:
                        logger.warning(
                            "[GATEWAY] Label %s already in use, attempting cleanup and retry",
                            label,
                        )
                        # Find and delete the stale session with this label
                        await self._cleanup_stale_label(label, agent_id)
                        # Retry spawn once
                        retry_resp = await session.post(
                            f"{GATEWAY_URL}/tools/invoke",
                            headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                            json={
                                "tool": "sessions_spawn",
                                "sessionKey": parent_session_key + "-retry",
                                "args": {
                                    "task": task_prompt,
                                    "agentId": agent_id,
                                    "model": model,
                                    "runTimeoutSeconds": 480 if (model or "").startswith(("lmstudio/", "ollama/")) else 900,
                                    "cleanup": "keep",
                                    "label": label,
                                }
                            },
                            timeout=aiohttp.ClientTimeout(total=30)
                        )
                        data = await retry_resp.json()
                        if data.get("ok"):
                            # Retry succeeded, continue to result extraction below
                            logger.info("[GATEWAY] Retry after label cleanup succeeded for %s", label)
                        else:
                            error_msg = f"sessions_spawn_failed_after_retry: {data}"
                    
                    if not data.get("ok"):
                        error_type = classify_error_type(error_msg, data)
                        
                        await safe_log_usage_event(
                            self.db,
                            source="orchestrator-spawn",
                            model=model,
                            route_type=resolve_route_type(model, subscription_models=(routing_policy or {}).get("subscription_models", []), subscription_providers=(routing_policy or {}).get("subscription_providers", [])),
                            task_type="inbox" if "inbox" in label else "task_execution",
                            status="error",
                            error_code="sessions_spawn_failed",
                            metadata={"label": label, "agent_id": agent_id, "error_type": error_type},
                        )
                        logger.error(
                            "[GATEWAY] sessions_spawn failed",
                            extra={"gateway": {"model": model, "response": data, "error_type": error_type}},
                        )
                        return None, error_msg, error_type
                
                result = data.get("result", {})
                # Gateway wraps tool results in {content, details}
                details = result.get("details", result)
                if details.get("status") != "accepted":
                    error_msg = f"sessions_spawn_not_accepted: {result}"
                    error_type = classify_error_type(error_msg, result)
                    
                    await safe_log_usage_event(
                        self.db,
                        source="orchestrator-spawn",
                        model=model,
                        route_type=resolve_route_type(model, subscription_models=(routing_policy or {}).get("subscription_models", []), subscription_providers=(routing_policy or {}).get("subscription_providers", [])),
                        task_type="inbox" if "inbox" in label else "task_execution",
                        status="error",
                        error_code="sessions_spawn_not_accepted",
                        metadata={"label": label, "agent_id": agent_id, "details": details, "error_type": error_type},
                    )
                    logger.error(
                        "[GATEWAY] sessions_spawn not accepted",
                        extra={"gateway": {"model": model, "result": result, "error_type": error_type}},
                    )
                    return None, error_msg, error_type

                await safe_log_usage_event(
                    self.db,
                    source="orchestrator-spawn",
                    model=model,
                    route_type=resolve_route_type(model, subscription_models=(routing_policy or {}).get("subscription_models", []), subscription_providers=(routing_policy or {}).get("subscription_providers", [])),
                    task_type="inbox" if "inbox" in label else "task_execution",
                    status="success",
                    metadata={"label": label, "agent_id": agent_id, "run_id": details.get("runId")},
                )

                return (
                    {
                        "runId": details["runId"],
                        "childSessionKey": details["childSessionKey"],
                    },
                    None,
                    "none",  # No error
                )

        except Exception as e:
            error_msg = str(e)
            error_type = classify_error_type(error_msg)
            
            logger.error(
                "[GATEWAY] Error calling sessions_spawn",
                extra={"gateway": {"model": model, "error": error_msg, "error_type": error_type}},
                exc_info=True,
            )
            return None, error_msg, error_type
    
    async def resolve_transcript_path(self, session_key: str) -> Optional[str]:
        """Query Gateway sessions_list to get the transcript path for a session."""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_list",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-resolve-transcript",
                        "args": {"limit": 50, "messageLimit": 0}
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    for s in details.get("sessions", []):
                        if s.get("key") == session_key:
                            return s.get("transcriptPath")
        except Exception as e:
            logger.debug("[WORKER] Error resolving transcript path: %s", e)
        return None
    
    async def get_session_history(self, session_key: str) -> Optional[list[dict]]:
        """Get session message history via Gateway sessions_history API."""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-status-check",
                        "args": {
                            "sessionKey": session_key,
                            "limit": 3,
                            "includeTools": False,
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    return details.get("messages", [])
                return None
        except Exception as e:
            logger.debug("[WORKER] Error querying Gateway sessions_history: %s", e)
            return None
    
    async def fetch_session_summary(
        self, session_key: str, transcript_hint: Optional[str] = None
    ) -> Optional[str]:
        """Fetch the last assistant message from a completed session as its summary.

        Primary: read transcript file on disk (including .deleted files).
        Fallback: Gateway sessions_history API.
        """
        # --- Attempt 1: Read transcript directly from disk ---
        transcript = self.find_transcript_file(session_key, transcript_hint=transcript_hint)
        if transcript:
            messages = self.read_transcript_assistant_messages(transcript)
            if messages:
                # Use the longest assistant message (the actual output, not preamble)
                text = max(messages, key=len)
                if len(text) > 16000:
                    text = text[:16000] + "..."
                logger.info(
                    "[WORKER] Read transcript summary for %s (len=%d, file=%s)",
                    session_key, len(text), transcript.name,
                )
                return text

        # --- Attempt 2: Gateway sessions_history API ---
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-fetch-summary",
                        "args": {
                            "sessionKey": session_key,
                            "limit": 3,
                            "includeTools": False
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    messages = details.get("messages", [])

                    for msg in reversed(messages):
                        if msg.get("role") == "assistant":
                            text = msg.get("content", "")
                            if isinstance(text, list):
                                text = " ".join(
                                    b.get("text", "") for b in text
                                    if b.get("type") == "text"
                                )
                            if text and len(text) > 16000:
                                text = text[:16000] + "..."
                            if text:
                                return text
        except Exception as e:
            logger.debug("[WORKER] Gateway sessions_history failed: %s", e)

        return None
    
    async def check_session_status(
        self,
        session_key: str,
        spawn_time: Optional[float] = None,
        transcript_hint: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Check if a worker session has completed.

        Primary method: find transcript file on disk (including .deleted files).
        Fallback: Gateway sessions_history API.
        """
        try:
            # Method 1: Find transcript on disk (most reliable)
            transcript = self.find_transcript_file(session_key, transcript_hint=transcript_hint)
            if transcript:
                mtime = transcript.stat().st_mtime
                age_seconds = time.time() - mtime

                # .deleted files are always completed
                is_deleted = ".deleted." in transcript.name
                if is_deleted:
                    messages = self.read_transcript_assistant_messages(transcript)
                    return {
                        "completed": True,
                        "success": len(messages) > 0,
                        "error": "" if messages else "No assistant response in deleted transcript",
                    }

                # Live transcript — check if still being written
                if age_seconds < 15:
                    return {"completed": False, "success": False, "error": ""}

                messages = self.read_transcript_assistant_messages(transcript)
                if messages:
                    return {"completed": True, "success": True, "error": ""}
                if age_seconds > 300:
                    return {"completed": True, "success": False, "error": "Session stale (no response)"}
                return {"completed": False, "success": False, "error": ""}

            # Method 2: Try Gateway sessions_history
            history = await self.get_session_history(session_key)
            if history is not None and len(history) > 0:
                has_assistant = any(msg.get("role") == "assistant" for msg in history)
                if has_assistant:
                    return {"completed": True, "success": True, "error": ""}
                if spawn_time and (time.time() - spawn_time) / 60 > 15:
                    return {"completed": True, "success": False, "error": "Session stale"}
                return {"completed": False, "success": False, "error": ""}

            # Method 3: Check age-based fallback
            if spawn_time is not None:
                age_minutes = (time.time() - spawn_time) / 60
                if age_minutes < 5:
                    return {"completed": False, "success": False, "error": ""}
                return {"completed": True, "success": False, "error": "Session not found"}
            return {"completed": True, "success": False, "error": "Session not found"}

        except Exception as e:
            logger.warning("[WORKER] Error checking session status: %s", e)
            return None
    
    async def _cleanup_stale_label(self, label: str, agent_id: str) -> None:
        """Find and delete sessions with a specific label to resolve conflicts."""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_list",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-label-cleanup",
                        "args": {"limit": 100, "messageLimit": 0}
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    for s in details.get("sessions", []):
                        if s.get("label") == label:
                            await self.delete_session(s["key"])
        except Exception as e:
            logger.warning("[GATEWAY] Error cleaning up stale label %s: %s", label, e)

    async def delete_session(self, session_key: str) -> bool:
        """Delete a completed worker session to prevent session leak.
        
        Uses Gateway WebSocket API (sessions.delete) via aiohttp.
        """
        import aiohttp
        ws_url = GATEWAY_URL.replace("http://", "ws://").replace("https://", "wss://")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    ws_url,
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as ws:
                    request_id = uuid.uuid4().hex[:12]
                    await ws.send_json({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "sessions.delete",
                        "params": {"key": session_key},
                    })
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("id") == request_id:
                                if "error" in data:
                                    logger.warning(
                                        "[GATEWAY] sessions.delete error for %s: %s",
                                        session_key, data["error"],
                                    )
                                    return False
                                logger.info("[GATEWAY] Deleted session %s", session_key)
                                return True
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            return False
        except Exception as e:
            logger.warning("[GATEWAY] Error deleting session %s: %s", session_key, e)
            return False

    @staticmethod
    def find_transcript_file(session_key: str, transcript_hint: Optional[str] = None) -> Optional[pathlib.Path]:
        """Find a transcript file on disk for the given session key.
        
        Searches agent session directories for JSONL files matching the session UUID,
        including files that have been marked as deleted (.deleted.*).
        
        The session key UUID (subagent UUID) often differs from the transcript filename
        (sessionId), so we try both the subagent UUID and any hint from sessions_list.
        """
        parts = session_key.split(":")
        if len(parts) < 2:
            return None
        agent_id = parts[1]
        subagent_uuid = parts[3] if len(parts) >= 4 else None

        # Collect UUIDs to search for
        search_uuids = []
        if subagent_uuid:
            search_uuids.append(subagent_uuid)

        # If we have a transcript path hint, extract its sessionId
        if transcript_hint:
            hint_path = pathlib.Path(transcript_hint)
            # Extract UUID from filename like "6c51b07a-2a06-4e3f-b1d9-f05ecf156ad2.jsonl"
            stem = hint_path.name.split(".")[0]
            if stem and stem not in search_uuids:
                search_uuids.append(stem)
            # Also check if the hint path itself exists (or its .deleted version)
            if hint_path.exists():
                return hint_path
            deleted = list(hint_path.parent.glob(f"{hint_path.name}.deleted.*")) if hint_path.parent.exists() else []
            if deleted:
                return sorted(deleted, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        # Search known locations
        search_dirs = [
            pathlib.Path.home() / ".openclaw" / "agents" / agent_id / "sessions",
            pathlib.Path.home() / ".openclaw" / "workspace",
        ]

        for base in search_dirs:
            if not base.exists():
                continue
            for uuid in search_uuids:
                exact = base / f"{uuid}.jsonl"
                if exact.exists():
                    return exact
                deleted = list(base.glob(f"{uuid}.jsonl.deleted.*"))
                if deleted:
                    return sorted(deleted, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        return None
    
    @staticmethod
    def read_transcript_assistant_messages(transcript_path: pathlib.Path) -> list[str]:
        """Read all assistant message texts from a JSONL transcript file."""
        messages = []
        try:
            with open(transcript_path, "r") as f:
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
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        # Extract text blocks, skip thinking blocks
                        text_parts = []
                        for block in content:
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        content = "\n".join(text_parts)
                    if content:
                        messages.append(content)
        except Exception as e:
            logger.debug("[WORKER] Error reading transcript %s: %s", transcript_path, e)
        return messages
