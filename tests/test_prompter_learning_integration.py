"""Integration tests for Prompter with learning enhancement."""

import pytest
from pathlib import Path
from unittest.mock import patch

from app.orchestrator.prompter import Prompter
from app.models import OutcomeLearning


class TestPrompterLearningIntegration:
    """Integration tests for Prompter with PromptEnhancer."""
    
    @pytest.mark.asyncio
    async def test_build_task_prompt_enhanced_with_learnings(self, db_session, tmp_path):
        """Test that enhanced prompter includes learnings."""
        # Create learning
        learning = OutcomeLearning(
            id="learn-1",
            agent_type="programmer",
            pattern_name="missing_tests",
            lesson_text="Always add unit tests for new features.",
            confidence=0.8,
            is_active=True,
        )
        db_session.add(learning)
        await db_session.commit()
        
        # Build enhanced prompt
        task_dict = {
            "id": "task-123",
            "title": "Add user endpoint",
            "notes": "Create REST endpoint for user management",
        }
        
        prompt, learning_ids = await Prompter.build_task_prompt_enhanced(
            db=db_session,
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="",
            learning_disabled=False,
        )
        
        # Verify learning is included
        assert "Lessons from Past Tasks" in prompt
        assert "missing_tests" in prompt
        assert "Always add unit tests" in prompt
        assert len(learning_ids) == 1
        assert learning_ids[0] == "learn-1"
        
        # Verify base prompt content is still there
        assert "Add user endpoint" in prompt or "task-123" in prompt
    
    @pytest.mark.asyncio
    async def test_build_task_prompt_enhanced_control_group(self, db_session, tmp_path):
        """Test that control group gets no learnings."""
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
        
        task_dict = {
            "id": "task-123",
            "title": "Add endpoint",
            "notes": "",
        }
        
        # Control group
        prompt, learning_ids = await Prompter.build_task_prompt_enhanced(
            db=db_session,
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="",
            learning_disabled=True,
        )
        
        # No learnings should be included
        assert "Lessons from Past Tasks" not in prompt
        assert learning_ids == []
    
    @pytest.mark.asyncio
    async def test_build_task_prompt_enhanced_no_db_session(self, tmp_path):
        """Test that prompter works without db session."""
        task_dict = {
            "id": "task-123",
            "title": "Add endpoint",
            "notes": "",
        }
        
        # No db session
        prompt, learning_ids = await Prompter.build_task_prompt_enhanced(
            db=None,
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="",
            learning_disabled=False,
        )
        
        # Should work but with no learnings
        assert prompt  # Has content
        assert learning_ids == []
        assert "Lessons from Past Tasks" not in prompt
    
    @pytest.mark.asyncio
    async def test_build_task_prompt_enhanced_matches_sync_base(self, tmp_path):
        """Test that enhanced prompt includes sync prompt content."""
        task_dict = {
            "id": "task-123",
            "title": "Implement auth",
            "notes": "Add JWT authentication",
        }
        
        # Build sync prompt
        sync_prompt = Prompter.build_task_prompt(
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="Global engineering rules",
        )
        
        # Build enhanced prompt (no learnings available)
        enhanced_prompt, learning_ids = await Prompter.build_task_prompt_enhanced(
            db=None,  # No db = no learnings
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="Global engineering rules",
            learning_disabled=False,
        )
        
        # Should be identical when no learnings
        assert enhanced_prompt == sync_prompt
        assert learning_ids == []
    
    @pytest.mark.asyncio
    async def test_build_task_prompt_enhanced_multiple_learnings(self, db_session, tmp_path):
        """Test that multiple learnings are included."""
        # Create multiple learnings
        learning1 = OutcomeLearning(
            id="learn-1",
            agent_type="programmer",
            pattern_name="missing_tests",
            lesson_text="Always add tests",
            confidence=0.9,
            is_active=True,
        )
        learning2 = OutcomeLearning(
            id="learn-2",
            agent_type="programmer",
            pattern_name="error_handling",
            lesson_text="Add proper error handling",
            confidence=0.8,
            is_active=True,
        )
        learning3 = OutcomeLearning(
            id="learn-3",
            agent_type="programmer",
            pattern_name="documentation",
            lesson_text="Document all public APIs",
            confidence=0.7,
            is_active=True,
        )
        db_session.add_all([learning1, learning2, learning3])
        await db_session.commit()
        
        task_dict = {
            "id": "task-123",
            "title": "Add feature",
            "notes": "",
        }
        
        prompt, learning_ids = await Prompter.build_task_prompt_enhanced(
            db=db_session,
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="",
            learning_disabled=False,
        )
        
        # Should include top 3 learnings (default max)
        assert len(learning_ids) <= 3
        # Highest confidence first
        assert all(pattern in prompt for pattern in ["missing_tests", "error_handling"])
    
    @pytest.mark.asyncio
    async def test_build_task_prompt_enhanced_preserves_structure(self, db_session, tmp_path):
        """Test that prompt structure is preserved with learnings."""
        # Create learning
        learning = OutcomeLearning(
            id="learn-1",
            agent_type="programmer",
            pattern_name="missing_tests",
            lesson_text="Add tests",
            confidence=0.8,
            is_active=True,
        )
        db_session.add(learning)
        await db_session.commit()
        
        task_dict = {
            "id": "task-123",
            "title": "Add feature",
            "notes": "Implementation notes",
        }
        
        prompt, _ = await Prompter.build_task_prompt_enhanced(
            db=db_session,
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="Engineering rules",
            learning_disabled=False,
        )
        
        # Check that standard sections still exist
        assert "# Work Assignment" in prompt or "Work Assignment" in prompt
        assert "## Your Task" in prompt or "Your Task" in prompt
        assert "## When You're Done" in prompt or "When You're Done" in prompt
        
        # Learnings should be at the top
        lessons_index = prompt.index("Lessons from Past Tasks")
        work_index = prompt.index("Work Assignment")
        assert lessons_index < work_index
    
    @pytest.mark.asyncio
    async def test_existing_sync_api_unchanged(self, tmp_path):
        """Test that existing sync API still works."""
        task_dict = {
            "id": "task-123",
            "title": "Add feature",
            "notes": "Notes",
        }
        
        # Old sync API should still work
        prompt = Prompter.build_task_prompt(
            item=task_dict,
            project_path=tmp_path,
            agent_type="programmer",
            rules="",
        )
        
        # Should return string (not tuple)
        assert isinstance(prompt, str)
        assert "Add feature" in prompt or "task-123" in prompt
