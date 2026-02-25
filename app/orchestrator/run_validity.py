"""Run Validity Contract — hard validation layer for orchestrator task completion.

Prevents 'marked done but not actually done' outcomes by enforcing a versioned
contract before a task can be moved to completed status.

Contract v1 requirements:
  1. LIFECYCLE   — required lifecycle events present (spawned + first_response)
  2. SLA         — first-response SLA met (default 10 min from spawn)
  3. TRANSCRIPT  — transcript durability confirmed (file readable before compaction)
  4. EVIDENCE    — evidence bundle attached (work summary OR file changes)

Fail-closed: on any violation the completion is rejected and a remediation reason
code is recorded so operators know exactly what failed.

Remediation reason codes:
  RVC_MISSING_LIFECYCLE  — started_at not set (task was never properly spawned)
  RVC_NO_FIRST_RESPONSE  — no assistant message found in transcript
  RVC_SLA_BREACH         — first response exceeded SLA window
  RVC_NO_TRANSCRIPT      — transcript file not found / not readable
  RVC_NO_EVIDENCE        — no work summary and no file changes produced
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Contract version ──────────────────────────────────────────────────────────

CONTRACT_VERSION = 1

# ── SLA constants ─────────────────────────────────────────────────────────────

# Maximum seconds from task start to first assistant response (10 minutes)
FIRST_RESPONSE_SLA_SECONDS: int = 600

# ── Remediation reason codes ──────────────────────────────────────────────────

RVC_MISSING_LIFECYCLE = "RVC_MISSING_LIFECYCLE"
RVC_NO_FIRST_RESPONSE = "RVC_NO_FIRST_RESPONSE"
RVC_SLA_BREACH = "RVC_SLA_BREACH"
RVC_NO_TRANSCRIPT = "RVC_NO_TRANSCRIPT"
RVC_NO_EVIDENCE = "RVC_NO_EVIDENCE"

# ── Schema ────────────────────────────────────────────────────────────────────


@dataclass
class ContractViolation:
    """A single contract requirement that was not satisfied."""
    code: str           # Remediation reason code (RVC_*)
    requirement: str    # Human-readable requirement name
    detail: str         # Specific explanation of what was missing / wrong


@dataclass
class RunValidityResult:
    """Result of a run validity contract check."""
    contract_version: int = CONTRACT_VERSION
    passed: bool = False
    violations: list[ContractViolation] = field(default_factory=list)

    # Observed values (for audit trail)
    lifecycle_ok: bool = False
    first_response_time_seconds: Optional[float] = None
    sla_ok: bool = False
    transcript_found: bool = False
    evidence_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "passed": self.passed,
            "violations": [
                {"code": v.code, "requirement": v.requirement, "detail": v.detail}
                for v in self.violations
            ],
            "lifecycle_ok": self.lifecycle_ok,
            "first_response_time_seconds": self.first_response_time_seconds,
            "sla_ok": self.sla_ok,
            "transcript_found": self.transcript_found,
            "evidence_ok": self.evidence_ok,
        }


# ── Validator ─────────────────────────────────────────────────────────────────


class RunValidityChecker:
    """
    Validates a worker run against the Run Validity Contract before completion.

    Instantiate with the context from _handle_worker_completion and call
    validate() to get a RunValidityResult.

    Usage::

        checker = RunValidityChecker(
            task_id=task_id,
            started_at=db_task.started_at,
            session_key=worker_info.child_session_key,
            transcript_path=worker_info.transcript_path,
            result_summary=result_summary,
            files_modified=files_modified,
            find_transcript_fn=worker_manager._find_transcript_file,
            read_messages_fn=worker_manager._read_transcript_assistant_messages,
            first_response_sla_seconds=FIRST_RESPONSE_SLA_SECONDS,
        )
        result = checker.validate()
        if not result.passed:
            # fail-closed: reject completion, log violations
            ...
    """

    def __init__(
        self,
        *,
        task_id: str,
        started_at: Optional[datetime],
        session_key: str,
        transcript_path: Optional[str] = None,
        result_summary: Optional[str] = None,
        files_modified: Optional[list[str]] = None,
        find_transcript_fn: Any = None,
        read_messages_fn: Any = None,
        first_response_sla_seconds: int = FIRST_RESPONSE_SLA_SECONDS,
    ) -> None:
        self.task_id = task_id
        self.started_at = started_at
        self.session_key = session_key
        self.transcript_path = transcript_path
        self.result_summary = result_summary
        self.files_modified = files_modified or []
        self._find_transcript = find_transcript_fn
        self._read_messages = read_messages_fn
        self.first_response_sla_seconds = first_response_sla_seconds

    # ── Public API ────────────────────────────────────────────────────────────

    def validate(self) -> RunValidityResult:
        """Run all contract checks and return the consolidated result."""
        result = RunValidityResult()

        self._check_lifecycle(result)
        transcript = self._check_transcript(result)
        self._check_first_response(result, transcript)
        self._check_evidence(result)

        result.passed = len(result.violations) == 0
        return result

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_lifecycle(self, result: RunValidityResult) -> None:
        """Requirement 1: lifecycle events — task must have been properly spawned.

        A properly spawned task has started_at set (engine recorded the start time).
        Without this, the task was never actually handed to a worker.
        """
        if self.started_at is not None:
            result.lifecycle_ok = True
        else:
            result.lifecycle_ok = False
            result.violations.append(ContractViolation(
                code=RVC_MISSING_LIFECYCLE,
                requirement="lifecycle_events",
                detail=(
                    f"Task {self.task_id[:8]} has no started_at timestamp — "
                    "it was never properly spawned. Lifecycle event 'spawned' is missing."
                ),
            ))

    def _check_transcript(self, result: RunValidityResult) -> Optional[Path]:
        """Requirement 3: transcript durability — file must exist and be readable.

        We confirm transcript durability *before* any compaction/deletion can
        occur.  Returns the found transcript Path so other checks can reuse it.
        """
        transcript: Optional[Path] = None

        if self._find_transcript is not None:
            try:
                transcript = self._find_transcript(
                    self.session_key,
                    transcript_hint=self.transcript_path,
                )
            except Exception as e:
                logger.debug(
                    "[RVC] Error finding transcript for %s: %s",
                    self.task_id[:8], e,
                )

        if transcript is not None and transcript.exists():
            result.transcript_found = True
        else:
            result.transcript_found = False
            result.violations.append(ContractViolation(
                code=RVC_NO_TRANSCRIPT,
                requirement="transcript_durability",
                detail=(
                    f"No transcript file found for session {self.session_key} "
                    f"(task {self.task_id[:8]}). Cannot confirm run durability."
                ),
            ))

        return transcript

    def _check_first_response(
        self,
        result: RunValidityResult,
        transcript: Optional[Path],
    ) -> None:
        """Requirements 2+4a: first-response event present and SLA met.

        Reads the transcript to find the first assistant message, then measures
        the time from task start to that message.
        """
        if transcript is None or not transcript.exists():
            # Already flagged by _check_transcript; also flag first_response
            result.sla_ok = False
            result.violations.append(ContractViolation(
                code=RVC_NO_FIRST_RESPONSE,
                requirement="first_response_event",
                detail=(
                    f"Cannot verify first_response for task {self.task_id[:8]}: "
                    "no readable transcript."
                ),
            ))
            return

        first_response_ts = self._read_first_response_timestamp(transcript)

        if first_response_ts is None:
            result.first_response_time_seconds = None
            result.sla_ok = False
            result.violations.append(ContractViolation(
                code=RVC_NO_FIRST_RESPONSE,
                requirement="first_response_event",
                detail=(
                    f"Transcript for task {self.task_id[:8]} contains no assistant "
                    "messages. Lifecycle event 'first_response' is missing."
                ),
            ))
            return

        # Measure time-to-first-response if we have a start reference
        if self.started_at is not None:
            start_ts = self.started_at.timestamp()
            delta = first_response_ts - start_ts
            result.first_response_time_seconds = round(delta, 1)

            if delta <= self.first_response_sla_seconds:
                result.sla_ok = True
            else:
                result.sla_ok = False
                result.violations.append(ContractViolation(
                    code=RVC_SLA_BREACH,
                    requirement="first_response_sla",
                    detail=(
                        f"First response for task {self.task_id[:8]} arrived after "
                        f"{delta:.0f}s, exceeding SLA of {self.first_response_sla_seconds}s."
                    ),
                ))
        else:
            # No started_at → can't measure, but we confirmed a response exists
            result.first_response_time_seconds = None
            result.sla_ok = True  # can't measure, give benefit of doubt

    def _check_evidence(self, result: RunValidityResult) -> None:
        """Requirement 4: evidence bundle — work summary OR artifact refs present."""
        has_summary = bool(self.result_summary and self.result_summary.strip())
        has_files = bool(self.files_modified)

        if has_summary or has_files:
            result.evidence_ok = True
        else:
            result.evidence_ok = False
            result.violations.append(ContractViolation(
                code=RVC_NO_EVIDENCE,
                requirement="evidence_bundle",
                detail=(
                    f"Task {self.task_id[:8]} produced neither a work summary "
                    "nor any file changes. Evidence bundle is missing."
                ),
            ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _read_first_response_timestamp(transcript: Path) -> Optional[float]:
        """Return the wall-clock timestamp of the first assistant message in the transcript.

        JSONL format: each line is a JSON object. We look for entries with
        type=="message" and message.role=="assistant".  The timestamp is taken
        from the top-level "timestamp" field (ISO-8601) if present, otherwise
        we fall back to the file mtime.
        """
        try:
            with open(transcript, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "message":
                        continue

                    msg = entry.get("message", {})
                    if msg.get("role") != "assistant":
                        continue

                    # Got the first assistant message — extract timestamp
                    raw_ts = entry.get("timestamp") or entry.get("ts")
                    if raw_ts:
                        try:
                            if isinstance(raw_ts, (int, float)):
                                return float(raw_ts)
                            dt = datetime.fromisoformat(str(raw_ts).rstrip("Z"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            return dt.timestamp()
                        except Exception:
                            pass

                    # Fall back to file mtime as a proxy
                    return transcript.stat().st_mtime

        except Exception as e:
            logger.debug("[RVC] Error reading transcript timestamps: %s", e)

        return None


# ── Convenience factory ───────────────────────────────────────────────────────


def make_checker_from_worker(
    *,
    task_id: str,
    started_at: Optional[datetime],
    worker_info: Any,  # WorkerInfo
    result_summary: Optional[str],
    files_modified: Optional[list[str]],
    worker_manager: Any,  # WorkerManager — provides _find_transcript_file + _read_transcript_assistant_messages
    first_response_sla_seconds: int = FIRST_RESPONSE_SLA_SECONDS,
) -> RunValidityChecker:
    """Build a RunValidityChecker from worker completion context."""
    return RunValidityChecker(
        task_id=task_id,
        started_at=started_at,
        session_key=worker_info.child_session_key,
        transcript_path=worker_info.transcript_path,
        result_summary=result_summary,
        files_modified=files_modified,
        find_transcript_fn=worker_manager._find_transcript_file,
        read_messages_fn=worker_manager._read_transcript_assistant_messages,
        first_response_sla_seconds=first_response_sla_seconds,
    )
