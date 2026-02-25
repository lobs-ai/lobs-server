"""Tests for the failure_explainer service."""

import pytest

from app.services.failure_explainer import (
    FailureExplanation,
    explain_failure,
    explain_failure_markdown,
    get_runbook,
    list_runbooks,
)


# ---------------------------------------------------------------------------
# FailureExplanation.to_markdown
# ---------------------------------------------------------------------------

class TestFailureExplanationToMarkdown:
    def _make(self, **overrides) -> FailureExplanation:
        defaults = dict(
            code="worker_failed",
            title="Worker Execution Failure",
            summary="The agent worker process exited non-zero.",
            likely_causes=["Unhandled exception", "Build error"],
            quick_fix="Reset task and retry.",
            runbook_path="docs/runbooks/worker-failed.md",
            runbook_url="../docs/runbooks/worker-failed.md",
            severity="medium",
        )
        defaults.update(overrides)
        return FailureExplanation(**defaults)

    def test_includes_title(self):
        md = self._make().to_markdown()
        assert "Worker Execution Failure" in md

    def test_includes_code(self):
        md = self._make().to_markdown()
        assert "`worker_failed`" in md

    def test_includes_summary(self):
        md = self._make().to_markdown()
        assert "exited non-zero" in md

    def test_includes_causes(self):
        md = self._make().to_markdown()
        assert "Unhandled exception" in md
        assert "Build error" in md

    def test_includes_quick_fix(self):
        md = self._make().to_markdown()
        assert "Reset task" in md

    def test_includes_runbook_link_by_default(self):
        md = self._make().to_markdown()
        assert "worker-failed.md" in md
        assert "Full runbook" in md

    def test_runbook_link_suppressed(self):
        md = self._make().to_markdown(include_runbook_link=False)
        assert "Full runbook" not in md
        assert "worker-failed.md" not in md

    def test_to_dict_round_trip(self):
        exp = self._make()
        d = exp.to_dict()
        assert d["code"] == "worker_failed"
        assert d["severity"] == "medium"
        assert isinstance(d["likely_causes"], list)


# ---------------------------------------------------------------------------
# explain_failure — exact code lookup
# ---------------------------------------------------------------------------

class TestExplainFailureByCode:
    def test_worker_failed_exact(self):
        result = explain_failure(None, error_code="worker_failed")
        assert result is not None
        assert result.code == "worker_failed"

    def test_sessions_spawn_failed_exact(self):
        result = explain_failure(None, error_code="sessions_spawn_failed")
        assert result is not None
        assert result.code == "sessions_spawn_failed"

    def test_sessions_spawn_not_accepted_alias(self):
        """sessions_spawn_not_accepted should resolve to sessions_spawn_failed runbook."""
        result = explain_failure(None, error_code="sessions_spawn_not_accepted")
        assert result is not None
        assert result.code == "sessions_spawn_failed"

    def test_infrastructure_failure_by_code(self):
        result = explain_failure(None, error_code="infrastructure_failure")
        assert result is not None
        assert result.code == "infrastructure_failure"

    def test_no_file_changes_by_code(self):
        result = explain_failure(None, error_code="no_file_changes")
        assert result is not None
        assert result.code == "no_file_changes"

    def test_stuck_no_progress_by_code(self):
        result = explain_failure(None, error_code="stuck_no_progress")
        assert result is not None
        assert result.code == "stuck_no_progress"

    def test_unknown_code_returns_none(self):
        result = explain_failure(None, error_code="totally_unknown_xyz")
        assert result is None

    def test_code_case_insensitive(self):
        result = explain_failure(None, error_code="WORKER_FAILED")
        assert result is not None
        assert result.code == "worker_failed"


# ---------------------------------------------------------------------------
# explain_failure — failure_reason text matching
# ---------------------------------------------------------------------------

class TestExplainFailureByReason:
    def test_exact_no_file_changes(self):
        result = explain_failure("No file changes produced")
        assert result is not None
        assert result.code == "no_file_changes"

    def test_exact_infrastructure_failure(self):
        result = explain_failure("Infrastructure failure detected")
        assert result is not None
        assert result.code == "infrastructure_failure"

    def test_stuck_pattern_with_minutes(self):
        result = explain_failure("Stuck - no progress for 45 minutes")
        assert result is not None
        assert result.code == "stuck_no_progress"

    def test_stuck_pattern_alternate_format(self):
        result = explain_failure("Stuck – no progress for 30 minutes")
        assert result is not None
        assert result.code == "stuck_no_progress"

    def test_sessions_spawn_failed_in_reason(self):
        result = explain_failure("sessions_spawn_failed: gateway error")
        assert result is not None
        assert result.code == "sessions_spawn_failed"

    def test_sessions_spawn_not_accepted_in_reason(self):
        result = explain_failure("sessions_spawn_not_accepted")
        assert result is not None
        assert result.code == "sessions_spawn_failed"

    def test_none_reason_returns_none(self):
        result = explain_failure(None)
        assert result is None

    def test_empty_reason_returns_none(self):
        result = explain_failure("")
        assert result is None

    def test_whitespace_only_reason_returns_none(self):
        result = explain_failure("   ")
        assert result is None

    def test_unrecognised_reason_returns_none(self):
        result = explain_failure("Something very specific that nobody codes for")
        assert result is None

    def test_error_code_takes_priority_over_reason(self):
        """When both are provided, error_code should be tried first."""
        # error_code matches worker_failed; reason would match no_file_changes
        result = explain_failure(
            "No file changes produced",
            error_code="worker_failed",
        )
        assert result is not None
        assert result.code == "worker_failed"


# ---------------------------------------------------------------------------
# explain_failure_markdown
# ---------------------------------------------------------------------------

class TestExplainFailureMarkdown:
    def test_returns_markdown_for_known_code(self):
        md = explain_failure_markdown(None, error_code="worker_failed")
        assert "Worker Execution Failure" in md
        assert "worker_failed" in md

    def test_returns_markdown_for_known_reason(self):
        md = explain_failure_markdown("Stuck - no progress for 20 minutes")
        assert "Stuck" in md

    def test_fallback_for_unknown(self):
        md = explain_failure_markdown("some weird unknown error", error_code=None)
        assert "Unknown Failure" in md
        assert "no matching runbook" in md
        assert "some weird unknown error" in md

    def test_fallback_for_none(self):
        md = explain_failure_markdown(None)
        assert "Unknown Failure" in md

    def test_runbook_link_present_by_default(self):
        md = explain_failure_markdown(None, error_code="infrastructure_failure")
        assert "Full runbook" in md
        assert "infrastructure-failure.md" in md

    def test_runbook_link_suppressed(self):
        md = explain_failure_markdown(
            None, error_code="infrastructure_failure", include_runbook_link=False
        )
        assert "Full runbook" not in md

    def test_fallback_truncates_long_reason(self):
        long_reason = "x" * 500
        md = explain_failure_markdown(long_reason)
        # Should be truncated at 200 chars (plus some markup)
        assert len(md) < 600


# ---------------------------------------------------------------------------
# list_runbooks
# ---------------------------------------------------------------------------

class TestListRunbooks:
    def test_returns_list(self):
        runbooks = list_runbooks()
        assert isinstance(runbooks, list)

    def test_contains_top_five(self):
        runbooks = list_runbooks()
        codes = {rb["code"] for rb in runbooks}
        assert "worker_failed" in codes
        assert "stuck_no_progress" in codes
        assert "no_file_changes" in codes
        assert "infrastructure_failure" in codes
        assert "sessions_spawn_failed" in codes

    def test_each_has_required_fields(self):
        runbooks = list_runbooks()
        required = {"code", "title", "summary", "likely_causes", "quick_fix", "runbook_path", "runbook_url", "severity"}
        for rb in runbooks:
            missing = required - set(rb.keys())
            assert not missing, f"Runbook {rb.get('code')} missing fields: {missing}"

    def test_runbook_paths_reference_docs_dir(self):
        runbooks = list_runbooks()
        for rb in runbooks:
            assert rb["runbook_path"].startswith("docs/runbooks/"), (
                f"Expected docs/runbooks/ prefix, got: {rb['runbook_path']}"
            )

    def test_runbook_paths_end_in_md(self):
        runbooks = list_runbooks()
        for rb in runbooks:
            assert rb["runbook_path"].endswith(".md"), (
                f"Expected .md extension, got: {rb['runbook_path']}"
            )


# ---------------------------------------------------------------------------
# get_runbook
# ---------------------------------------------------------------------------

class TestGetRunbook:
    def test_exact_lookup(self):
        rb = get_runbook("worker_failed")
        assert rb is not None
        assert rb.code == "worker_failed"

    def test_alias_lookup(self):
        rb = get_runbook("sessions_spawn_not_accepted")
        assert rb is not None
        assert rb.code == "sessions_spawn_failed"

    def test_not_found_returns_none(self):
        rb = get_runbook("no_such_code")
        assert rb is None

    def test_case_insensitive(self):
        rb = get_runbook("INFRASTRUCTURE_FAILURE")
        assert rb is not None
        assert rb.code == "infrastructure_failure"

    def test_all_canonical_codes_resolvable(self):
        codes = [
            "worker_failed",
            "stuck_no_progress",
            "no_file_changes",
            "infrastructure_failure",
            "sessions_spawn_failed",
        ]
        for code in codes:
            rb = get_runbook(code)
            assert rb is not None, f"Could not resolve runbook for code: {code}"


# ---------------------------------------------------------------------------
# Runbook file existence
# ---------------------------------------------------------------------------

class TestRunbookFilesExist:
    """Verify the actual markdown runbook files are present on disk."""

    def test_all_runbook_files_exist(self, tmp_path):
        import os
        from pathlib import Path

        project_root = Path(__file__).parent.parent
        runbooks = list_runbooks()

        for rb in runbooks:
            full_path = project_root / rb["runbook_path"]
            assert full_path.exists(), (
                f"Runbook file missing: {full_path}"
            )
            # Should be non-empty
            assert full_path.stat().st_size > 100, (
                f"Runbook file suspiciously small: {full_path}"
            )

    def test_runbook_files_have_expected_sections(self):
        """Each runbook should have at minimum a What/Diagnosis/Fix section."""
        from pathlib import Path
        project_root = Path(__file__).parent.parent
        runbooks = list_runbooks()

        for rb in runbooks:
            full_path = project_root / rb["runbook_path"]
            if not full_path.exists():
                continue  # already caught above
            content = full_path.read_text()
            # Must contain the canonical code somewhere
            assert rb["code"].replace("_", "-") in content.lower() or rb["code"] in content, (
                f"Runbook {full_path.name} doesn't mention its own code"
            )
            # Must have diagnosis and fix sections
            assert "Diagnosis" in content or "diagnosis" in content, (
                f"Runbook {full_path.name} missing Diagnosis section"
            )
            assert "Fix" in content or "Resolution" in content, (
                f"Runbook {full_path.name} missing Fix/Resolution section"
            )
