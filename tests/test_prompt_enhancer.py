"""Tests for PromptEnhancer."""

import pytest
from datetime import datetime
from unittest.mock import patch

from app.orchestrator.prompt_enhancer import PromptEnhancer
from app.models import OutcomeLearning


class TestPromptEnhancer:
    """Tests for PromptEnhancer service."""
    
    @pytest.mark.asyncio
    async def test_query_relevant_learnings_filters_by_agent(self, db_session):
        """Test that query filters by agent type."""
        # Create learnings for different agents
        learning_prog = OutcomeLearning(
            id="learn-prog-1",
            agent_type="programmer",
            pattern_name="missing_tests",
            lesson_text="Always add tests",
            confidence=0.8,
            is_active=True,
        )
        learning_res = OutcomeLearning(
            id="learn-res-1",
            agent_type="researcher",
            pattern_name="bad_sources",
            lesson_text="Verify sources",
            confidence=0.9,
            is_active=True,
        )
        db_session.add_all([learning_prog, learning_res])
        await db_session.commit()
        
        task_dict = {"id": "task-1", "title": "Add endpoint", "notes": ""}
        
        # Query for programmer
        learnings = await PromptEnhancer._query_relevant_learnings(
            db=db_session,
            agent_type="programmer",
            task_dict=task_dict,
        )
        
        # Should only get programmer learnings
        assert len(learnings) == 1
        assert learnings[0].agent_type == "programmer"
        assert learnings[0].id == "learn-prog-1"
    
    @pytest.mark.asyncio
    async def test_query_relevant_learnings_filters_by_confidence(self, db_session):
        """Test that query filters by confidence threshold."""
        # Create learnings with different confidence levels
        learning_high = OutcomeLearning(
            id="learn-high",
            agent_type="programmer",
            pattern_name="high_conf",
            lesson_text="High confidence lesson",
            confidence=0.9,
            is_active=True,
        )
        learning_low = OutcomeLearning(
            id="learn-low",
            agent_type="programmer",
            pattern_name="low_conf",
            lesson_text="Low confidence lesson",
            confidence=0.1,  # Below default threshold of 0.3
            is_active=True,
        )
        db_session.add_all([learning_high, learning_low])
        await db_session.commit()
        
        task_dict = {"id": "task-1", "title": "Add endpoint", "notes": ""}
        
        learnings = await PromptEnhancer._query_relevant_learnings(
            db=db_session,
            agent_type="programmer",
            task_dict=task_dict,
        )
        
        # Should only get high confidence learning
        assert len(learnings) == 1
        assert learnings[0].id == "learn-high"
    
    @pytest.mark.asyncio
    async def test_query_relevant_learnings_filters_inactive(self, db_session):
        """Test that query excludes inactive learnings."""
        learning_active = OutcomeLearning(
            id="learn-active",
            agent_type="programmer",
            pattern_name="active_pattern",
            lesson_text="Active lesson",
            confidence=0.8,
            is_active=True,
        )
        learning_inactive = OutcomeLearning(
            id="learn-inactive",
            agent_type="programmer",
            pattern_name="inactive_pattern",
            lesson_text="Inactive lesson",
            confidence=0.8,
            is_active=False,
        )
        db_session.add_all([learning_active, learning_inactive])
        await db_session.commit()
        
        task_dict = {"id": "task-1", "title": "Add endpoint", "notes": ""}
        
        learnings = await PromptEnhancer._query_relevant_learnings(
            db=db_session,
            agent_type="programmer",
            task_dict=task_dict,
        )
        
        # Should only get active learning
        assert len(learnings) == 1
        assert learnings[0].id == "learn-active"
    
    @pytest.mark.asyncio
    async def test_query_relevant_learnings_matches_category(self, db_session):
        """Test that query matches task category."""
        # Learning for bug fixes
        learning_bug = OutcomeLearning(
            id="learn-bug",
            agent_type="programmer",
            pattern_name="bug_pattern",
            lesson_text="Check edge cases",
            task_category="bug_fix",
            confidence=0.8,
            is_active=True,
        )
        # Generic learning (no category)
        learning_generic = OutcomeLearning(
            id="learn-generic",
            agent_type="programmer",
            pattern_name="generic_pattern",
            lesson_text="Write clean code",
            task_category=None,  # Applies to all
            confidence=0.7,
            is_active=True,
        )
        db_session.add_all([learning_bug, learning_generic])
        await db_session.commit()
        
        # Bug fix task
        task_dict = {"id": "task-1", "title": "Fix crash", "notes": ""}
        
        learnings = await PromptEnhancer._query_relevant_learnings(
            db=db_session,
            agent_type="programmer",
            task_dict=task_dict,
        )
        
        # Should get both bug-specific and generic learnings
        assert len(learnings) == 2
        assert set(l.id for l in learnings) == {"learn-bug", "learn-generic"}
    
    def test_select_learnings_limits_count(self):
        """Test that only top N learnings are selected."""
        # Create 10 learnings with decreasing confidence
        learnings = [
            OutcomeLearning(
                id=f"learn-{i}",
                agent_type="programmer",
                pattern_name=f"pattern{i}",
                lesson_text=f"Lesson {i}",
                confidence=0.9 - (i * 0.05),
                is_active=True,
            )
            for i in range(10)
        ]
        
        selected = PromptEnhancer._select_learnings(learnings)
        
        # Should respect MAX_LEARNINGS_PER_PROMPT (default=3)
        assert len(selected) <= 3
        # Should be highest confidence first
        if len(selected) > 1:
            assert selected[0].confidence >= selected[-1].confidence
    
    def test_inject_learnings_prefix_style(self):
        """Test learning injection with prefix style."""
        learnings = [
            OutcomeLearning(
                id="1",
                agent_type="programmer",
                pattern_name="missing_tests",
                lesson_text="Always include unit tests.",
                confidence=0.8,
            ),
            OutcomeLearning(
                id="2",
                agent_type="programmer",
                pattern_name="error_handling",
                lesson_text="Add try/except blocks.",
                confidence=0.7,
            ),
        ]
        
        base_prompt = "## Your Task\n\nImplement user login."
        enhanced = PromptEnhancer._inject_learnings(base_prompt, learnings)
        
        # Check structure
        assert "=== Lessons from Past Tasks ===" in enhanced
        assert "[missing_tests]" in enhanced
        assert "Always include unit tests." in enhanced
        assert "[error_handling]" in enhanced
        assert "Add try/except blocks." in enhanced
        assert "## Your Task" in enhanced  # Original prompt preserved
        # Lessons should come before original prompt
        assert enhanced.index("Lessons from Past Tasks") < enhanced.index("## Your Task")
    
    def test_inject_learnings_truncates_long_lessons(self):
        """Test that long lesson text is truncated."""
        long_text = "A" * 300  # Longer than 200 char limit
        learnings = [
            OutcomeLearning(
                id="1",
                agent_type="programmer",
                pattern_name="long_lesson",
                lesson_text=long_text,
                confidence=0.8,
            ),
        ]
        
        base_prompt = "## Your Task\n\nImplement feature."
        enhanced = PromptEnhancer._inject_learnings(base_prompt, learnings)
        
        # Should be truncated with ellipsis
        assert "..." in enhanced
        # Shouldn't contain the full long text
        assert long_text not in enhanced
    
    @pytest.mark.asyncio
    async def test_enhance_prompt_full_flow(self, db_session):
        """Test full enhancement flow."""
        # Create learning
        learning = OutcomeLearning(
            id="learn-1",
            agent_type="programmer",
            pattern_name="missing_tests",
            lesson_text="Always add tests",
            confidence=0.8,
            is_active=True,
        )
        db_session.add(learning)
        await db_session.commit()
        
        task_dict = {"id": "task-1", "title": "Implement feature X", "notes": ""}
        base_prompt = "Implement feature X"
        
        enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
            db=db_session,
            base_prompt=base_prompt,
            task_dict=task_dict,
            agent_type="programmer",
            learning_disabled=False,
        )
        
        assert "Lessons from Past Tasks" in enhanced
        assert "Always add tests" in enhanced
        assert len(learning_ids) == 1
        assert learning_ids[0] == "learn-1"
    
    @pytest.mark.asyncio
    async def test_enhance_prompt_respects_control_group(self, db_session):
        """Test that control group gets unmodified prompt."""
        # Create learning
        learning = OutcomeLearning(
            id="learn-1",
            agent_type="programmer",
            pattern_name="missing_tests",
            lesson_text="Always add tests",
            confidence=0.8,
            is_active=True,
        )
        db_session.add(learning)
        await db_session.commit()
        
        task_dict = {"id": "task-1", "title": "Implement feature X", "notes": ""}
        base_prompt = "Implement feature X"
        
        enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
            db=db_session,
            base_prompt=base_prompt,
            task_dict=task_dict,
            agent_type="programmer",
            learning_disabled=True,  # Control group
        )
        
        # Should be unchanged
        assert enhanced == base_prompt
        assert learning_ids == []
    
    @pytest.mark.asyncio
    async def test_enhance_prompt_respects_feature_flag(self, db_session):
        """Test that feature flag disables enhancement."""
        with patch.dict('os.environ', {'LEARNING_INJECTION_ENABLED': 'false'}):
            # Reload module to pick up env var
            import importlib
            from app.orchestrator import prompt_enhancer
            importlib.reload(prompt_enhancer)
            
            task_dict = {"id": "task-1", "title": "Implement feature X", "notes": ""}
            base_prompt = "Implement feature X"
            
            enhanced, learning_ids = await prompt_enhancer.PromptEnhancer.enhance_prompt(
                db=db_session,
                base_prompt=base_prompt,
                task_dict=task_dict,
                agent_type="programmer",
                learning_disabled=False,
            )
            
            # Should be unchanged
            assert enhanced == base_prompt
            assert learning_ids == []
    
    @pytest.mark.asyncio
    async def test_enhance_prompt_no_learnings_found(self, db_session):
        """Test behavior when no learnings are found."""
        task_dict = {"id": "task-1", "title": "Implement feature X", "notes": ""}
        base_prompt = "Implement feature X"
        
        enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
            db=db_session,
            base_prompt=base_prompt,
            task_dict=task_dict,
            agent_type="programmer",
            learning_disabled=False,
        )
        
        # Should return base prompt unchanged
        assert enhanced == base_prompt
        assert learning_ids == []
    
    def test_infer_task_category_bug_fix(self):
        """Test category inference for bug fixes."""
        task_dict = {"title": "Fix crash when user logs in", "notes": ""}
        category = PromptEnhancer._infer_task_category(task_dict)
        assert category == "bug_fix"
    
    def test_infer_task_category_feature(self):
        """Test category inference for features."""
        task_dict = {"title": "Add user profile page", "notes": ""}
        category = PromptEnhancer._infer_task_category(task_dict)
        assert category == "feature"
    
    def test_infer_task_category_test(self):
        """Test category inference for tests."""
        task_dict = {"title": "Add tests for auth module", "notes": ""}
        category = PromptEnhancer._infer_task_category(task_dict)
        assert category == "test"
    
    def test_infer_task_category_refactor(self):
        """Test category inference for refactoring."""
        task_dict = {"title": "Refactor database layer", "notes": ""}
        category = PromptEnhancer._infer_task_category(task_dict)
        assert category == "refactor"
    
    def test_infer_task_complexity_from_shape(self):
        """Test complexity inference from shape field."""
        task_dict = {"shape": "small", "title": "", "notes": ""}
        complexity = PromptEnhancer._infer_task_complexity(task_dict)
        assert complexity == "simple"
        
        task_dict = {"shape": "medium", "title": "", "notes": ""}
        complexity = PromptEnhancer._infer_task_complexity(task_dict)
        assert complexity == "medium"
        
        task_dict = {"shape": "large", "title": "", "notes": ""}
        complexity = PromptEnhancer._infer_task_complexity(task_dict)
        assert complexity == "complex"
    
    def test_infer_task_complexity_from_content_length(self):
        """Test complexity inference from content length."""
        # Short task
        task_dict = {"title": "Fix typo", "notes": ""}
        complexity = PromptEnhancer._infer_task_complexity(task_dict)
        assert complexity == "simple"
        
        # Medium task
        task_dict = {"title": "Add feature", "notes": "A" * 300}
        complexity = PromptEnhancer._infer_task_complexity(task_dict)
        assert complexity == "medium"
        
        # Complex task
        task_dict = {"title": "Refactor entire module", "notes": "A" * 1000}
        complexity = PromptEnhancer._infer_task_complexity(task_dict)
        assert complexity == "complex"
