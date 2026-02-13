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
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced
from app.orchestrator.circuit_breaker import CircuitBreaker
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.prompter import Prompter
from app.orchestrator.git_manager import GitManager, OpenClawConfigManager

logger = logging.getLogger(__name__)


class WorkerManager:
    """
    Manages spawning and tracking concurrent worker subprocesses.
    
    Tracks active workers in memory and syncs state to DB.
    Enforces domain locks (one worker per project).
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        
        # In-memory tracking
        # worker_id -> (process, task_id, project_id, agent_type, start_time, log_file)
        self.active_workers: dict[str, tuple[
            subprocess.Popen, str, str, str, float, Path
        ]] = {}
        
        # Git/config managers per worker
        # worker_id -> (git_manager, config_manager, repo_path)
        self.worker_git_managers: dict[str, tuple[
            Optional[GitManager], OpenClawConfigManager, Optional[Path]
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

        # Check agent type lock (one worker per agent at a time)
        if agent_type in self.agent_locks:
            locked_task = self.agent_locks[agent_type]
            logger.info(
                f"[WORKER] Agent type {agent_type} locked by task {locked_task[:8]}. "
                f"Task {task_id[:8]} queued."
            )
            return False

        # Track for cleanup on exception
        git_manager = None
        config_manager = None
        task_id_short = task_id[:8]
        config_overridden = False
        
        try:
            # Get project details
            project = await self.db.get(Project, project_id)
            if not project:
                logger.error(f"Project {project_id} not found")
                return False

            # Resolve repo path: prefer project.repo_path, fall back to BASE_DIR/project_id
            if project.repo_path:
                repo_path = Path(project.repo_path)
            else:
                repo_path = BASE_DIR / project_id
            if not repo_path.exists():
                logger.error(f"Project repo not found: {repo_path}")
                return False

            # Create worker ID
            worker_id = f"worker_{int(time.time())}_{task_id[:8]}"
            
            # Setup log file
            log_file = WORKER_RESULTS_DIR / f"{task_id}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Git setup (only if project has repo_path)
            config_manager = OpenClawConfigManager()
            
            if project.repo_path:
                git_manager = GitManager(repo_path)
                
                # Create git branch
                if not git_manager.create_task_branch(task_id_short):
                    logger.error(f"Failed to create git branch for task {task_id_short}")
                    return False
                
                # Get agent workspace path from config
                import json
                config_path = Path.home() / ".openclaw" / "openclaw.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                agents = config.get("agents", {}).get("list", [])
                agent_workspace = None
                for a in agents:
                    if a.get("id") == agent_type or a.get("name") == agent_type:
                        agent_workspace = Path(a.get("workspace", ""))
                        break
                
                if not agent_workspace or not agent_workspace.exists():
                    logger.error(f"Agent workspace not found for {agent_type}")
                    git_manager.cleanup_on_failure(task_id_short, has_commits=False)
                    return False
                
                # Copy template files to repo
                git_manager.copy_template_files(agent_workspace)
                
                # Override workspace in config to point to repo
                if not await config_manager.override_workspace(agent_type, repo_path):
                    logger.error("Failed to override workspace config")
                    git_manager.cleanup_on_failure(task_id_short, has_commits=False)
                    return False
                
                config_overridden = True
                
                logger.info(
                    f"[WORKER] Git setup complete for {task_id_short}: "
                    f"branch created, templates copied, workspace overridden"
                )
            
            # Track git/config managers
            self.worker_git_managers[worker_id] = (
                git_manager,
                config_manager,
                repo_path if project.repo_path else None
            )

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

            # Read prompt content for -m flag
            prompt_text = prompt_file.read_text(encoding="utf-8")

            # OpenClaw command
            cmd = [
                "openclaw",
                "agent",
                "--agent", agent_type,
                "-m", prompt_text,
                "--timeout", "900",
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
            await AgentTracker(self.db).mark_working(
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
            
            # Cleanup on exception: restore config if it was overridden
            if config_overridden and config_manager:
                try:
                    await config_manager.restore_workspace(agent_type)
                    logger.info(f"[WORKER] Restored workspace config after spawn failure")
                except Exception as restore_error:
                    logger.error(f"[WORKER] Failed to restore config: {restore_error}")
            
            # Git cleanup
            if git_manager:
                try:
                    git_manager.cleanup_on_failure(task_id_short, has_commits=False)
                except Exception as cleanup_error:
                    logger.error(f"[WORKER] Failed git cleanup: {cleanup_error}")
            
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
        
        # Get git/config managers
        git_data = self.worker_git_managers.pop(worker_id, None)
        git_manager = git_data[0] if git_data else None
        config_manager = git_data[1] if git_data else None
        repo_path = git_data[2] if git_data else None
        
        # Remove from tracking
        self.active_workers.pop(worker_id, None)
        self.project_locks.pop(project_id, None)
        self.agent_locks.pop(agent_type, None)

        # Read log tail for error detection
        log_tail = self._read_log_tail(log_file, lines=50)

        # Determine success/failure
        succeeded = exit_code == 0
        
        # Git variables for DB recording
        commit_sha = None
        files_modified = []
        task_id_short = task_id[:8]
        
        if succeeded:
            # Success
            logger.info(
                f"[WORKER] Worker {worker_id} completed successfully "
                f"(task={task_id[:8]}, duration={int(duration)}s)"
            )
            
            # Git cleanup and commit (if git-managed)
            if git_manager and config_manager:
                try:
                    # Restore workspace config first
                    await config_manager.restore_workspace(agent_type)
                    
                    # Clean up template files
                    git_manager.cleanup_template_files()
                    
                    # Check for changes
                    has_changes, diff_stat = git_manager.has_changes()
                    
                    if has_changes:
                        # Commit and push
                        task_title = (await self.db.get(Task, task_id)).title or task_id_short
                        commit_sha, files_modified = git_manager.commit_and_push(
                            task_id_short,
                            task_title
                        )
                        
                        if commit_sha:
                            logger.info(
                                f"[GIT] Committed {commit_sha[:8]} with "
                                f"{len(files_modified)} files:\n{diff_stat}"
                            )
                    else:
                        logger.info(f"[GIT] No changes to commit for {task_id_short}")
                    
                except Exception as e:
                    logger.error(f"[GIT] Git operations failed for {task_id_short}: {e}")

            # Update task
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "completed"
                db_task.status = "completed"
                db_task.finished_at = datetime.now(timezone.utc)
                db_task.updated_at = datetime.now(timezone.utc)
                # Reset escalation on success
                db_task.escalation_tier = 0
                db_task.retry_count = 0
                await self.db.commit()

            # Update agent tracker
            await AgentTracker(self.db).mark_completed(
                agent_type=agent_type,
                task_id=task_id,
                duration_seconds=duration
            )
            
            # Record success in circuit breaker
            circuit_breaker = CircuitBreaker(self.db)
            await circuit_breaker.record_success(project_id, agent_type)

        else:
            # Failure
            logger.warning(
                f"[WORKER] Worker {worker_id} failed "
                f"(task={task_id[:8]}, exit_code={exit_code})"
            )
            
            # Git cleanup on failure
            if git_manager and config_manager:
                try:
                    # Restore workspace config first
                    await config_manager.restore_workspace(agent_type)
                    
                    # Check if any commits were made (branch has commits beyond base)
                    result = git_manager._run_git(
                        "rev-list", "--count", f"HEAD...origin/main",
                        check=False
                    )
                    has_commits = result.returncode == 0 and int(result.stdout.strip().split()[0] or 0) > 0
                    
                    # Cleanup: remove templates, reset changes, delete branch if empty
                    git_manager.cleanup_on_failure(task_id_short, has_commits)
                    
                    logger.info(f"[GIT] Cleaned up after failure for {task_id_short}")
                    
                except Exception as e:
                    logger.error(f"[GIT] Cleanup failed for {task_id_short}: {e}")

            # Update agent tracker
            await AgentTracker(self.db).mark_failed(agent_type, task_id)
            
            # Check if this is infrastructure failure
            circuit_breaker = CircuitBreaker(self.db)
            is_infra_failure = await circuit_breaker.record_failure(
                task_id=task_id,
                project_id=project_id,
                agent_type=agent_type,
                error_log=log_tail,
                failure_reason=f"exit_code_{exit_code}"
            )
            
            # Use enhanced escalation manager
            escalation_enhanced = EscalationManagerEnhanced(self.db)
            
            if is_infra_failure:
                # Infrastructure failure - just create alert, don't escalate
                logger.warning(
                    f"[WORKER] Infrastructure failure detected for {task_id[:8]}, "
                    f"pausing further spawning"
                )
                await escalation_enhanced.create_simple_alert(
                    task_id=task_id,
                    project_id=project_id,
                    error_log=log_tail,
                    severity="high"
                )
                
                # Mark task as blocked
                db_task = await self.db.get(Task, task_id)
                if db_task:
                    db_task.work_state = "blocked"
                    db_task.status = "active"
                    db_task.failure_reason = "Infrastructure failure detected"
                    db_task.updated_at = datetime.now(timezone.utc)
                    await self.db.commit()
            else:
                # Task-level failure - use multi-tier escalation
                escalation_result = await escalation_enhanced.handle_failure(
                    task_id=task_id,
                    project_id=project_id,
                    agent_type=agent_type,
                    error_log=log_tail,
                    exit_code=exit_code
                )
                
                logger.info(
                    f"[WORKER] Escalation result for {task_id[:8]}: {escalation_result}"
                )

        # Read work summary if worker succeeded
        summary = None
        if succeeded:
            summary = await self._read_work_summary(project_id)
        
        # Record worker run
        await self._record_worker_run(
            worker_id=worker_id,
            task_id=task_id,
            start_time=start_time,
            duration=duration,
            succeeded=succeeded,
            exit_code=exit_code,
            summary=summary,
            commit_sha=commit_sha,
            files_modified=files_modified
        )

        # Update worker status (mark inactive if no other workers)
        if not self.active_workers:
            await self._update_worker_status(active=False)

        # Mark agent idle
        await AgentTracker(self.db).mark_idle(agent_type)

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
        exit_code: int,
        summary: str | None = None,
        commit_sha: str | None = None,
        files_modified: list[str] | None = None
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
                source="orchestrator",
                summary=summary,
                commit_shas=[commit_sha] if commit_sha else None,
                files_modified=files_modified
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
    
    async def _read_work_summary(self, project_id: str) -> str | None:
        """Read .work-summary file from project directory."""
        try:
            # Get project from database to find its path
            project = await self.db.get(Project, project_id)
            if not project:
                return None
            
            # Construct path to .work-summary
            from app.orchestrator.config import BASE_DIR
            project_dir = Path(project.repo_path) if project.repo_path else BASE_DIR / project_id
            summary_file = project_dir / ".work-summary"
            
            if not summary_file.exists():
                return None
            
            # Read summary file
            with open(summary_file, "r", encoding="utf-8", errors="ignore") as f:
                summary = f.read().strip()
                return summary if summary else None
        
        except Exception as e:
            logger.warning(f"Failed to read work summary for {project_id}: {e}")
            return None

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
        self.worker_git_managers.clear()

        # Update DB
        await self._update_worker_status(active=False)

        logger.info("[WORKER] Worker shutdown complete")
