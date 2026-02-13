"""Git management for worker agents.

Handles branch creation, template file copying, and commit/push after task completion.
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Git config
GIT_USER_NAME = "Lobs"
GIT_USER_EMAIL = "thelobsbot@gmail.com"

# Template files to copy into project repos
TEMPLATE_FILES = [
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
    "IDENTITY.md",
    "WORKER_RULES.md",
    "HEARTBEAT.md",
]

# Global config file lock (async)
_config_lock = asyncio.Lock()


class GitManager:
    """Manages git operations for worker agents."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in the repo."""
        cmd = ["git", "-C", str(self.repo_path)] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check
        )
        if result.returncode != 0 and check:
            logger.error(f"Git command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def create_task_branch(self, task_id_short: str) -> bool:
        """Create a git branch for the task.
        
        Returns True on success, False on failure.
        """
        branch_name = f"task/{task_id_short}"
        
        try:
            # Ensure we have latest main/master
            self._run_git("fetch", "origin", check=False)
            
            # Get default branch (main or master)
            result = self._run_git(
                "symbolic-ref", "refs/remotes/origin/HEAD",
                check=False
            )
            if result.returncode == 0:
                default_branch = result.stdout.strip().split('/')[-1]
            else:
                # Fallback to main
                default_branch = "main"
            
            # Checkout default branch
            self._run_git("checkout", default_branch, check=False)
            self._run_git("pull", "--rebase", "origin", default_branch, check=False)
            
            # Create and checkout task branch
            self._run_git("checkout", "-b", branch_name)
            
            logger.info(f"[GIT] Created branch {branch_name} in {self.repo_path.name}")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"[GIT] Failed to create branch {branch_name}: {e}")
            return False

    def copy_template_files(self, agent_workspace: Path) -> list[str]:
        """Copy template files from agent workspace to repo root.
        
        Returns list of copied file names.
        """
        copied = []
        
        for filename in TEMPLATE_FILES:
            src = agent_workspace / filename
            if not src.exists():
                continue
            
            dst = self.repo_path / filename
            try:
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                copied.append(filename)
                logger.debug(f"[GIT] Copied {filename} to {self.repo_path.name}")
            except Exception as e:
                logger.warning(f"[GIT] Failed to copy {filename}: {e}")
        
        logger.info(
            f"[GIT] Copied {len(copied)} template files to {self.repo_path.name}"
        )
        return copied

    def cleanup_template_files(self) -> None:
        """Remove template files from repo root."""
        for filename in TEMPLATE_FILES:
            filepath = self.repo_path / filename
            if filepath.exists():
                try:
                    filepath.unlink()
                    logger.debug(f"[GIT] Removed {filename} from {self.repo_path.name}")
                except Exception as e:
                    logger.warning(f"[GIT] Failed to remove {filename}: {e}")

    def has_changes(self) -> tuple[bool, str]:
        """Check if there are staged changes.
        
        Returns (has_changes, diff_stat).
        """
        try:
            # Stage all changes
            self._run_git("add", "-A")
            
            # Unstage template files to prevent them from being committed
            # (in case they were accidentally added/modified by the agent)
            for filename in TEMPLATE_FILES:
                self._run_git("reset", "HEAD", filename, check=False)
            
            # Get diff stat
            result = self._run_git(
                "diff", "--cached", "--stat",
                check=False
            )
            diff_stat = result.stdout.strip()
            
            has_changes = bool(diff_stat)
            
            return has_changes, diff_stat
            
        except Exception as e:
            logger.error(f"[GIT] Failed to check for changes: {e}")
            return False, ""

    def commit_and_push(
        self,
        task_id_short: str,
        task_title: str
    ) -> tuple[Optional[str], list[str]]:
        """Commit and push changes.
        
        Returns (commit_sha, modified_files).
        """
        branch_name = f"task/{task_id_short}"
        
        try:
            # Configure git user
            self._run_git("config", "user.name", GIT_USER_NAME)
            self._run_git("config", "user.email", GIT_USER_EMAIL)
            
            # Commit
            commit_message = f"task({task_id_short}): {task_title}"
            self._run_git("commit", "-m", commit_message)
            
            # Get commit SHA
            result = self._run_git("rev-parse", "HEAD")
            commit_sha = result.stdout.strip()
            
            # Get list of modified files
            result = self._run_git("diff", "--name-only", "HEAD~1", "HEAD")
            modified_files = [
                line.strip()
                for line in result.stdout.split("\n")
                if line.strip()
            ]
            
            # Push to remote
            self._run_git("push", "origin", branch_name)
            
            logger.info(
                f"[GIT] Committed and pushed {commit_sha[:8]} "
                f"({len(modified_files)} files)"
            )
            
            return commit_sha, modified_files
            
        except subprocess.CalledProcessError as e:
            logger.error(f"[GIT] Failed to commit/push: {e}")
            return None, []

    def merge_to_main(self, task_id_short: str) -> bool:
        """Merge task branch back to main and push.
        
        Attempts automatic merge. Returns True on success, False on conflict.
        On conflict, leaves the branch unmerged for manual resolution.
        """
        branch_name = f"task/{task_id_short}"
        
        try:
            # Get default branch
            result = self._run_git(
                "symbolic-ref", "refs/remotes/origin/HEAD",
                check=False
            )
            if result.returncode == 0:
                default_branch = result.stdout.strip().split('/')[-1]
            else:
                default_branch = "main"
            
            # Fetch latest
            self._run_git("fetch", "origin", check=False)
            
            # Checkout main
            self._run_git("checkout", default_branch)
            self._run_git("pull", "--rebase", "origin", default_branch, check=False)
            
            # Merge task branch
            result = self._run_git(
                "merge", branch_name, "--no-edit",
                check=False
            )
            
            if result.returncode != 0:
                # Merge conflict
                logger.warning(
                    f"[GIT] Merge conflict for {branch_name}. "
                    f"Aborting merge, branch preserved for manual resolution."
                )
                self._run_git("merge", "--abort", check=False)
                # Go back to task branch so repo isn't in weird state
                self._run_git("checkout", branch_name, check=False)
                return False
            
            # Push merged main
            result = self._run_git("push", "origin", default_branch, check=False)
            if result.returncode != 0:
                logger.error(f"[GIT] Failed to push merged {default_branch}")
                return False
            
            # Delete task branch (local and remote)
            self._run_git("branch", "-d", branch_name, check=False)
            self._run_git("push", "origin", "--delete", branch_name, check=False)
            
            logger.info(
                f"[GIT] Merged {branch_name} → {default_branch} and pushed"
            )
            return True
            
        except Exception as e:
            logger.error(f"[GIT] Merge failed for {branch_name}: {e}")
            return False

    def cleanup_on_failure(self, task_id_short: str, has_commits: bool) -> None:
        """Clean up after task failure.
        
        - Remove template files
        - Reset uncommitted changes
        - Delete branch if no commits
        """
        branch_name = f"task/{task_id_short}"
        
        try:
            # Remove template files
            self.cleanup_template_files()
            
            # Reset uncommitted changes
            self._run_git("checkout", "--", ".", check=False)
            
            # Delete branch if no commits
            if not has_commits:
                # Get default branch
                result = self._run_git(
                    "symbolic-ref", "refs/remotes/origin/HEAD",
                    check=False
                )
                if result.returncode == 0:
                    default_branch = result.stdout.strip().split('/')[-1]
                else:
                    default_branch = "main"
                
                # Checkout default branch
                self._run_git("checkout", default_branch, check=False)
                
                # Delete task branch
                self._run_git("branch", "-D", branch_name, check=False)
                
                logger.info(f"[GIT] Deleted empty branch {branch_name}")
            
        except Exception as e:
            logger.error(f"[GIT] Cleanup failed: {e}")


class OpenClawConfigManager:
    """Manages temporary workspace overrides in openclaw.json."""

    def __init__(self, config_path: Path = Path.home() / ".openclaw" / "openclaw.json"):
        self.config_path = config_path
        self._original_workspace: Optional[str] = None

    async def override_workspace(
        self,
        agent_type: str,
        new_workspace: Path
    ) -> bool:
        """Temporarily override agent workspace.
        
        Uses async lock to prevent concurrent modifications.
        Returns True on success.
        """
        async with _config_lock:
            try:
                # Read config
                config = json.loads(self.config_path.read_text(encoding="utf-8"))
                
                # Find agent in list
                agents = config.get("agents", {}).get("list", [])
                agent = None
                for a in agents:
                    if a.get("id") == agent_type or a.get("name") == agent_type:
                        agent = a
                        break
                
                if not agent:
                    logger.error(f"[CONFIG] Agent {agent_type} not found in config")
                    return False
                
                # Save original workspace
                self._original_workspace = agent.get("workspace")
                
                # Override workspace
                agent["workspace"] = str(new_workspace)
                
                # Write config
                self.config_path.write_text(
                    json.dumps(config, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                
                logger.info(
                    f"[CONFIG] Overrode {agent_type} workspace: "
                    f"{self._original_workspace} → {new_workspace}"
                )
                
                return True
                
            except Exception as e:
                logger.error(f"[CONFIG] Failed to override workspace: {e}")
                return False

    async def restore_workspace(self, agent_type: str) -> None:
        """Restore original workspace path."""
        async with _config_lock:
            if self._original_workspace is None:
                logger.warning("[CONFIG] No original workspace to restore")
                return
            
            try:
                # Read config
                config = json.loads(self.config_path.read_text(encoding="utf-8"))
                
                # Find agent
                agents = config.get("agents", {}).get("list", [])
                agent = None
                for a in agents:
                    if a.get("id") == agent_type or a.get("name") == agent_type:
                        agent = a
                        break
                
                if not agent:
                    logger.warning(f"[CONFIG] Agent {agent_type} not found")
                    return
                
                # Restore workspace
                agent["workspace"] = self._original_workspace
                
                # Write config
                self.config_path.write_text(
                    json.dumps(config, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                
                logger.info(
                    f"[CONFIG] Restored {agent_type} workspace to "
                    f"{self._original_workspace}"
                )
                
                self._original_workspace = None
                
            except Exception as e:
                logger.error(f"[CONFIG] Failed to restore workspace: {e}")
