"""Prompt builder for worker agents.

Port of ~/lobs-orchestrator/orchestrator/services/prompter.py

Constructs structured prompts that include:
- Agent context (AGENTS.md + SOUL.md)
- Engineering rules (global + project-specific)
- Product context
- Task details
- Output contract
"""

import logging
from pathlib import Path
from typing import Any, Optional, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.registry import get_agent
from app.orchestrator.config import BASE_DIR

logger = logging.getLogger(__name__)


# Project context files to include in prompts
PROJECT_CONTEXT_FILES = [
    "README.md",
    "CONTEXT.md",
    "ARCHITECTURE.md",
    "PRODUCT.md",
]


class Prompter:
    """Builds structured prompts for agents."""

    # ---------------------------------------------------------------------
    # Agent context
    # ---------------------------------------------------------------------

    @staticmethod
    def _normalize_agent_type(agent_type: str | None) -> str:
        """Normalize legacy/empty agent_type values to supported agent templates."""

        t = (agent_type or "").strip().lower()
        if not t:
            return "programmer"

        # Historically, agentType was metadata (e.g. 'worker'). Default those to programmer.
        legacy_to_programmer = {
            "worker",
            "task-runner",
            "worker-template",
        }
        if t in legacy_to_programmer:
            return "programmer"

        return t

    @staticmethod
    def _load_agent_system_prompt(agent_type: str) -> str:
        """Load AGENTS.md + SOUL.md for the agent type via AgentRegistry."""

        try:
            cfg = get_agent(agent_type)
            agents_md = (cfg.agents_md or "").strip()
            soul_md = (cfg.soul_md or "").strip()
            if not agents_md and not soul_md:
                return ""

            # Keep this reasonably bounded.
            agents_md = agents_md[:6000]
            soul_md = soul_md[:6000]

            return (
                "## Agent Context (Base System Prompt)\n\n"
                "### AGENTS.md\n"
                f"{agents_md}\n\n"
                "### SOUL.md\n"
                f"{soul_md}\n\n"
                "---\n\n"
            )
        except Exception as e:
            # If registry isn't available or agent not found, keep prompt usable.
            logger.warning(f"Failed to load agent system prompt for {agent_type}: {e}")
            return ""

    # ---------------------------------------------------------------------
    # Project context helpers
    # ---------------------------------------------------------------------

    @staticmethod
    def _summarize_project_files(project_path: Path) -> str:
        """Return a compact file listing for code context.

        Intentionally shallow + filtered to avoid huge prompts.
        """

        ignore_names = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            "dist",
            "build",
        }

        lines: list[str] = []
        try:
            for p in sorted(project_path.iterdir()):
                if p.name in ignore_names:
                    continue
                if p.is_dir():
                    lines.append(f"- {p.name}/")
                else:
                    lines.append(f"- {p.name}")
        except Exception:
            return ""

        if not lines:
            return ""

        joined = "\n".join(lines[:80])
        if len(lines) > 80:
            joined += f"\n- ... ({len(lines) - 80} more)"

        return "## Project Files (top-level)\n\n" + joined + "\n\n---\n\n"

    @staticmethod
    def _build_agent_specific_guidance(
        agent_type: str,
        item: dict[str, Any],
        project_path: Path,
    ) -> str:
        """Add agent-specific framing instructions."""

        t = agent_type

        if t == "programmer":
            code_ctx = Prompter._summarize_project_files(project_path)
            return (
                "## Agent Mode: Programmer\n\n"
                "Focus on implementation. Prefer small, correct changes.\n\n"
                "**Testing is MANDATORY:**\n"
                "1. Run existing tests first to understand the baseline\n"
                "2. Write new tests for every change (happy path + edge cases)\n"
                "3. Run the full test suite — ALL tests must pass before you finish\n"
                "4. Mention test results in your `.work-summary`\n\n"
                "**Be proactive:** Fix related issues blocking your task. Improve test coverage for code you touch. "
                "If you discover problems outside your scope, create handoffs.\n\n"
                "**Important:** Do NOT output thinking/reasoning text. Go straight to action. /no_think\n\n"
                + code_ctx
            )

        if t == "researcher":
            scope = item.get("scope") or item.get("constraints") or ""
            scope_block = f"**Scope/Constraints:** {scope}\n\n" if scope else ""

            question = item.get("title") or ""
            notes = item.get("notes") or ""
            q = question or notes

            return (
                "## Agent Mode: Researcher\n\n"
                "Focus on answering the research question with sources and clear synthesis.\n\n"
                f"**Research Question:**\n{q}\n\n"
                + scope_block
                + "Write findings to `research-findings.md` in the project directory.\n\n---\n\n"
            )

        if t == "reviewer":
            criteria = item.get("criteria") or (
                "Correctness, readability, tests, security, and adherence to project conventions."
            )

            return (
                "## Agent Mode: Reviewer\n\n"
                f"**Review Criteria:** {criteria}\n\n"
                + "Write review notes to `review-notes.md` in the project directory.\n\n---\n\n"
            )

        if t == "writer":
            brief = item.get("brief") or item.get("title") or "(no brief provided)"
            audience = item.get("audience") or "(unspecified)"
            style = item.get("style") or item.get("tone") or "(unspecified)"

            return (
                "## Agent Mode: Writer\n\n"
                f"**Brief:** {brief}\n\n"
                f"**Audience:** {audience}\n\n"
                f"**Style/Tone:** {style}\n\n"
                "Write output to an appropriate docs/ location and state it in `.work-summary`.\n\n"
                "---\n\n"
            )

        if t == "architect":
            constraints = item.get("constraints") or item.get("nonGoals") or ""
            constraints_block = f"**Constraints/Non-Goals:** {constraints}\n\n" if constraints else ""

            return (
                "## Agent Mode: Architect\n\n"
                + "Focus on system-level design, tradeoffs, and a concrete implementation plan broken into tasks.\n\n"
                + constraints_block
                + "Prefer incremental designs that fit existing architecture.\n\n"
                + "---\n\n"
            )

        # Unknown agent types: keep prompt usable.
        return (
            f"## Agent Mode: {agent_type}\n\n"
            "No specialized prompt template is defined for this agent type. Follow the task instructions below.\n\n"
            "---\n\n"
        )

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    @staticmethod
    def build_task_prompt(
        item: dict[str, Any],
        project_path: Path,
        agent_type: str | None = None,
        rules: str = "",
    ) -> str:
        """Build a complete prompt for an agent.

        Args:
            item: Task dict (from scanner)
            project_path: Path to project workspace
            agent_type: Agent template type (programmer|researcher|reviewer|writer|architect|...)
            rules: Global engineering rules (text)
        """

        task_id = item.get("id", "unknown")
        project_id = item.get("project_id", "unknown")
        task_title = item.get("title", "")
        task_notes = item.get("notes", "")

        normalized_agent_type = Prompter._normalize_agent_type(agent_type)

        # 0. Agent Context (AGENTS.md + SOUL.md)
        agent_context = Prompter._load_agent_system_prompt(normalized_agent_type)

        # 1. Product Context
        product_context = ""
        for filename in PROJECT_CONTEXT_FILES:
            file_path = project_path / filename
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")[:5000]  # Limit size
                    product_context += f"### {filename}\n{content}\n\n"
                except Exception:
                    pass

        # 2. Project-specific Engineering Rules
        project_rules = ""
        project_rules_path = project_path / "ENGINEERING_RULES.md"
        if project_rules_path.exists():
            try:
                project_rules = project_rules_path.read_text(encoding="utf-8")[:3000]
            except Exception:
                pass

        # Agent-specific framing (before task details)
        agent_guidance = Prompter._build_agent_specific_guidance(
            normalized_agent_type,
            item=item,
            project_path=project_path,
        )

        prompt = (
            agent_context
            + "# Work Assignment\n\n"
            + f"**Project:** {project_id}  \n"
            + f"**Workspace:** `{project_path}`  \n"
            + f"**Task ID:** {task_id}\n\n"
            + "---\n\n"
            + f"{agent_guidance}"
            + "## Engineering Rules\n\n"
        )

        # Add engineering rules (global + project)
        if rules:
            prompt += f"{rules}\n\n"
        if project_rules:
            prompt += f"### Project-Specific ({project_id})\n{project_rules}\n\n"

        if not rules and not project_rules:
            prompt += "(none)\n\n"

        prompt += "---\n\n"

        # Add product context
        if product_context:
            prompt += f"## Product Context\n\n{product_context}---\n\n"

        prompt += "## Your Task\n\n"

        # Task details
        if task_title and task_notes:
            prompt += f"**{task_title}**\n\n{task_notes}\n\n"
        elif task_title:
            prompt += f"**{task_title}**\n\n"
        elif task_notes:
            prompt += f"{task_notes}\n\n"

        prompt += (
            "---\n\n"
            "## When You're Done\n\n"
            "Just stop. The orchestrator handles git commits, pushes, and state updates automatically.\n\n"
            "**Optional:** Write a very short summary (1-2 lines) to `.work-summary`:\n\n"
            "```\n"
            "echo \"Add auth middleware\" > .work-summary\n"
            "```\n\n"
            "**If blocked:** Write blocker to `.work-summary` and exit 1:\n\n"
            "```\n"
            "echo \"BLOCKED: missing db schema\" > .work-summary\n"
            "exit 1\n"
            "```\n\n"
            "---\n\n"
            "Begin.\n"
        )

        return prompt

    @staticmethod
    def build_diagnostic_prompt(error_log: str, task_id: str, project_id: str) -> str:
        """Build a prompt for diagnostic analysis."""

        return f"""You are a diagnostic agent. Analyze this failure and propose a fix.

## Context
- **Task ID:** {task_id}
- **Project:** {project_id}

## Error Log
```
{error_log}
```

## Instructions
1. Analyze the error carefully
2. Identify the root cause
3. Propose a specific fix
4. If you can fix it directly, do so
5. Write your findings to a diagnostic report

Be thorough but concise.
"""

    @staticmethod
    def build_research_prompt(prompt: str, project_id: str) -> str:
        """Build a prompt for research tasks."""

        return f"""You are a research agent. Investigate the following topic.

## Project
{project_id}

## Research Prompt
{prompt}

## Instructions
1. Research thoroughly using available tools
2. Synthesize findings into a clear document
3. Include sources where applicable
4. Write to `research-findings.md` in the project directory

Be comprehensive but focused.
"""

    @staticmethod
    async def build_task_prompt_enhanced(
        db: Optional[AsyncSession],
        item: dict[str, Any],
        project_path: Path,
        agent_type: str | None = None,
        rules: str = "",
        learning_disabled: bool = False,
    ) -> Tuple[str, List[str]]:
        """
        Build a complete prompt for an agent, enhanced with learnings.
        
        This is the learning-aware version of build_task_prompt().
        Falls back gracefully if db is None or enhancement fails.
        
        Args:
            db: Database session (optional - if None, no enhancement)
            item: Task dict (from scanner)
            project_path: Path to project workspace
            agent_type: Agent template type
            rules: Global engineering rules (text)
            learning_disabled: If True, skip enhancement (A/B control group)
            
        Returns:
            Tuple of (prompt_text, list of applied learning IDs)
        """
        # Build base prompt using existing sync method
        base_prompt = Prompter.build_task_prompt(
            item=item,
            project_path=project_path,
            agent_type=agent_type,
            rules=rules,
        )
        
        # If no db session, cannot enhance
        if db is None:
            logger.debug("[LEARNING] No db session provided, skipping enhancement")
            return base_prompt, []
        
        # Normalize agent type (same logic as base prompter)
        normalized_agent_type = Prompter._normalize_agent_type(agent_type)
        
        # Import here to avoid circular dependency
        from app.orchestrator.prompt_enhancer import PromptEnhancer
        
        # Enhance with learnings (fail-safe: returns base prompt on error)
        enhanced_prompt, learning_ids = await PromptEnhancer.enhance_prompt(
            db=db,
            base_prompt=base_prompt,
            task_dict=item,
            agent_type=normalized_agent_type,
            learning_disabled=learning_disabled,
        )
        
        return enhanced_prompt, learning_ids
