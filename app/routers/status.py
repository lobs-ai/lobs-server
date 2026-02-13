"""System status and health API endpoints."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Task,
    WorkerRun,
    AgentStatus as AgentStatusModel,
    InboxItem,
    Memory,
)
from app.schemas import (
    SystemOverview,
    ServerHealth,
    OrchestratorHealth,
    WorkersHealth,
    TasksHealth,
    MemoriesHealth,
    InboxHealth,
    ActivityEvent,
    CostSummary,
    CostPeriod,
    AgentCostBreakdown,
)

router = APIRouter(prefix="/status", tags=["status"])


@router.get("/overview", response_model=SystemOverview)
async def get_overview(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> SystemOverview:
    """Get combined system health snapshot."""
    
    # Server health
    start_time = getattr(request.app.state, "start_time", None)
    uptime_seconds = 0
    if start_time:
        uptime_seconds = int((datetime.now(timezone.utc) - start_time).total_seconds())
    
    server = ServerHealth(
        status="ok",
        uptime_seconds=uptime_seconds,
        version="0.1.0"
    )
    
    # Orchestrator health
    orchestrator_engine = getattr(request.app.state, "orchestrator", None)
    orchestrator = OrchestratorHealth(
        running=orchestrator_engine is not None,
        paused=False  # TODO: get from orchestrator if it exposes pause state
    )
    
    # Workers health - count active workers and get totals from worker_runs
    active_result = await db.execute(
        select(func.count()).select_from(WorkerRun).where(
            and_(WorkerRun.ended_at.is_(None), WorkerRun.started_at.isnot(None))
        )
    )
    active_workers = active_result.scalar() or 0
    
    completed_result = await db.execute(
        select(func.count()).select_from(WorkerRun).where(WorkerRun.succeeded == True)
    )
    total_completed = completed_result.scalar() or 0
    
    failed_result = await db.execute(
        select(func.count()).select_from(WorkerRun).where(WorkerRun.succeeded == False)
    )
    total_failed = failed_result.scalar() or 0
    
    workers = WorkersHealth(
        active=active_workers,
        total_completed=total_completed,
        total_failed=total_failed
    )
    
    # Agents - get recent agent statuses
    agents_result = await db.execute(
        select(AgentStatusModel).order_by(AgentStatusModel.last_active_at.desc())
    )
    agents_data = []
    for agent in agents_result.scalars().all():
        agents_data.append({
            "type": agent.agent_type,
            "status": agent.status or "unknown",
            "last_active": agent.last_active_at.isoformat() if agent.last_active_at else None
        })
    
    # Tasks health
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    active_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.status == "active")
    )
    active_tasks = active_result.scalar() or 0
    
    waiting_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.status == "waiting_on")
    )
    waiting_tasks = waiting_result.scalar() or 0
    
    blocked_result = await db.execute(
        select(func.count()).select_from(Task).where(
            and_(Task.blocked_by.isnot(None), Task.status != "completed")
        )
    )
    blocked_tasks = blocked_result.scalar() or 0
    
    completed_today_result = await db.execute(
        select(func.count()).select_from(Task).where(
            and_(
                Task.status == "completed",
                Task.finished_at >= today_start
            )
        )
    )
    completed_today = completed_today_result.scalar() or 0
    
    tasks = TasksHealth(
        active=active_tasks,
        waiting=waiting_tasks,
        blocked=blocked_tasks,
        completed_today=completed_today
    )
    
    # Memories health
    total_memories_result = await db.execute(
        select(func.count()).select_from(Memory)
    )
    total_memories = total_memories_result.scalar() or 0
    
    today_memories_result = await db.execute(
        select(func.count()).select_from(Memory).where(
            Memory.created_at >= today_start
        )
    )
    today_entries = today_memories_result.scalar() or 0
    
    memories = MemoriesHealth(
        total=total_memories,
        today_entries=today_entries
    )
    
    # Inbox health
    unread_result = await db.execute(
        select(func.count()).select_from(InboxItem).where(InboxItem.is_read == False)
    )
    unread = unread_result.scalar() or 0
    
    inbox = InboxHealth(unread=unread)
    
    return SystemOverview(
        server=server,
        orchestrator=orchestrator,
        workers=workers,
        agents=agents_data,
        tasks=tasks,
        memories=memories,
        inbox=inbox
    )


@router.get("/activity", response_model=list[ActivityEvent])
async def get_activity(
    limit: int = 50,
    since: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db)
) -> list[ActivityEvent]:
    """Get recent activity timeline across the system."""
    
    events = []
    
    # Get recent tasks
    task_query = select(Task).order_by(Task.updated_at.desc()).limit(limit)
    if since:
        task_query = task_query.where(Task.updated_at >= since)
    
    task_result = await db.execute(task_query)
    for task in task_result.scalars().all():
        # Add event for task completion
        if task.status == "completed" and task.finished_at:
            events.append(ActivityEvent(
                type="task_completed",
                title=task.title,
                timestamp=task.finished_at,
                details=f"Task completed: {task.title}"
            ))
        # Add event for task status changes
        elif task.updated_at:
            events.append(ActivityEvent(
                type="task_updated",
                title=task.title,
                timestamp=task.updated_at,
                details=f"Task {task.status}: {task.title}"
            ))
    
    # Get recent worker runs
    worker_query = select(WorkerRun).order_by(WorkerRun.started_at.desc()).limit(limit)
    if since:
        worker_query = worker_query.where(WorkerRun.started_at >= since)
    
    worker_result = await db.execute(worker_query)
    for run in worker_result.scalars().all():
        if run.started_at:
            events.append(ActivityEvent(
                type="worker_spawned",
                title=f"Worker {run.worker_id or 'unknown'} started",
                timestamp=run.started_at,
                details=f"Worker started for task {run.task_id or 'unknown'}"
            ))
        
        if run.ended_at:
            event_type = "worker_completed" if run.succeeded else "error"
            title = f"Worker {run.worker_id or 'unknown'} {'completed' if run.succeeded else 'failed'}"
            events.append(ActivityEvent(
                type=event_type,
                title=title,
                timestamp=run.ended_at,
                details=run.timeout_reason or ""
            ))
    
    # Get recent inbox items
    inbox_query = select(InboxItem).order_by(InboxItem.modified_at.desc()).limit(limit)
    if since:
        inbox_query = inbox_query.where(InboxItem.modified_at >= since)
    
    inbox_result = await db.execute(inbox_query)
    for item in inbox_result.scalars().all():
        if item.modified_at:
            events.append(ActivityEvent(
                type="inbox_received",
                title=item.title,
                timestamp=item.modified_at,
                details=item.summary or ""
            ))
    
    # Get recent memories
    memory_query = select(Memory).order_by(Memory.updated_at.desc()).limit(limit)
    if since:
        memory_query = memory_query.where(Memory.updated_at >= since)
    
    memory_result = await db.execute(memory_query)
    for memory in memory_result.scalars().all():
        events.append(ActivityEvent(
            type="memory_updated",
            title=memory.title,
            timestamp=memory.updated_at,
            details=f"Memory updated: {memory.memory_type}"
        ))
    
    # Sort all events by timestamp descending
    events.sort(key=lambda e: e.timestamp, reverse=True)
    
    # Return limited results
    return events[:limit]


@router.get("/costs", response_model=CostSummary)
async def get_costs(
    db: AsyncSession = Depends(get_db)
) -> CostSummary:
    """Get token/cost tracking summary."""
    
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    
    # Helper to calculate period costs
    async def get_period_costs(start_time: datetime) -> CostPeriod:
        result = await db.execute(
            select(
                func.coalesce(func.sum(WorkerRun.input_tokens), 0).label("tokens_in"),
                func.coalesce(func.sum(WorkerRun.output_tokens), 0).label("tokens_out"),
                func.coalesce(func.sum(WorkerRun.total_cost_usd), 0.0).label("cost")
            ).where(WorkerRun.started_at >= start_time)
        )
        row = result.one()
        return CostPeriod(
            tokens_in=int(row.tokens_in),
            tokens_out=int(row.tokens_out),
            estimated_cost=float(row.cost)
        )
    
    # Get costs for each period
    today = await get_period_costs(today_start)
    week = await get_period_costs(week_start)
    month = await get_period_costs(month_start)
    
    # Get costs by agent (from task_id -> agent mapping)
    # For now, aggregate by worker runs since we may not have agent info directly
    # This is a simplified version - could be enhanced to join with tasks table
    by_agent = []
    
    # Get unique sources/agents from worker runs
    agent_result = await db.execute(
        select(
            WorkerRun.source,
            func.coalesce(func.sum(WorkerRun.total_tokens), 0).label("tokens"),
            func.count().label("runs")
        ).group_by(WorkerRun.source)
    )
    
    for row in agent_result.all():
        if row.source:  # Only include if source is set
            by_agent.append(AgentCostBreakdown(
                type=row.source,
                tokens_total=int(row.tokens),
                runs=int(row.runs)
            ))
    
    return CostSummary(
        today=today,
        week=week,
        month=month,
        by_agent=by_agent
    )


# MARK: - Software Updates

# Only track Mission Control - it manages itself
TRACKED_REPOS = {
    "lobs-mission-control": os.path.expanduser("~/lobs-mission-control"),
}


class RepoUpdateInfo(BaseModel):
    name: str
    path: str
    local_commit: str
    local_message: str
    local_date: str
    remote_commit: str | None = None
    remote_message: str | None = None
    remote_date: str | None = None
    behind: int = 0
    ahead: int = 0
    has_update: bool = False
    branch: str = "main"
    error: str | None = None


class UpdateCheckResponse(BaseModel):
    repos: list[RepoUpdateInfo]
    has_updates: bool = False
    checked_at: str


async def _run_git(cwd: str, *args: str, timeout: int = 15) -> tuple[int, str]:
    """Run a git command and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, stdout.decode().strip()


@router.get("/updates")
async def check_updates(client_commit: str | None = None) -> UpdateCheckResponse:
    """Check for available updates.
    
    If client_commit is provided, compare that against origin/main
    instead of the server's local HEAD (for when the app runs on
    a different machine than the server).
    """
    repos: list[RepoUpdateInfo] = []

    for name, path in TRACKED_REPOS.items():
        if not os.path.isdir(os.path.join(path, ".git")):
            repos.append(RepoUpdateInfo(
                name=name, path=path,
                local_commit="", local_message="",
                local_date="", error="Not a git repo"
            ))
            continue

        try:
            # Get current branch
            rc, branch = await _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
            if rc != 0:
                branch = "main"

            # Fetch latest from origin (quiet)
            await _run_git(path, "fetch", "origin", branch, "--quiet")

            # Remote HEAD info (latest available)
            _, remote_commit = await _run_git(path, "rev-parse", "--short", f"origin/{branch}")
            _, remote_message = await _run_git(path, "log", "-1", f"origin/{branch}", "--format=%s")
            _, remote_date = await _run_git(path, "log", "-1", f"origin/{branch}", "--format=%ci")

            if client_commit:
                # Client told us its commit — compare that against origin
                local_commit = client_commit
                # Get message for client commit (may fail if server doesn't have it locally, 
                # but after fetch it should)
                rc, local_message = await _run_git(path, "log", "-1", client_commit, "--format=%s")
                if rc != 0:
                    local_message = "(unknown commit)"
                rc, local_date = await _run_git(path, "log", "-1", client_commit, "--format=%ci")
                if rc != 0:
                    local_date = ""

                # Count commits between client and origin
                _, full_remote = await _run_git(path, "rev-parse", f"origin/{branch}")
                _, full_local = await _run_git(path, "rev-parse", client_commit)
                if full_remote.startswith(full_local[:7]):
                    # Same commit
                    ahead, behind = 0, 0
                else:
                    _, rev_list = await _run_git(
                        path, "rev-list", "--left-right", "--count",
                        f"{client_commit}...origin/{branch}"
                    )
                    ahead, behind = 0, 0
                    parts = rev_list.split()
                    if len(parts) == 2:
                        ahead, behind = int(parts[0]), int(parts[1])
            else:
                # No client commit — use server's local HEAD
                _, local_commit = await _run_git(path, "rev-parse", "--short", "HEAD")
                _, local_message = await _run_git(path, "log", "-1", "--format=%s")
                _, local_date = await _run_git(path, "log", "-1", "--format=%ci")

                _, rev_list = await _run_git(path, "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}")
                ahead, behind = 0, 0
                parts = rev_list.split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])

            repos.append(RepoUpdateInfo(
                name=name, path=path, branch=branch,
                local_commit=local_commit, local_message=local_message, local_date=local_date,
                remote_commit=remote_commit, remote_message=remote_message, remote_date=remote_date,
                behind=behind, ahead=ahead,
                has_update=behind > 0,
            ))
        except Exception as e:
            repos.append(RepoUpdateInfo(
                name=name, path=path,
                local_commit="", local_message="",
                local_date="", error=str(e),
            ))

    return UpdateCheckResponse(
        repos=repos,
        has_updates=any(r.has_update for r in repos),
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


class SelfUpdateResponse(BaseModel):
    success: bool
    pull_output: str
    build_output: str
    new_commit: str | None = None
    binary_path: str | None = None


@router.post("/updates/self-update")
async def self_update_mission_control() -> SelfUpdateResponse:
    """Pull + build Mission Control, returning the new binary path for relaunch."""
    path = TRACKED_REPOS.get("lobs-mission-control")
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return SelfUpdateResponse(success=False, pull_output="Repo not found", build_output="")

    try:
        # Pull
        rc, branch = await _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0:
            branch = "main"

        rc, pull_output = await _run_git(path, "pull", "--rebase", "origin", branch, timeout=30)
        if rc != 0:
            return SelfUpdateResponse(success=False, pull_output=pull_output, build_output="")

        _, new_commit = await _run_git(path, "rev-parse", "--short", "HEAD")

        # Build (use bin/build which generates BuildInfo with commit hash)
        build_script = os.path.join(path, "bin", "build")
        if os.path.isfile(build_script):
            build_cmd = ["bash", build_script]
        else:
            build_cmd = ["swift", "build"]
        
        proc = await asyncio.create_subprocess_exec(
            *build_cmd,
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        build_output = stdout.decode().strip()

        if proc.returncode != 0:
            return SelfUpdateResponse(
                success=False,
                pull_output=pull_output,
                build_output=build_output,
                new_commit=new_commit,
            )

        # Find the built binary
        find_proc = await asyncio.create_subprocess_exec(
            "swift", "build", "--show-bin-path",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        bin_stdout, _ = await asyncio.wait_for(find_proc.communicate(), timeout=10)
        bin_path = bin_stdout.decode().strip()
        binary_path = os.path.join(bin_path, "lobs-mission-control") if bin_path else None

        return SelfUpdateResponse(
            success=True,
            pull_output=pull_output,
            build_output="Build succeeded",
            new_commit=new_commit,
            binary_path=binary_path,
        )
    except asyncio.TimeoutError:
        return SelfUpdateResponse(success=False, pull_output="", build_output="Build timed out (120s)")
    except Exception as e:
        return SelfUpdateResponse(success=False, pull_output="", build_output=str(e))
