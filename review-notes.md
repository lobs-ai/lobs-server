# Diagnostic Review Notes — Task `diag_d50b583b-820f-4988-98eb-039d6489a826_1771883059`

## Scope
Analyze repeated failure of original task `d50b583b-820f-4988-98eb-039d6489a826` ("Document deployment architecture with setup scripts").

## What I found

### 1) Root cause (primary)
**Worker completion detection is treating deleted/compacted transcripts with no assistant message as hard task failure, but the system does not preserve enough failure payload to recover or route around it.**

Evidence:
- Failure reason surfaced: `No assistant response in deleted transcript`.
- This string is emitted by orchestrator code when transcript file has `.deleted.*` suffix and parsed assistant messages are empty:
  - `app/orchestrator/worker.py` (`_check_session_status`, around lines ~740-755)
  - `app/orchestrator/worker_gateway.py` has the same logic.
- Logs show multiple successful worker spawns across model/provider/agent switches for this same task, but no usable assistant output persisted:
  - `logs/server.log` around 26695, 26963, 27452, 27824 (writer/researcher spawned on different models)
- Escalation kept triggering without new actionable error context (`Failure pattern detected ... 3 retries, tier 3`).

### 2) Contributing system issue
**Persistence path is fragile under contention (SQLite lock), so even diagnostic/reflection outputs can fail to save.**

Evidence:
- `logs/server.log:33601` shows `sqlite3.OperationalError: database is locked` while persisting reflection output in `app/orchestrator/worker.py` (`_persist_reflection_output`).
- This increases chance of repeated retries with poor memory of prior failure details.

## Recommended fix / workaround

### Immediate workaround (operational)
1. **Run this task via `programmer` agent on `openai-codex/gpt-5.3-codex` only** (single model, single retry) and disable auto-escalation loop for this task instance.
2. **Shorten prompt payload for this run** (keep only task objective + concrete deliverables) to reduce early session abort risk.
3. If possible, **capture artifacts directly in repo** (`DEPLOYMENT.md`, `bin/setup_deploy.sh`, `bin/verify_deploy.sh`) instead of relying on transcript-only completion signals.

### Code-level fixes (should be implemented)
1. **Harden completion contract for deleted transcripts**
   - If transcript is deleted and no assistant message exists, persist structured failure metadata (`provider_exit_status`, `stderr_excerpt`, `session_end_reason`, `transcript_path`) instead of generic string.
2. **Add first-response ACK for diagnostics/tasks**
   - Require/persist minimal assistant ACK within N seconds; if absent, fail fast with actionable infra classification (provider/tool/runtime) rather than retrying blind.
3. **Add DB lock retry/backoff for reflection/task-result persistence**
   - Wrap SQLite commit with bounded retry + jitter to avoid losing diagnostic context.

## Retry / modify / escalate decision
**Decision: MODIFY then RETRY (do not escalate to human yet).**

Reasoning:
- This is primarily an orchestration reliability issue, not a product ambiguity issue.
- Task content itself is valid and should be completable once run path is constrained and completion/persistence handling is improved.
- Escalate only if a constrained retry (single agent/model + reduced prompt + direct file artifacts) still fails.

## Suggested handoff
```json
{
  "to": "programmer",
  "initiative": "code-review-fixes",
  "title": "Harden worker failure classification for deleted transcripts and persistence lock retries",
  "context": "Task d50b583b repeatedly failed with 'No assistant response in deleted transcript'. See review-notes.md diagnostic evidence and logs/server.log entries around 26695, 26963, 27452, 27824, 33601.",
  "acceptance": "Deleted transcript failures include structured root-cause metadata; DB-locked writes retry with backoff; diagnostic tasks persist actionable failure context; tests cover no-assistant/deleted-transcript and DB-lock retry paths.",
  "files": ["app/orchestrator/worker.py", "app/orchestrator/worker_gateway.py", "app/orchestrator/escalation_enhanced.py", "tests/"]
}
```
