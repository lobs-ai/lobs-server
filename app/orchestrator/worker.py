"""Worker manager - spawns and manages OpenClaw worker processes.

Port of ~/lobs-orchestrator/orchestrator/core/worker.py
Key changes:
- Replace git commit/push with DB writes for task status
- Replace worker-status.json writes with DB updates to worker_status table
- Replace worker-history.json appends with DB inserts to worker_runs table
- Keep OpenClaw worker spawning logic (subprocess management)
- Keep domain locks (one worker per project)
"""

import asyncio
import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkerStatus, WorkerRun, Project
from app.orchestrator.config import (
    BASE_DIR,
    WORKER_RESULTS_DIR,
    MAX_WORKERS,
    WORKER_WARNING_TIMEOUT,
    WORKER_KILL_TIMEOUT,
)
from app.orchestrator.escalation import EscalationManager
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.prompter import Prompter

logger = logging.getLogger(__name__)


class WorkerManager:
    """
    Manages spawning and tracking concurrent worker subprocesses.
    
    Tracks active workers in memory and syncs state to DB.
    Enforces domain locks (one worker per project).
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.escalation = EscalationManager(db)
        self.agent_tracker = AgentTracker(db)
        
        # In-memory tracking
        # worker_id -> (process, task_id, project_id, agent_type, start_time, log_file)
        self.active_workers: dict[str, tuple[
            subprocess.Popen, str, str, str, float, Path
        ]] = {}
        
        # Domain locks: one worker per project
        self.project_locks: dict[str, str] = {}  # project_id -> task_id
        
        # Per-agent-type limiting
        self.agent_locks: dict[str, str] = {}  # agent_type -> task_id

        self.max_workers = MAX_WORKERS

    async def spawn_worker(
        self,
        task: dict[str, Any],
        project_id: str,
        agent_type: str,
        rules: Optional[dict[str, Any]] = None
    ) -> bool:
        """
        Spawn an OpenClaw worker for the given task.
        
        Args:
            task: Task dict (from scanner)
            project_id: Project ID
            agent_type: Agent type (programmer/researcher/etc)
            rules: Optional engineering rules
            
        Returns:
            True if worker spawned, False if queued/blocked
        """
        task_id = task.get("id")
        if not task_id:
            logger.warning("Cannot spawn worker: task missing ID")
            return False

        # Check capacity
        if len(self.active_workers) >= self.max_workers:
            logger.info(
                f"[WORKER] Max workers ({self.max_workers}) reached. "
                f"Task {task_id[:8]} queued."
            )
            return False

        # Check project lock (one worker per project)
        if project_id in self.project_locks:
            locked_task = self.project_locks[project_id]
            logger.info(
                f"[WORKER] Project {project_id} locked by task {locked_task[:8]}. "
                f"Task {task_id[:8]} queued."
            )
            return False

        # Check agent type lock
        if agent_type in self.agent_locks:
            locked_task = self.agent_locks[agent_type]
            logger.info(
                f"[WORKER] Agent type {agent_type} locked by task {locked_task[:8]}. "
                f"Task {task_id[:8]} queued."
            )
            return False

        try:
            # Get project details
            project = await self.db.get(Project, project_id)
            if not project:
                logger.error(f"Project {project_id} not found")
                return False

            repo_path = BASE_DIR / project_id
            if not repo_path.exists():
                logger.error(f"Project repo not found: {repo_path}")
                return False

            # Create worker ID
            worker_id = f"worker_{int(time.time())}_{task_id[:8]}"
            
            # Setup log file
            log_file = WORKER_RESULTS_DIR / f"{task_id}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)

            # Build OpenClaw command
            task_title = task.get("title", task_id[:8])
            
            # Build prompt using Prompter (with agent context, rules, etc)
            prompt_file = WORKER_RESULTS_DIR / f"{task_id}.prompt.txt"
            try:
                # TODO: Load global engineering rules from config/DB
                global_rules = ""
                prompt_content = Prompter.build_task_prompt(
                    item=task,
                    project_path=repo_path,
                    agent_type=agent_type,
                    rules=global_rules
                )
                prompt_file.write_text(prompt_content, encoding="utf-8")
                logger.info(f"[WORKER] Built structured prompt for {task_id[:8]} (agent={agent_type})")
            except Exception as e:
                # Fallback to simple prompt if Prompter fails
                logger.warning(f"[WORKER] Prompter failed for {task_id[:8]}: {e}. Using fallback.")
                task_notes = task.get("notes", "")
                prompt_content = f"{task_title}\n\n{task_notes}".strip()
                prompt_file.write_text(prompt_content, encoding="utf-8")

            # OpenClaw command (simplified - adjust based on your setup)
            cmd = [
                "openclaw",
                "agent",
                "--agent", "worker",  # Use worker identity
                "--workspace", str(repo_path),
                "-f", str(prompt_file),
                "--session-label", f"task:{task_id[:8]}",
            ]

            # Spawn process
            with open(log_file, "w") as log:
                process = subprocess.Popen(
                    cmd,
                    cwd=repo_path,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # Detach from parent
                )

            start_time = time.time()
            
            # Track worker
            self.active_workers[worker_id] = (
                process, task_id, project_id, agent_type, start_time, log_file
            )
            self.project_locks[project_id] = task_id
            self.agent_locks[agent_type] = task_id

            # Update DB: worker status
            await self._update_worker_status(
                active=True,
                worker_id=worker_id,
                task_id=task_id,
                project_id=project_id,
                started_at=datetime.fromtimestamp(start_time, tz=timezone.utc)
            )

            # Update DB: task status
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "in_progress"
                db_task.started_at = datetime.now(timezone.utc)
                db_task.updated_at = datetime.now(timezone.utc)
                await self.db.commit()

            # Update agent tracker
            await self.agent_tracker.mark_working(
                agent_type=agent_type,
                task_id=task_id,
                project_id=project_id,
                activity=task_title
            )

            logger.info(
                f"[WORKER] Spawned worker {worker_id} for task {task_id[:8]} "
                f"(project={project_id}, agent={agent_type}, pid={process.pid})"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to spawn worker for task {task_id[:8]}: {e}", exc_info=True)
            return False

    async def check_workers(self) -> None:
        """
        Check all active workers and handle completed/failed ones.
        
        Called by engine on each tick.
        """
        if not self.active_workers:
            return

        for worker_id in list(self.active_workers.keys()):
            await self._check_worker(worker_id)

    async def _check_worker(self, worker_id: str) -> None:
        """Check a specific worker and handle completion/timeout."""
        worker_data = self.active_workers.get(worker_id)
        if not worker_data:
            return

        process, task_id, project_id, agent_type, start_time, log_file = worker_data

        # Check if process finished
        exit_code = process.poll()
        
        if exit_code is not None:
            # Process finished
            await self._handle_worker_completion(
                worker_id, task_id, project_id, agent_type, 
                start_time, exit_code, log_file
            )
            return

        # Check timeout
        runtime = time.time() - start_time
        
        if runtime > WORKER_KILL_TIMEOUT:
            logger.warning(
                f"[WORKER] Worker {worker_id} exceeded timeout "
                f"({int(runtime/60)}m). Killing."
            )
            await self._kill_worker(worker_id, reason="timeout")
            return

        elif runtime > WORKER_WARNING_TIMEOUT:
            # Just log warning, don't kill yet
            if int(runtime) % 300 == 0:  # Log every 5 minutes
                logger.warning(
                    f"[WORKER] Worker {worker_id} running long "
                    f"({int(runtime/60)}m)"
                )

    async def _handle_worker_completion(
        self,
        worker_id: str,
        task_id: str,
        project_id: str,
        agent_type: str,
        start_time: float,
        exit_code: int,
        log_file: Path
    ) -> None:
        """Handle worker completion (success or failure)."""
        duration = time.time() - start_time
        
        # Remove from tracking
        self.active_workers.pop(worker_id, None)
        self.project_locks.pop(project_id, None)
        self.agent_locks.pop(agent_type, None)

        # Read log tail for error detection
        log_tail = self._read_log_tail(log_file, lines=50)

        # Determine success/failure
        succeeded = exit_code == 0
        
        if succeeded:
            # Success
            logger.info(
                f"[WORKER] Worker {worker_id} completed successfully "
                f"(task={task_id[:8]}, duration={int(duration)}s)"
            )

            # Update task
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "completed"
                db_task.status = "completed"
                db_task.finished_at = datetime.now(timezone.utc)
                db_task.updated_at = datetime.now(timezone.utc)
                await self.db.commit()

            # Update agent tracker
            await self.agent_tracker.mark_completed(
                agent_type=agent_type,
                task_id=task_id,
                duration_seconds=duration
            )

        else:
            # Failure
            logger.warning(
                f"[WORKER] Worker {worker_id} failed "
                f"(task={task_id[:8]}, exit_code={exit_code})"
            )

            # Update task
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "blocked"
                db_task.status = "active"  # Keep active for retry
                db_task.updated_at = datetime.now(timezone.utc)
                await self.db.commit()

            # Update agent tracker
            await self.agent_tracker.mark_failed(agent_type, task_id)

            # Create escalation alert
            await self.escalation.create_failure_alert(
                task_id=task_id,
                project_id=project_id,
                error_log=log_tail,
                severity="medium"
            )

        # Record worker run
        await self._record_worker_run(
            worker_id=worker_id,
            task_id=task_id,
            start_time=start_time,
            duration=duration,
            succeeded=succeeded,
            exit_code=exit_code
        )

        # Update worker status (mark inactive if no other workers)
        if not self.active_workers:
            await self._update_worker_status(active=False)

        # Mark agent idle
        await self.agent_tracker.mark_idle(agent_type)

    async def _kill_worker(self, worker_id: str, reason: str) -> None:
        """Kill a worker process."""
        worker_data = self.active_workers.get(worker_id)
        if not worker_data:
            return

        process, task_id, project_id, agent_type, start_time, log_file = worker_data

        logger.warning(f"[WORKER] Killing worker {worker_id} (reason={reason})")

        try:
            # Try graceful shutdown first
            process.terminate()
            time.sleep(2)
            
            # Force kill if still running
            if process.poll() is None:
                process.kill()

        except Exception as e:
            logger.error(f"Error killing worker {worker_id}: {e}")

        # Handle as failed completion
        await self._handle_worker_completion(
            worker_id, task_id, project_id, agent_type,
            start_time, -1, log_file
        )

    async def _update_worker_status(
        self,
        active: bool,
        worker_id: Optional[str] = None,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        started_at: Optional[datetime] = None
    ) -> None:
        """Update worker_status table (singleton record)."""
        try:
            result = await self.db.execute(
                select(WorkerStatus).where(WorkerStatus.id == 1)
            )
            status = result.scalar_one_or_none()

            if not status:
                status = WorkerStatus(id=1)
                self.db.add(status)

            status.active = active
            
            if active:
                status.worker_id = worker_id
                status.current_task = task_id
                status.current_project = project_id
                status.started_at = started_at
                status.last_heartbeat = datetime.now(timezone.utc)
            else:
                status.worker_id = None
                status.current_task = None
                status.current_project = None
                status.ended_at = datetime.now(timezone.utc)

            await self.db.commit()

        except Exception as e:
            logger.error(f"Failed to update worker status: {e}", exc_info=True)
            await self.db.rollback()

    async def _record_worker_run(
        self,
        worker_id: str,
        task_id: str,
        start_time: float,
        duration: float,
        succeeded: bool,
        exit_code: int
    ) -> None:
        """Record worker run to history table."""
        try:
            run = WorkerRun(
                worker_id=worker_id,
                task_id=task_id,
                started_at=datetime.fromtimestamp(start_time, tz=timezone.utc),
                ended_at=datetime.now(timezone.utc),
                tasks_completed=1 if succeeded else 0,
                succeeded=succeeded,
                timeout_reason="exit_code_" + str(exit_code) if not succeeded else None,
                source="orchestrator"
            )

            self.db.add(run)
            await self.db.commit()

        except Exception as e:
            logger.error(f"Failed to record worker run: {e}", exc_info=True)
            await self.db.rollback()

    def _read_log_tail(self, log_file: Path, lines: int = 50) -> str:
        """Read last N lines from log file."""
        if not log_file.exists():
            return ""
        
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                return "".join(all_lines[-lines:])
        except Exception as e:
            logger.warning(f"Failed to read log file {log_file}: {e}")
            return ""

    async def get_worker_status(self) -> dict[str, Any]:
        """Get current worker status summary."""
        try:
            result = await self.db.execute(
                select(WorkerStatus).where(WorkerStatus.id == 1)
            )
            status = result.scalar_one_or_none()

            if not status or not status.active:
                return {
                    "busy": False,
                    "active_count": 0,
                    "current_task": None,
                    "state": "idle"
                }

            return {
                "busy": True,
                "active_count": len(self.active_workers),
                "current_task": status.current_task,
                "current_project": status.current_project,
                "worker_id": status.worker_id,
                "state": "working",
                "started_at": status.started_at.isoformat() if status.started_at else None
            }

        except Exception as e:
            logger.error(f"Failed to get worker status: {e}", exc_info=True)
            return {
                "busy": False,
                "active_count": 0,
                "error": str(e)
            }

    async def shutdown(self, timeout: float = 300.0) -> None:
        """Gracefully shutdown all workers."""
        if not self.active_workers:
            logger.info("[WORKER] No active workers to shutdown")
            return

        logger.info(f"[WORKER] Shutting down {len(self.active_workers)} workers...")

        # Terminate all workers
        for worker_id, (process, _, _, _, _, _) in self.active_workers.items():
            try:
                logger.info(f"[WORKER] Terminating worker {worker_id}")
                process.terminate()
            except Exception as e:
                logger.warning(f"Error terminating worker {worker_id}: {e}")

        # Wait for graceful shutdown
        deadline = time.time() + timeout
        while self.active_workers and time.time() < deadline:
            await asyncio.sleep(1)
            await self.check_workers()

        # Force kill any remaining
        for worker_id, (process, _, _, _, _, _) in list(self.active_workers.items()):
            try:
                if process.poll() is None:
                    logger.warning(f"[WORKER] Force killing worker {worker_id}")
                    process.kill()
            except Exception as e:
                logger.error(f"Error killing worker {worker_id}: {e}")

        # Clear state
        self.active_workers.clear()
        self.project_locks.clear()
        self.agent_locks.clear()

        # Update DB
        await self._update_worker_status(active=False)

        logger.info("[WORKER] Worker shutdown complete")
