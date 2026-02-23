# 12. Tool Access Policies

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

AI agents in the lobs system have access to powerful tools that can modify code, execute commands, and interact with external systems. We needed to decide:

- **Which tools should each agent type have access to?**
- **What restrictions should apply to prevent unintended consequences?**
- **How do we balance capability vs. safety?**
- **Should all agents have the same tools, or should access be role-specific?**

Early implementations gave all agents full tool access (read, write, exec, browser, etc.). This caused problems:

**Safety Issues:**
- Researcher agents accidentally committing code changes
- Writer agents running destructive shell commands
- Agents pushing changes to wrong branches
- Unintended file deletions in critical directories

**Confusion:**
- Agents unsure which tools to use for a task
- Overlapping responsibilities (should researcher or programmer run tests?)
- Prompt pollution (documenting all tools even if agent shouldn't use them)

**Audit Challenges:**
- Hard to trace which agent made which changes
- Unclear why a file was modified (research or implementation?)
- Tool misuse difficult to detect

We needed a policy that:
- Grants agents **only the tools they need** for their role
- Prevents accidental misuse (researcher can't commit code)
- Provides clear boundaries (programmer writes code, reviewer reads it)
- Supports safe experimentation (read-only tools for research)
- Scales to new tools and agent types

## Decision

We adopt a **role-based tool access policy** where each agent type receives a **curated toolset** matched to their responsibilities, with **safety constraints** on high-risk operations.

### Tool Access Matrix

| Tool | Programmer | Researcher | Architect | Writer | Reviewer |
|------|------------|------------|-----------|--------|----------|
| **read** | ✅ Full | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| **write** | ✅ Full | ❌ No | ✅ Docs only | ✅ Docs only | ❌ No |
| **edit** | ✅ Full | ❌ No | ✅ Docs only | ✅ Docs only | ❌ No |
| **exec** | ✅ Tests/build | ⚠️ Read-only | ⚠️ Analysis | ❌ No | ⚠️ Read-only |
| **browser** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **web_search** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ⚠️ Limited |
| **web_fetch** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **message** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **git** | ❌ Orchestrator only | ❌ No | ❌ No | ❌ No | ❌ No |

**Legend:**
- ✅ Full access
- ⚠️ Restricted (specific use cases only)
- ❌ No access

### Agent-Specific Tool Policies

#### Programmer

**Role:** Write, modify, and test code.

**Tools:**
- **read** — Read any file (code, docs, configs)
- **write/edit** — Write/modify any file in project
- **exec** — Run tests, build, lint, type-check
  - ✅ `pytest`, `npm test`, `swift test`
  - ✅ `mypy`, `ruff`, `eslint`
  - ✅ `npm run build`, `swift build`
  - ❌ `rm -rf`, `git push`, `curl` to external APIs
- **browser** — Check documentation, API references
- **web_search/fetch** — Research libraries, patterns

**Constraints:**
- No git operations (orchestrator handles commits/pushes)
- No external API calls (prevent data exfiltration)
- No system-level commands (no `sudo`, `shutdown`, etc.)

**Rationale:** Programmer needs full code access to implement features. Exec is limited to development tools (tests, linting) to prevent accidental damage.

#### Researcher

**Role:** Investigate, analyze, and document findings.

**Tools:**
- **read** — Read any file (code, docs, data)
- **exec** — Read-only analysis commands
  - ✅ `ls`, `grep`, `rg`, `find`, `cat`, `head`, `tail`
  - ✅ `wc`, `du`, `tree`
  - ❌ **No write operations** — No `touch`, `mkdir`, `rm`, `mv`
  - ❌ **No code execution** — No `python script.py`, `node app.js`
- **browser** — Navigate websites, test UIs
- **web_search/fetch** — Primary research tools

**Constraints:**
- **No write/edit** — Researcher reads but doesn't modify files
- **No code execution** — Can't run scripts or tests
- Findings documented in new files (created via handoff to writer)

**Rationale:** Researcher explores without modifying. Read-only exec prevents accidental changes. Findings handed off to other agents for implementation.

#### Architect

**Role:** Design systems, document decisions, plan implementations.

**Tools:**
- **read** — Read any file
- **write/edit** — Write/modify documentation
  - ✅ `docs/**/*.md`, `*.md` (design docs, ADRs, architecture)
  - ✅ `ARCHITECTURE.md`, `CONTRIBUTING.md`, `README.md`
  - ❌ `src/**/*.py`, `app/**/*.ts` (code is programmer's domain)
- **exec** — Analysis commands only
  - ✅ `tree`, `grep`, `find` (project structure analysis)
  - ❌ No test execution, no builds
- **browser** — Research patterns, technologies
- **web_search/fetch** — Research best practices

**Constraints:**
- **No code changes** — Architect designs, programmer implements
- **Docs only** — write/edit limited to markdown files in docs/
- Hands off implementation to programmer

**Rationale:** Architect focuses on high-level design. Can update architecture docs but not implementation. Creates handoffs for code changes.

#### Writer

**Role:** Create and maintain documentation.

**Tools:**
- **read** — Read code and existing docs (to understand what to document)
- **write/edit** — Write/modify documentation
  - ✅ `docs/**/*.md`, `README.md`, API docs
  - ✅ Code comments (docstrings, JSDoc)
  - ❌ Implementation code (no logic changes)
- **browser** — Check rendered docs, test links
- **web_search/fetch** — Research documentation best practices

**Constraints:**
- **Docs and comments only** — Can add docstrings but not change function logic
- **No execution** — Writer doesn't run tests or builds
- Focuses on clarity and completeness

**Rationale:** Writer improves documentation without touching implementation. Can add code comments for readability but can't change behavior.

#### Reviewer

**Role:** Audit code, identify issues, suggest improvements.

**Tools:**
- **read** — Read any file (full codebase access)
- **exec** — Read-only analysis + test execution
  - ✅ `pytest`, `npm test` (run tests to verify claims)
  - ✅ `grep`, `rg`, `find` (search for patterns)
  - ✅ Static analysis (`mypy`, `ruff`, `eslint`)
  - ❌ No write operations
- **browser** — Check documentation, verify claims
- **web_fetch** — Research vulnerabilities, best practices

**Constraints:**
- **No write/edit** — Reviewer reads and reports, doesn't fix
- **No code execution** — Can run tests but not arbitrary scripts
- Creates handoffs to programmer for fixes

**Rationale:** Reviewer has wide read access to audit thoroughly but can't modify (separation of concerns). Findings handed off to programmer.

### Tool Constraints by Category

#### File System Operations

**Allowed paths (write/edit):**
- ✅ Project root and subdirectories (src/, app/, tests/, docs/)
- ✅ .handoffs/ (for creating handoffs)
- ✅ memory/ (for agent memory)
- ❌ System directories (/etc/, /usr/, /var/)
- ❌ Other projects (~/other-project/)
- ❌ Template files (AGENTS.md, SOUL.md, etc.)

#### Command Execution

**Allowed commands (exec):**
- ✅ Development tools (pytest, npm, ruff, mypy, eslint)
- ✅ Read-only commands (grep, find, ls, cat)
- ✅ Build tools (make, npm run build, swift build)
- ❌ Destructive commands (rm -rf, dd, mkfs)
- ❌ Network commands (curl, wget, ssh, scp)
- ❌ System admin (sudo, systemctl, reboot)
- ❌ Git operations (git commit, git push)

**Rationale:** Prevent accidental data loss, network exfiltration, privilege escalation.

#### Network Access

**Allowed:**
- ✅ web_search (Brave API, rate-limited)
- ✅ web_fetch (HTTP GET for documentation)
- ✅ browser (controlled navigation)
- ❌ Raw HTTP (curl, wget)
- ❌ SSH/SCP (no remote access)
- ❌ Database connections (except via app code during tests)

**Rationale:** Web tools are mediated by OpenClaw (logging, rate limits). Raw network access could leak data.

#### External Integrations

**Allowed:**
- ❌ message (no unsupervised messaging)
- ❌ tts (no audio generation)
- ❌ canvas (no UI presentation)
- ❌ nodes (no device control)

**Rationale:** Worker agents shouldn't contact users directly. Main orchestrator handles external communication.

### Enforcement Mechanism

**Layer 1: Documentation (Soft Enforcement)**
- TOOLS.md for each agent type lists available tools
- Agent prompts emphasize tool constraints
- Agents self-regulate based on instructions

**Layer 2: OpenClaw Policy (Hard Enforcement)**
- OpenClaw Gateway enforces tool policy per agent session
- Unauthorized tool calls rejected with error
- Policy violations logged for audit

**Layer 3: Orchestrator Monitoring (Detection)**
- Orchestrator reviews agent transcripts for tool misuse
- Flags violations (researcher used write, programmer called curl)
- Creates alerts for human review

**Example OpenClaw policy configuration:**
```yaml
# agents/programmer/policy.yml
allowed_tools:
  - read
  - write
  - edit
  - exec
  - browser
  - web_search
  - web_fetch

exec_allowlist:
  - pytest
  - python -m pytest
  - npm test
  - mypy
  - ruff
  - eslint
  - npm run build
  - swift build
  - swift test

exec_blocklist:
  - rm -rf
  - sudo
  - git push
  - curl
  - wget
```

## Consequences

### Positive

- **Safety boundaries** — Agents can't accidentally perform destructive operations
- **Clear roles** — Each agent knows exactly which tools to use
- **Audit trail** — Tool usage logged, violations detectable
- **Separation of concerns** — Researcher reads, programmer writes, reviewer audits
- **Reduced prompt size** — TOOLS.md only documents relevant tools
- **Easier onboarding** — New agent types get explicit tool list
- **Prevents mistakes** — Researcher can't accidentally commit code

### Negative

- **Less flexibility** — Agent may be blocked from legitimate use case
- **Policy maintenance** — Need to update policies as tools evolve
- **False constraints** — Some tasks might benefit from cross-role tool access
- **Workarounds** — Agents might find creative ways to work around restrictions
- **Configuration complexity** — Policy files per agent type

### Neutral

- Some tools (browser, web_search) available to all agents
- Policies are guidelines, not absolute blocks (agents can violate if needed)

## Alternatives Considered

### Option 1: Uniform Tool Access (All Agents Same Tools)

- **Pros:**
  - Simple (one policy for all)
  - Flexible (agent chooses tool based on task)
  - Less configuration

- **Cons:**
  - **Safety risk** — All agents can perform destructive operations
  - **Role confusion** — Unclear which agent should do what
  - **Prompt bloat** — All agents document all tools

- **Why rejected:** Too risky. Researcher agents were accidentally committing code.

### Option 2: Task-Based Tool Access (Tools Granted Per Task)

- **Pros:**
  - Fine-grained (tools match task needs exactly)
  - Minimal access (only what's needed for this task)

- **Cons:**
  - **Complex** — Orchestrator must decide tools per task
  - **Rigid** — Agent can't adapt if task requires unexpected tool
  - **High overhead** — Policy decision for every task

- **Why rejected:** Over-engineered. Role-based is simpler and sufficient.

### Option 3: Prompt-Based Constraints (No Enforcement)

- **Pros:**
  - Simple (just document in prompt)
  - Flexible (agent can break rule if needed)
  - No technical enforcement needed

- **Cons:**
  - **Unreliable** — Agents ignore constraints (especially under pressure)
  - **No safety net** — Accidental misuse not prevented
  - **Hard to audit** — Can't detect violations programmatically

- **Why rejected:** Tried this first. Agents regularly violated constraints (unintentionally).

### Option 4: Capability-Based Access (Fine-Grained Permissions)

- **Pros:**
  - Very precise (read:src/auth/*, write:docs/*)
  - Provable security (capability model is formal)
  - Scales to complex permissions

- **Cons:**
  - **Complex** — Need capability tokens, delegation logic
  - **Overkill** — Current needs are simple (role-based is enough)
  - **Hard to reason about** — Developers struggle with fine-grained permissions

- **Why rejected:** Premature. Role-based is sufficient. Can add capabilities later if needed.

## Special Cases

### Case 1: Programmer Needs to Read External API

**Scenario:** Implementing API integration, need to check docs.

**Solution:** Use browser or web_fetch (allowed for programmer).

**Rationale:** Reading documentation is safe. Actual API calls happen in code (tested, reviewed).

### Case 2: Researcher Needs to Run Proof-of-Concept

**Scenario:** Testing a library to see if it works.

**Solution:** 
- Option A: Create PoC in `.research/` directory (no project impact)
- Option B: Hand off to programmer ("can you test if library X works?")

**Rationale:** Researcher focuses on investigation, programmer handles implementation tests.

### Case 3: Writer Needs to Verify Code Example

**Scenario:** Writing tutorial, want to ensure code example actually runs.

**Solution:**
- Option A: Writer documents example, programmer reviews + tests it
- Option B: Grant writer read-only exec for verification (future enhancement)

**Rationale:** Writer shouldn't run arbitrary code. Programmer validates technical accuracy.

### Case 4: Reviewer Finds Security Vulnerability

**Scenario:** Needs to test exploit to confirm severity.

**Solution:**
- Reviewer documents exploit in report
- Hands off to programmer: "Verify this exploit works, then fix"

**Rationale:** Reviewer audits, programmer implements fix. Separation prevents reviewer from accidentally deploying exploit.

## Testing Strategy

**Tool access tests:**
- Researcher attempts write → Verify blocked by policy
- Programmer runs tests → Verify allowed
- Writer modifies code → Verify policy warning (docs-only)

**Command execution tests:**
- Programmer runs `pytest` → Allowed
- Programmer runs `rm -rf /` → Blocked by allowlist
- Researcher runs `python script.py` → Blocked

**Policy violation detection:**
- Agent uses unauthorized tool → Logged in transcript
- Orchestrator flags violation → Creates alert

**Role compliance tests:**
- Spawn 100 tasks across all agent types
- Audit transcripts for tool misuse
- Verify <1% policy violations

## Migration

**From:** Uniform tool access (all agents same tools)  
**To:** Role-based policies

**Steps:**
1. Document tool policies in agents/*/TOOLS.md (week 1)
2. Update agent prompts to emphasize constraints (week 1)
3. Configure OpenClaw policies per agent type (week 2)
4. Monitor for violations (week 3-4)
5. Refine policies based on observed behavior (ongoing)

**Rollback:** Remove policy enforcement in OpenClaw, rely on prompt-based constraints

## Future Enhancements

1. **Dynamic tool grants** — Orchestrator can grant tool for single task
2. **Tool usage analytics** — Track which tools each agent uses most
3. **Anomaly detection** — Flag unusual tool usage (programmer using web_search 100×)
4. **Sandboxed exec** — Run commands in containers for safety
5. **Capability tokens** — Fine-grained permissions for advanced use cases

## References

- `agents/*/TOOLS.md` — Per-agent tool documentation
- `worker-template/TOOLS.md` — Worker tool guidelines
- OpenClaw tool policy configuration (Gateway)
- ADR-0008: Agent Specialization Model
- ADR-0009: Workspace Isolation Strategy

## Open Questions

1. **Should we allow emergency tool grants (human overrides policy)?**  
   → Yes. Add `override_tools: [write]` in task metadata for exceptional cases.

2. **Should tool violations auto-fail tasks?**  
   → No. Log violation, let agent complete, human reviews. Hard failures might block legitimate work.

3. **Should we sandbox all exec calls (containers)?**  
   → Not yet. Current allowlist is sufficient. Add sandboxing if we see repeated misuse.

4. **Should agents be able to request tools ("I need write access for this")?**  
   → Interesting. Could hand off to orchestrator: "Grant me write access to X because Y." Future enhancement.

---

*Based on Michael Nygard's ADR format*
