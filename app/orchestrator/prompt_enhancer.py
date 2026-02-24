"""Prompt enhancement for agent learning system.

Injects relevant learnings from past task outcomes into prompts before execution.
All operations are fail-safe: errors log but never block task execution.
"""

import logging
import os
from typing import List, Optional, Tuple

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Configuration
LEARNING_INJECTION_ENABLED = os.getenv("LEARNING_INJECTION_ENABLED", "true").lower() == "true"
MAX_LEARNINGS_PER_PROMPT = int(os.getenv("MAX_LEARNINGS_PER_PROMPT", "3"))
MIN_CONFIDENCE_THRESHOLD = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.3"))


class PromptEnhancer:
    """Enhances task prompts with relevant learnings from past outcomes."""
    
    @staticmethod
    async def enhance_prompt(
        db: AsyncSession,
        base_prompt: str,
        task_dict: dict,
        agent_type: str,
        learning_disabled: bool = False,
    ) -> Tuple[str, List[str]]:
        """
        Enhance prompt with relevant learnings.
        
        FAIL-SAFE: Any error returns base_prompt unchanged with empty learning list.
        
        Args:
            db: Database session
            base_prompt: Original prompt from Prompter
            task_dict: Task dictionary with id, title, notes, etc.
            agent_type: Agent type executing the task
            learning_disabled: If True, skip enhancement (A/B control group)
            
        Returns:
            Tuple of (enhanced_prompt, list of applied learning IDs)
        """
        try:
            # Feature flag check
            if not LEARNING_INJECTION_ENABLED:
                logger.debug("[LEARNING] Prompt enhancement disabled by feature flag")
                return base_prompt, []
            
            # A/B test control group
            if learning_disabled:
                logger.debug(f"[LEARNING] Skipping enhancement for task {task_dict.get('id', 'unknown')} (control group)")
                return base_prompt, []
            
            # Query relevant learnings
            learnings = await PromptEnhancer._query_relevant_learnings(
                db=db,
                agent_type=agent_type,
                task_dict=task_dict,
            )
            
            if not learnings:
                logger.debug(f"[LEARNING] No relevant learnings found for task {task_dict.get('id', 'unknown')}")
                return base_prompt, []
            
            # Select top N learnings
            selected = PromptEnhancer._select_learnings(learnings)
            
            if not selected:
                return base_prompt, []
            
            # Inject learnings into prompt
            enhanced = PromptEnhancer._inject_learnings(base_prompt, selected)
            
            learning_ids = [l.id for l in selected]
            
            logger.info(
                f"[LEARNING] PromptEnhancer: Injected {len(selected)} learnings into task {task_dict.get('id', 'unknown')}: "
                f"{[l.pattern_name for l in selected]}"
            )
            
            return enhanced, learning_ids
            
        except Exception as e:
            # FAIL-SAFE: Never block task execution due to learning system errors
            logger.error(f"[LEARNING] PromptEnhancer.enhance_prompt failed, returning base prompt: {e}", exc_info=True)
            return base_prompt, []
    
    @staticmethod
    async def _query_relevant_learnings(
        db: AsyncSession,
        agent_type: str,
        task_dict: dict,
    ) -> List:
        """
        Query learnings relevant to this task.
        
        Matches on:
        - Agent type (exact match)
        - Task category (if learning specifies one)
        - Task complexity (if learning specifies one)
        - Active learnings only
        - Confidence above threshold
        
        Returns:
            List of OutcomeLearning objects sorted by relevance
        """
        try:
            from app.models import OutcomeLearning
            
            # Infer task properties (simplified version - can be enhanced later)
            task_category = PromptEnhancer._infer_task_category(task_dict)
            task_complexity = PromptEnhancer._infer_task_complexity(task_dict)
            
            # Build query
            stmt = select(OutcomeLearning).where(
                and_(
                    OutcomeLearning.agent_type == agent_type,
                    OutcomeLearning.is_active == True,
                    OutcomeLearning.confidence >= MIN_CONFIDENCE_THRESHOLD,
                )
            )
            
            # Match category: either learning has no category (applies to all) or matches task
            stmt = stmt.where(
                or_(
                    OutcomeLearning.task_category.is_(None),
                    OutcomeLearning.task_category == task_category,
                )
            )
            
            # Match complexity: either learning has no complexity or matches task
            stmt = stmt.where(
                or_(
                    OutcomeLearning.task_complexity.is_(None),
                    OutcomeLearning.task_complexity == task_complexity,
                )
            )
            
            # Sort by: confidence first, then success count
            stmt = stmt.order_by(
                OutcomeLearning.confidence.desc(),
                OutcomeLearning.success_count.desc(),
            )
            
            # Get more candidates than needed, then filter
            stmt = stmt.limit(MAX_LEARNINGS_PER_PROMPT * 3)
            
            result = await db.execute(stmt)
            learnings = list(result.scalars().all())
            
            logger.debug(
                f"[LEARNING] Query found {len(learnings)} candidate learnings "
                f"(agent={agent_type}, category={task_category}, complexity={task_complexity})"
            )
            
            return learnings
            
        except Exception as e:
            logger.error(f"[LEARNING] _query_relevant_learnings failed: {e}", exc_info=True)
            return []
    
    @staticmethod
    def _select_learnings(learnings: List) -> List:
        """
        Select top N learnings to inject.
        
        V1: Simple top-N by confidence (already sorted).
        Future: Could use diversity, recency, or other signals.
        
        Args:
            learnings: List of OutcomeLearning objects (already sorted)
            
        Returns:
            List of selected learnings (max MAX_LEARNINGS_PER_PROMPT)
        """
        # Already sorted by confidence, just take top N
        selected = learnings[:MAX_LEARNINGS_PER_PROMPT]
        return selected
    
    @staticmethod
    def _inject_learnings(base_prompt: str, learnings: List) -> str:
        """
        Inject learnings into prompt using prefix style.
        
        Adds a "Lessons from Past Tasks" section at the top of the prompt.
        
        Args:
            base_prompt: Original prompt text
            learnings: List of OutcomeLearning objects to inject
            
        Returns:
            Enhanced prompt with lessons prepended
        """
        if not learnings:
            return base_prompt
        
        lessons_section = "=== Lessons from Past Tasks ===\n\n"
        lessons_section += "Based on similar tasks, keep these in mind:\n\n"
        
        for i, learning in enumerate(learnings, 1):
            # Truncate lesson text if too long (keep prompts manageable)
            lesson_text = learning.lesson_text
            if len(lesson_text) > 200:
                lesson_text = lesson_text[:197] + "..."
            lessons_section += f"{i}. [{learning.pattern_name}] {lesson_text}\n"
        
        lessons_section += "\n" + "=" * 32 + "\n\n"
        
        # Prepend to base prompt
        enhanced = lessons_section + base_prompt
        
        return enhanced
    
    @staticmethod
    def _infer_task_category(task_dict: dict) -> Optional[str]:
        """
        Infer task category from task metadata.
        
        Categories: bug_fix, feature, test, refactor, docs, research
        
        Args:
            task_dict: Task dictionary
            
        Returns:
            Inferred category or None
        """
        title = (task_dict.get("title") or "").lower()
        notes = (task_dict.get("notes") or "").lower()
        text = f"{title} {notes}"
        
        # Simple keyword matching (can be enhanced with ML later)
        if any(kw in text for kw in ["bug", "fix", "broken", "error", "crash"]):
            return "bug_fix"
        if any(kw in text for kw in ["test", "coverage", "pytest"]):
            return "test"
        if any(kw in text for kw in ["refactor", "cleanup", "simplify"]):
            return "refactor"
        if any(kw in text for kw in ["doc", "documentation", "readme"]):
            return "docs"
        if any(kw in text for kw in ["research", "investigate", "explore"]):
            return "research"
        if any(kw in text for kw in ["feature", "add", "implement", "create"]):
            return "feature"
        
        # Default: assume feature work
        return "feature"
    
    @staticmethod
    def _infer_task_complexity(task_dict: dict) -> Optional[str]:
        """
        Infer task complexity from task metadata.
        
        Complexity levels: simple, medium, complex
        
        Args:
            task_dict: Task dictionary
            
        Returns:
            Inferred complexity or None
        """
        shape = task_dict.get("shape")
        if shape:
            shape_lower = shape.lower()
            if shape_lower in ["small", "xs", "s"]:
                return "simple"
            if shape_lower in ["medium", "m", "md"]:
                return "medium"
            if shape_lower in ["large", "l", "xl", "complex"]:
                return "complex"
        
        # Fallback: estimate from title/notes length
        title = task_dict.get("title") or ""
        notes = task_dict.get("notes") or ""
        total_chars = len(title) + len(notes)
        
        if total_chars < 100:
            return "simple"
        elif total_chars < 500:
            return "medium"
        else:
            return "complex"
