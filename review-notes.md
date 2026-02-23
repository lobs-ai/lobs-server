# Diagnostic Review Notes — Task `diag_C469A3C2-79B5-431E-A171-142979360E5D_1771885518`

## Scope
Analyze repeated failure of original task `C469A3C2-79B5-431E-A171-142979360E5D` ("Add knowledge_entries table and Knowledge API").

## Findings

### 1) Root cause (primary)
**This is an orchestrator/session execution failure, not a missing-design or coding-requirements problem.**

Evidence:
- Task state shows infra-style terminal reason, not implementation error:
  - `tasks.failure_reason = "No assistant response in deleted transcript"`
  - `retry_count = 3`, `escalation_tier = 3`
- All attempts died the same way, across different agents/models:
  - `worker_runs` IDs 9259, 9261, 9262, 9263 all have `timeout_reason = exit_code_-1`, `succeeded = 0`
  - token usage is `0 input / 0 output` for all runs
  - switched from `programmer` to `architect` with no behavior change
- The failure string maps directly to orchestrator logic in `app/orchestrator/worker.py` (and `worker_gateway.py`):
  - `_check_session_status()` treats a `.deleted` transcript with no assistant message as terminal failure: `"No assistant response in deleted transcript"`.

Interpretation:
- Workers are being spawned, but sessions are ending/being deleted before any assistant response is captured.
- Because there are no tokens and no assistant output, this is upstream of task implementation (session lifecycle / runtime stability / gateway integration), not caused by the knowledge-system spec itself.

### 2) Contributing factors
- **Intermittent SQLite lock contention** is visible in the same timeframe (reflection/usage persistence warnings), which degrades reliability and observability during retries.
- Task complexity is high (schema + API + indexer + scheduler + deprecations in one request), so when orchestration is unstable, long jobs are more likely to repeatedly fail before first usable output.

## Recommended fix / workaround

### Immediate workaround (to get delivery moving)
1. **Modify the task into smaller slices** and run sequentially:
   - A: migration + model only
   - B: API router endpoints only
   - C: indexer/sync service
   - D: periodic scheduler + deprecation flags/docs
2. **Run first retry as a constrained execution** (single agent/model, low contention window).
3. **Preflight session health** (ensure spawned session remains queryable and transcript path is stable before long execution).

### Platform/orchestrator fix (recommended)
1. In `worker.py` / `worker_gateway.py`, distinguish:
   - deleted transcript with process crash
   - deleted transcript due cleanup race
   - genuinely completed-but-empty transcript
2. Add short retry/backoff before terminalizing on deleted-empty transcript.
3. Persist richer diagnostics on failure (runId, childSessionKey, transcript path, poll attempts, last gateway responses).
4. Add retry/backoff for DB commit on non-critical reflection/usage writes to reduce lock-related cascading instability.

## Retry / modify / escalate decision
**Decision: MODIFY then RETRY.**

- The original implementation request is valid (design doc exists at `~/lobs-shared-memory/docs/designs/unified-knowledge-system.md`).
- Current failures are dominated by session/orchestration reliability, not spec ambiguity.
- If a modified/sliced retry still fails with deleted-empty transcripts, **escalate to infrastructure/orchestrator owners** before further feature retries.

## Suggested handoff
```json
{
  "to": "programmer",
  "initiative": "code-review-fixes",
  "title": "Harden orchestrator handling of deleted transcripts with no assistant output",
  "context": "Task C469A3C2 failed 4 consecutive runs (programmer/architect) with failure_reason='No assistant response in deleted transcript', exit_code_-1, and zero token usage. See review-notes.md and app/orchestrator/worker.py::_check_session_status.",
  "acceptance": "Orchestrator no longer prematurely terminal-fails on deleted-empty transcripts without retry/recheck; structured diagnostics are persisted for each failure; tests cover deleted transcript + no assistant + delayed session visibility paths.",
  "files": ["app/orchestrator/worker.py", "app/orchestrator/worker_gateway.py", "tests/"]
}
```