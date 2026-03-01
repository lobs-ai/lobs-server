"""Reflection workflow callables — discrete steps for the workflow engine.

These functions break the monolithic ReflectionCycleManager into composable
workflow nodes. Each function is registered in the python_call registry
and can be called as a workflow step with retry/failure handling.

The full reflection workflow:
1. list_execution_agents — returns agents eligible for reflection
2. build_context_packets — build context for all agents
3. spawn_reflection_agents — spawn a reflection session per agent
4. wait_for_reflections — poll until all reflection workers complete
5. persist_reflection_results — already handled by worker_manager completion hooks
6. run_sweep — quality filter + dedup + route to inbox
7. emit_completion — signal that the batch is done
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentReflection,
    AgentInitiative,
    OrchestratorSetting,
    SystemSweep,
)
from app.orchestrator.config import CONTROL_PLANE_AGENTS
from app.orchestrator.context_packets import ContextPacketBuilder
from app.orchestrator.model_chooser import ModelChooser
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.runtime_settings import SETTINGS_KEY_REFLECTION_LAST_RUN_AT

logger = logging.getLogger(__name__)


async def list_execution_agents(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Step 1: List all execution agents eligible for reflection."""
    registry = AgentRegistry()
    agents = [a for a in registry.available_types() if a not in CONTROL_PLANE_AGENTS]
    return {"agents": agents, "count": len(agents)}


async def build_context_packets(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Step 2: Build context packets for all agents."""
    agents = (context or {}).get("list_agents", {}).get("agents", [])
    if not agents:
        return {"packets": {}, "count": 0}

    packet_builder = ContextPacketBuilder(db)
    packets = {}
    for agent in agents:
        try:
            packet = await packet_builder.build_for_agent(agent, hours=6)
            packets[agent] = packet.to_dict()
        except Exception as e:
            logger.warning("[REFLECTION_WF] Failed to build context for %s: %s", agent, e)
            packets[agent] = {"error": str(e)}

    return {"packets": packets, "count": len(packets)}


async def spawn_reflection_agents(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Step 3: Spawn a reflection session per agent.

    Creates AgentReflection records and spawns OpenClaw sessions.
    Returns session info for tracking.
    """
    if worker_manager is None:
        return {"error": "No worker_manager available", "spawned": 0}

    agents = (context or {}).get("list_agents", {}).get("agents", [])
    packets = (context or {}).get("build_contexts", {}).get("packets", {})

    if not agents:
        return {"spawned": 0, "agents": []}

    # Import the reflection prompt builder
    from app.orchestrator.reflection_cycle import ReflectionCycleManager

    chooser = ModelChooser(db)
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=6)

    spawned = 0
    spawn_results = []

    for agent in agents:
        packet = packets.get(agent, {})
        reflection_id = str(uuid.uuid4())

        # Create reflection record
        db.add(AgentReflection(
            id=reflection_id,
            agent_type=agent,
            reflection_type="strategic",
            status="pending",
            window_start=window_start,
            window_end=window_end,
            context_packet=packet,
        ))

        # Build prompt using existing method
        prompt = ReflectionCycleManager._build_reflection_prompt(agent, packet, reflection_id)

        # Choose model — reflections need high quality thinking, use standard tier
        choice = await chooser.choose(
            agent_type=agent,
            task={
                "id": reflection_id,
                "title": "Strategic reflection cycle",
                "notes": "Periodic strategic reflection run",
                "status": "inbox",
                "model_tier": "standard",
            },
            purpose="reflection",
        )

        label = f"reflection-{agent}"
        # Prefer Gemini 3.1 for reflections (strong reasoning, no tool use needed)
        REFLECTION_MODEL = "google-gemini-cli/gemini-3.1-pro"
        reflection_model = REFLECTION_MODEL if REFLECTION_MODEL in choice.candidates else choice.model
        result, error, _error_type = await worker_manager._spawn_session(
            task_prompt=prompt,
            agent_id=agent,
            model=reflection_model,
            label=label,
        )

        if result:
            worker_manager.register_external_worker(
                result,
                agent_type=agent,
                model=reflection_model,
                label=label,
            )
            spawned += 1
            spawn_results.append({
                "agent": agent,
                "reflection_id": reflection_id,
                "model": choice.model,
                "status": "spawned",
            })
        else:
            spawn_results.append({
                "agent": agent,
                "reflection_id": reflection_id,
                "error": error or "spawn_failed",
                "status": "failed",
            })
            logger.warning("[REFLECTION_WF] Failed to spawn for %s: %s", agent, error)

    # Create sweep record
    sweep = SystemSweep(
        id=str(uuid.uuid4()),
        sweep_type="reflection_batch",
        status="completed",
        window_start=window_start,
        window_end=window_end,
        summary={"agents": len(agents), "spawned": spawned},
        decisions={"note": "Workflow-driven reflection batch"},
        completed_at=datetime.now(timezone.utc),
    )
    db.add(sweep)

    # Persist reflection anchor
    anchor = await db.get(OrchestratorSetting, SETTINGS_KEY_REFLECTION_LAST_RUN_AT)
    last_run_iso = datetime.now(timezone.utc).isoformat()
    if anchor is None:
        anchor = OrchestratorSetting(key=SETTINGS_KEY_REFLECTION_LAST_RUN_AT, value=last_run_iso)
        db.add(anchor)
    else:
        anchor.value = last_run_iso

    await db.commit()

    return {
        "spawned": spawned,
        "total_agents": len(agents),
        "results": spawn_results,
        "sweep_id": sweep.id,
    }


async def check_reflections_complete(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Step 4: Check if all reflection workers have finished.

    Returns completed=True when no reflection-* workers remain active.
    """
    if worker_manager is None:
        return {"completed": True, "remaining": 0}

    remaining = sum(
        1 for w in worker_manager.active_workers.values()
        if w.label.startswith("reflection-")
    )

    if remaining == 0:
        return {"completed": True, "remaining": 0}

    return {"completed": False, "remaining": remaining}


async def run_initiative_sweep(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Step 5: Run the initiative sweep arbitrator."""
    from app.orchestrator.sweep_arbitrator import SweepArbitrator
    arb = SweepArbitrator(db, worker_manager=worker_manager)
    return await arb.run_once()


async def run_daily_compression(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Step 6: Run daily identity compression."""
    from app.orchestrator.reflection_cycle import ReflectionCycleManager
    mgr = ReflectionCycleManager(db, worker_manager)
    result = await mgr.run_daily_compression()

    # Persist daily marker
    from zoneinfo import ZoneInfo
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    today_key = now_et.date().isoformat()

    from app.orchestrator.runtime_settings import SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET
    marker = await db.get(OrchestratorSetting, SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET)
    if marker is None:
        marker = OrchestratorSetting(key=SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET, value=today_key)
        db.add(marker)
    else:
        marker.value = today_key

    await db.commit()
    return result
