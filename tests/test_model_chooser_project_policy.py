"""Tests for ModelChooser._get_project_local_policy method.

Covers:
- Default project-level local model policy (never/preferred/allowed)
- Runtime overrides via orchestrator_settings
- Edge cases (None, empty project_id, etc.)
"""

from __future__ import annotations

import pytest

from app.orchestrator.model_chooser import ModelChooser


class TestGetProjectLocalPolicy:
    """Tests for ModelChooser._get_project_local_policy method."""

    @pytest.mark.asyncio
    async def test_get_project_local_policy_default(self, db_session):
        """_get_project_local_policy returns correct defaults for known projects."""
        chooser = ModelChooser(db_session)
        cfg = {}

        # Known projects with "never" policy
        result_lobs_server = await chooser._get_project_local_policy("lobs-server", cfg)
        assert result_lobs_server == "never", "lobs-server should use 'never' policy"

        result_mission_control = await chooser._get_project_local_policy("lobs-mission-control", cfg)
        assert result_mission_control == "never", "lobs-mission-control should use 'never' policy"

        result_mobile = await chooser._get_project_local_policy("lobs-mobile", cfg)
        assert result_mobile == "never", "lobs-mobile should use 'never' policy"

        result_sail = await chooser._get_project_local_policy("lobs-sail", cfg)
        assert result_sail == "never", "lobs-sail should use 'never' policy"

        # Known project with "preferred" policy
        result_flock = await chooser._get_project_local_policy("flock-master", cfg)
        assert result_flock == "preferred", "flock-master should use 'preferred' policy"

        # Known project with "allowed" policy
        result_grandmas = await chooser._get_project_local_policy("grandmas-stories", cfg)
        assert result_grandmas == "allowed", "grandmas-stories should use 'allowed' policy"

        # Unknown project should default to "allowed"
        result_unknown = await chooser._get_project_local_policy("unknown-project", cfg)
        assert result_unknown == "allowed", "unknown projects should default to 'allowed'"

    @pytest.mark.asyncio
    async def test_get_project_local_policy_override(self, db_session):
        """_get_project_local_policy respects runtime overrides from cfg."""
        chooser = ModelChooser(db_session)

        # Override lobs-server from "never" to "allowed"
        cfg = {"project_policy": {"lobs-server": "allowed", "custom-proj": "preferred"}}

        result_override = await chooser._get_project_local_policy("lobs-server", cfg)
        assert result_override == "allowed", "Runtime override should change 'never' to 'allowed'"

        result_custom = await chooser._get_project_local_policy("custom-proj", cfg)
        assert result_custom == "preferred", "Custom project override should be respected"

        # Still unknown projects default to "allowed" (unless in cfg)
        result_unknown = await chooser._get_project_local_policy("other-proj", cfg)
        assert result_unknown == "allowed", "Other unknown projects should still default to 'allowed'"

    @pytest.mark.asyncio
    async def test_get_project_local_policy_edge_cases(self, db_session):
        """_get_project_local_policy handles edge cases: None, empty string, etc."""
        chooser = ModelChooser(db_session)

        # None project_id
        result_none = await chooser._get_project_local_policy(None, {})
        assert result_none == "allowed", "None project_id should default to 'allowed'"

        # Empty project_id
        result_empty = await chooser._get_project_local_policy("", {})
        assert result_empty == "allowed", "Empty project_id should default to 'allowed'"

        # Empty cfg project_policy
        result_empty_cfg = await chooser._get_project_local_policy("test", {})
        assert result_empty_cfg == "allowed", "Empty cfg should fall back to defaults"

    @pytest.mark.asyncio
    async def test_get_project_local_policy_valid_return_values(self, db_session):
        """_get_project_local_policy always returns one of the three valid values."""
        chooser = ModelChooser(db_session)
        valid_policies = {"never", "preferred", "allowed"}

        test_projects = [
            "lobs-server",
            "lobs-mission-control",
            "lobs-mobile",
            "flock-master",
            "grandmas-stories",
            "unknown-project",
            None,
            "",
        ]

        for project_id in test_projects:
            result = await chooser._get_project_local_policy(project_id, {})
            assert result in valid_policies, (
                f"Project '{project_id}' returned invalid policy '{result}', "
                f"must be one of {valid_policies}"
            )

    @pytest.mark.asyncio
    async def test_get_project_local_policy_cfg_priority(self, db_session):
        """cfg.project_policy takes priority over built-in defaults."""
        chooser = ModelChooser(db_session)

        # Override multiple built-in defaults
        cfg = {
            "project_policy": {
                "lobs-server": "preferred",  # Override from "never"
                "flock-master": "never",     # Override from "preferred"
                "unknown-new": "allowed",
            }
        }

        # Check overrides are applied
        assert await chooser._get_project_local_policy("lobs-server", cfg) == "preferred"
        assert await chooser._get_project_local_policy("flock-master", cfg) == "never"
        assert await chooser._get_project_local_policy("unknown-new", cfg) == "allowed"

        # Check non-overridden defaults still work
        assert await chooser._get_project_local_policy("lobs-mobile", cfg) == "never"
        assert await chooser._get_project_local_policy("grandmas-stories", cfg) == "allowed"
