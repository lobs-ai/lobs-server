"""Failure explainer — maps failure codes/reasons to runbooks and structured explanations.

Each critical failure class has one canonical diagnosis entry that links to the
relevant runbook in docs/runbooks/.  Used by the reliability digest, escalation
alerts, and any human-readable failure output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FailureExplanation:
    """Structured explanation for a task failure."""

    code: str
    """Canonical failure code, e.g. 'worker_failed'."""

    title: str
    """Short human-readable title."""

    summary: str
    """One-sentence summary of what went wrong."""

    likely_causes: list[str]
    """Ordered list of the most probable root causes."""

    quick_fix: str
    """The single most common resolution action."""

    runbook_path: str
    """Relative path to the full runbook from the project root."""

    runbook_url: str
    """Markdown link to the runbook (relative path usable in docs)."""

    severity: str = "medium"
    """low | medium | high | critical"""

    def to_markdown(self, *, include_runbook_link: bool = True) -> str:
        """Render as a concise markdown block suitable for inbox / digest."""
        lines = [
            f"### 🔴 {self.title}",
            f"**Code:** `{self.code}`  |  **Severity:** {self.severity}",
            "",
            f"**Summary:** {self.summary}",
            "",
            "**Likely causes:**",
        ]
        for cause in self.likely_causes:
            lines.append(f"- {cause}")
        lines.extend(["", f"**Quick fix:** {self.quick_fix}"])
        if include_runbook_link:
            lines.extend(["", f"📖 [Full runbook]({self.runbook_url})"])
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "summary": self.summary,
            "likely_causes": self.likely_causes,
            "quick_fix": self.quick_fix,
            "runbook_path": self.runbook_path,
            "runbook_url": self.runbook_url,
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# Runbook registry
# ---------------------------------------------------------------------------

#: Base path for all runbooks (relative to project root)
RUNBOOK_BASE = "docs/runbooks"

_RUNBOOKS: list[FailureExplanation] = [
    FailureExplanation(
        code="worker_failed",
        title="Worker Execution Failure",
        summary="The agent worker process exited non-zero without a more-specific reason.",
        likely_causes=[
            "Unhandled exception or crash in the agent's task logic",
            "Test-suite failures causing the agent to exit 1",
            "Build / compile error in the target project",
            "Model returned an unexpected response the agent couldn't parse",
            "Missing tools or broken environment inside the worker",
        ],
        quick_fix=(
            "Read `task.failure_reason` and the latest `worker_runs.summary`, "
            "update the task notes with a clearer spec or the specific fix needed, "
            "then reset `work_state` to `not_started`."
        ),
        runbook_path=f"{RUNBOOK_BASE}/worker-failed.md",
        runbook_url=f"../{RUNBOOK_BASE}/worker-failed.md",
        severity="medium",
    ),
    FailureExplanation(
        code="stuck_no_progress",
        title="Task Stuck — No Progress Detected",
        summary="The monitor detected the task made no progress for the configured stall window.",
        likely_causes=[
            "Worker session silently hung (no output, no crash)",
            "OpenClaw gateway session was evicted without signalling",
            "Task was spawned but the worker never picked it up",
            "Network partition between server and gateway",
            "Agent triggered an infinite loop or very-long operation",
        ],
        quick_fix=(
            "Kill any orphaned worker session, then reset `work_state` to "
            "`not_started` and resume the orchestrator."
        ),
        runbook_path=f"{RUNBOOK_BASE}/stuck-no-progress.md",
        runbook_url=f"../{RUNBOOK_BASE}/stuck-no-progress.md",
        severity="medium",
    ),
    FailureExplanation(
        code="no_file_changes",
        title="No File Changes Produced",
        summary="The worker completed without error but produced zero code or doc changes.",
        likely_causes=[
            "Agent reported findings but didn't write any code",
            "Task was already complete — idempotent re-run with nothing left to do",
            "Agent wrote files outside the tracked workspace",
            "Task spec was ambiguous about whether file output was expected",
            "Research-style task incorrectly assigned to a programmer agent",
        ],
        quick_fix=(
            "Read the worker summary. If the task is complete, close it as done. "
            "If not, rephrase the task with an action verb (Implement / Fix / Write) "
            "and specify the target file paths."
        ),
        runbook_path=f"{RUNBOOK_BASE}/no-file-changes.md",
        runbook_url=f"../{RUNBOOK_BASE}/no-file-changes.md",
        severity="low",
    ),
    FailureExplanation(
        code="infrastructure_failure",
        title="Infrastructure Failure Detected",
        summary="The circuit breaker matched a systemic infrastructure-pattern error (connection, OOM, gateway).",
        likely_causes=[
            "OpenClaw gateway is down or unreachable",
            "OOM / memory pressure caused the worker to be killed",
            "SSL/TLS certificate error or system clock skew",
            "Model provider outage or rate-limit at the system level",
            "Disk full — server can't write logs or temp files",
        ],
        quick_fix=(
            "Run `openclaw gateway status` and check system resources. "
            "Fix the root cause, reset the task, then resume the orchestrator."
        ),
        runbook_path=f"{RUNBOOK_BASE}/infrastructure-failure.md",
        runbook_url=f"../{RUNBOOK_BASE}/infrastructure-failure.md",
        severity="high",
    ),
    FailureExplanation(
        code="sessions_spawn_failed",
        title="Worker Session Spawn Failed",
        summary="The orchestrator could not create a new OpenClaw worker session.",
        likely_causes=[
            "Gateway API returned an error or malformed response",
            "Gateway is at maximum session capacity",
            "Invalid or expired API key / token",
            "Model quota exceeded at the provider level",
            "Gateway version incompatible with the server's spawn API",
        ],
        quick_fix=(
            "Run `openclaw gateway status`. If the gateway is healthy, check "
            "session capacity and API key validity. Reset the task once fixed."
        ),
        runbook_path=f"{RUNBOOK_BASE}/sessions-spawn-failed.md",
        runbook_url=f"../{RUNBOOK_BASE}/sessions-spawn-failed.md",
        severity="high",
    ),
]

# Alias: sessions_spawn_not_accepted maps to the same runbook
_RUNBOOK_ALIASES: dict[str, str] = {
    "sessions_spawn_not_accepted": "sessions_spawn_failed",
    "no file changes produced": "no_file_changes",
    "infrastructure failure detected": "infrastructure_failure",
    "worker_failed": "worker_failed",
}

# Build lookup by canonical code
_BY_CODE: dict[str, FailureExplanation] = {rb.code: rb for rb in _RUNBOOKS}

# Regex patterns that map to failure codes (matched against failure_reason text)
_PATTERN_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"stuck\s*[-–]?\s*no progress", re.IGNORECASE), "stuck_no_progress"),
    (re.compile(r"no\s+file\s+changes\s+produced", re.IGNORECASE), "no_file_changes"),
    (re.compile(r"infrastructure\s+failure\s+detected", re.IGNORECASE), "infrastructure_failure"),
    (re.compile(r"sessions_spawn_not_accepted", re.IGNORECASE), "sessions_spawn_failed"),
    (re.compile(r"sessions_spawn_failed", re.IGNORECASE), "sessions_spawn_failed"),
    (re.compile(r"worker_failed", re.IGNORECASE), "worker_failed"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def explain_failure(
    failure_reason: Optional[str],
    error_code: Optional[str] = None,
) -> Optional[FailureExplanation]:
    """
    Return the canonical explanation for a task failure.

    Resolution order:
    1. Exact match on ``error_code`` (or alias).
    2. Exact match on ``failure_reason`` (case-insensitive, after stripping whitespace).
    3. Regex pattern match against ``failure_reason``.
    4. Returns ``None`` if no match found.

    Args:
        failure_reason: The ``Task.failure_reason`` text (may be long / free-form).
        error_code: The ``Task.error_code`` field (short code, may be None).

    Returns:
        :class:`FailureExplanation` or ``None``.
    """
    # 1. Try error_code first
    if error_code:
        code = _RUNBOOK_ALIASES.get(error_code.lower(), error_code.lower())
        if code in _BY_CODE:
            return _BY_CODE[code]

    if not failure_reason:
        return None

    reason_stripped = failure_reason.strip()

    # 2. Exact-ish match on failure_reason (normalise to lower + strip)
    reason_lower = reason_stripped.lower()
    code = _RUNBOOK_ALIASES.get(reason_lower)
    if code and code in _BY_CODE:
        return _BY_CODE[code]

    # 3. Regex pattern match
    for pattern, mapped_code in _PATTERN_MAP:
        if pattern.search(reason_stripped):
            return _BY_CODE.get(mapped_code)

    return None


def explain_failure_markdown(
    failure_reason: Optional[str],
    error_code: Optional[str] = None,
    *,
    include_runbook_link: bool = True,
) -> str:
    """
    Return a markdown-formatted explanation, or a plain fallback message.

    Suitable for embedding directly in inbox items, digest entries, or
    escalation alerts.
    """
    explanation = explain_failure(failure_reason, error_code)
    if explanation:
        return explanation.to_markdown(include_runbook_link=include_runbook_link)

    # Fallback: no matching runbook
    code_str = f"`{error_code}` — " if error_code else ""
    reason_str = (failure_reason or "unknown")[:200]
    return (
        f"### ❓ Unknown Failure\n"
        f"**Code:** {code_str}no matching runbook\n\n"
        f"**Reason:** {reason_str}\n\n"
        f"No canonical runbook exists for this failure. "
        f"Check the server logs and `worker_runs` table for details."
    )


def list_runbooks() -> list[dict]:
    """Return a list of all registered runbooks as plain dicts."""
    return [rb.to_dict() for rb in _RUNBOOKS]


def get_runbook(code: str) -> Optional[FailureExplanation]:
    """
    Look up a runbook by its canonical code or alias.

    Args:
        code: Canonical code (e.g. ``'worker_failed'``) or alias
              (e.g. ``'sessions_spawn_not_accepted'``).

    Returns:
        :class:`FailureExplanation` or ``None``.
    """
    resolved = _RUNBOOK_ALIASES.get(code.lower(), code.lower())
    return _BY_CODE.get(resolved)
