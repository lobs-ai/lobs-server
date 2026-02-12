"""Worker manager - spawns OpenClaw workers, updates database instead of git."""

import asyncio
import logging
import subprocess
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkerRun
from .config import (
    WORKER_RESULTS_DIR,
    MAX_WORKERS,
    WORKER_WARNING_TIMEOUT,
    WORKER_KILL_TIMEOUT,
)
from .agent_tracker import AgentTracker

logger = logging.getLogger(__name__)


class WorkerManager:
    """Manages spawning and tracking OpenClaw worker subprocesses."""

    def __init__(self, db: AsyncSession, agent_tracker: Optional[AgentTracker] = None):
        self.db = db
        self.agent_tracker = agent_tracker
        self.max_workers = MAX_WORKERS
        
        # In-memory tracking:
        # task_id -> (process, project_id, start_time, agent_type, task_title)
        self.active_workers: dict[str, tuple[subprocess.Popen, str, float, str, str]] = {}
        
        # Domain locks: one worker per project at a time
        self.project_locks: dict[str, str] = {}
        
        # Agent locks: one instance per agent type at a time
        self.agent_locks: dict[str, str] = {}

    async def spawn_worker(
        self,
        task: dict[str, Any],
        project_id: str,
        agent_type: str = "programmer",
    ) -> bool:
        """Spawn a worker for the given task.
        
        Returns True if spawned, False if at capacity or locked.
        """
        task_id = task["id"]
        task_title = task.get("title", task_id[:8])

        # Check capacity
        if len(self.active_workers) >= self.max_workers:
            logger.info(f"[CAPACITY] At max workers ({self.max_workers}). Queueing {task_id[:8]}")
            return False

        # Check project lock
        if project_id in self.project_locks:
            existing_task = self.project_locks[project_id]
            logger.info(f"[PROJECT-LOCK] Project {project_id} locked by {existing_task[:8]}. Queueing {task_id[:8]}")
            return False

        # Check agent lock
        if agent_type in self.agent_locks:
            existing_task = self.agent_locks[agent_type]
            logger.info(f"[AGENT-LOCK] Agent {agent_type} locked by {existing_task[:8]}. Queueing {task_id[:8]}")
            return False

        # Update task status to in_progress
        await self.db.execute(
            update(Task).where(Task.id == task_id).values(
                work_state="in_progress",
                started_at=datetime.now(timezone.utc)
            )
        )
        await self.db.commit()

        # Build prompt
        prompt = self._build_task_prompt(task, project_id, agent_type)

        # Spawn OpenClaw worker
        try:
            cmd = ["openclaw", "agent", "--agent", agent_type, "-m", prompt]
            
            WORKER_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            log_file_path = WORKER_RESULTS_DIR / f"{task_id}.log"
            log_file = open(log_file_path, "w")

            logger.info(f"Spawning OpenClaw worker for {task_id[:8]} with agent {agent_type}")
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

            # Track worker
            start_time = time.time()
            self.active_workers[task_id] = (process, project_id, start_time, agent_type, task_title)
            self.project_locks[project_id] = task_id
            self.agent_locks[agent_type] = task_id
            
            logger.info(f"[WORKER] Spawned worker for {task_id[:8]} (PID {process.pid})")
            
            # Track in agent tracker
            if self.agent_tracker:
                await self.agent_tracker.mark_working(agent_type, task_id, project_id, task_title)
            
            return True

        except Exception as e:
            logger.error(f"Failed to spawn worker for {task_id}: {e}")
            # Revert task status
            await self.db.execute(
                update(Task).where(Task.id == task_id).values(
                    work_state="not_started",
                    started_at=None
                )
            )
            await self.db.commit()
            return False

    async def check_workers(self):
        """Check status of active workers and handle completion."""
        finished = []
        current_time = time.time()

        for task_id, (process, project_id, start_time, agent_type, task_title) in list(self.active_workers.items()):
            retcode = process.poll()

            if retcode is not None:
                # Worker finished
                elapsed = current_time - start_time
                logger.info(f"Worker for {task_id[:8]} finished with code {retcode} ({elapsed:.1f}s)")
                finished.append((task_id, project_id, agent_type, retcode, start_time, task_title))
                continue

            # Check for stuck workers
            elapsed = current_time - start_time
            if elapsed > WORKER_KILL_TIMEOUT:
                logger.error(f"Worker for {task_id[:8]} exceeded timeout ({int(elapsed/60)}min). Killing.")
                try:
                    process.terminate()
                    time.sleep(2)
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                except Exception as e:
                    logger.error(f"Failed to kill stuck worker {task_id[:8]}: {e}")
                finished.append((task_id, project_id, agent_type, 1, start_time, task_title))

        # Handle finished workers
        for task_id, project_id, agent_type, retcode, start_time, task_title in finished:
            # Release locks
            if project_id in self.project_locks and self.project_locks[project_id] == task_id:
                del self.project_locks[project_id]
            if agent_type in self.agent_locks and self.agent_locks[agent_type] == task_id:
                del self.agent_locks[agent_type]

            del self.active_workers[task_id]

            duration = time.time() - start_time

            if retcode == 0:
                # Success
                await self.handle_worker_success(task_id, project_id, agent_type, duration)
            else:
                # Failure
                await self.handle_worker_failure(task_id, project_id, agent_type, duration)

    async def handle_worker_success(
        self,
        task_id: str,
        project_id: str,
        agent_type: str,
        duration: float,
    ):
        """Handle successful worker completion."""
        try:
            # Update task to completed
            await self.db.execute(
                update(Task).where(Task.id == task_id).values(
                    work_state="completed",
                    status="completed",
                    finished_at=datetime.now(timezone.utc)
                )
            )
            await self.db.commit()

            # Track in agent tracker
            if self.agent_tracker:
                await self.agent_tracker.mark_completed(agent_type, task_id, duration)
                await self.agent_tracker.mark_idle(agent_type)

            # Log worker run
            run = WorkerRun(
                worker_id=f"{agent_type}-{int(time.time())}",
                started_at=datetime.now(timezone.utc) - timedelta(seconds=duration),
                ended_at=datetime.now(timezone.utc),
                tasks_completed=1,
                task_id=task_id,
                succeeded=True,
            )
            self.db.add(run)
            await self.db.commit()

            logger.info(f"[WORKER] Task {task_id[:8]} completed successfully")

        except Exception as e:
            logger.error(f"Failed to handle worker success for {task_id}: {e}")
            await self.db.rollback()

    async def handle_worker_failure(
        self,
        task_id: str,
        project_id: str,
        agent_type: str,
        duration: float,
    ):
        """Handle worker failure."""
        try:
            # Read error log
            log_file_path = WORKER_RESULTS_DIR / f"{task_id}.log"
            error_tail = ""
            if log_file_path.exists():
                try:
                    with open(log_file_path, "r") as f:
                        lines = f.readlines()
                        error_tail = "".join(lines[-50:])
                except Exception:
                    pass

            # Update task to failed
            await self.db.execute(
                update(Task).where(Task.id == task_id).values(
                    work_state="failed",
                    status="active",  # Keep active for retry
                    finished_at=datetime.now(timezone.utc)
                )
            )
            await self.db.commit()

            # Track in agent tracker
            if self.agent_tracker:
                await self.agent_tracker.mark_failed(agent_type, task_id)
                await self.agent_tracker.mark_idle(agent_type)

            # Log worker run
            run = WorkerRun(
                worker_id=f"{agent_type}-{int(time.time())}",
                started_at=datetime.now(timezone.utc) - timedelta(seconds=duration),
                ended_at=datetime.now(timezone.utc),
                tasks_completed=0,
                task_id=task_id,
                succeeded=False,
            )
            self.db.add(run)
            await self.db.commit()

            logger.error(f"[WORKER] Task {task_id[:8]} failed")

        except Exception as e:
            logger.error(f"Failed to handle worker failure for {task_id}: {e}")
            await self.db.rollback()

    def _build_task_prompt(self, task: dict[str, Any], project_id: str, agent_type: str) -> str:
        """Build the prompt for the worker."""
        title = task.get("title", "")
        notes = task.get("notes", "")
        
        return f"""# Task: {title}

Project: {project_id}
Agent: {agent_type}

## Details

{notes}

## Instructions

Complete this task to the best of your ability. When done, report what you accomplished.
"""

    def get_worker_status(self) -> dict[str, Any]:
        """Get current worker status for API."""
        active_workers = []
        for task_id, (process, project_id, start_time, agent_type, task_title) in self.active_workers.items():
            active_workers.append({
                "taskId": task_id,
                "projectId": project_id,
                "agentType": agent_type,
                "taskTitle": task_title,
                "startedAt": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
                "pid": process.pid if hasattr(process, 'pid') else None,
            })

        return {
            "busy": len(self.active_workers) > 0,
            "activeWorkers": active_workers,
            "capacity": f"{len(self.active_workers)}/{self.max_workers}",
        }


from datetime import timedelta
