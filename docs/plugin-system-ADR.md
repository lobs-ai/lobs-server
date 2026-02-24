# ADR: Agent Plugin System for Custom Skills

**Date:** 2026-02-23  
**Status:** Proposed  
**Decision Makers:** Rafe, Lobs Architect Team  
**Stakeholders:** All agent types (programmer, researcher, writer, architect, project-manager)

---

## Context and Problem Statement

Agents currently have a **fixed set of capabilities** defined by their OpenClaw tools. Users cannot extend agent behavior with custom skills like:
- Domain-specific API integrations (e.g., Jira, Linear, GitHub beyond basic ops)
- Custom automation scripts (e.g., deployment workflows, code formatters)
- Specialized tools (e.g., CAD file processors, data pipelines)
- User-specific shortcuts (e.g., company-specific code generators)

**Problem:** No safe, structured way to add custom agent skills without modifying core orchestrator code.

**Goal:** Enable users to create, register, and deploy custom agent skills as plugins with:
1. **Clear interface** — Standardized manifest format
2. **Security model** — Sandboxed execution, permission controls
3. **Discoverability** — Agents can query available plugins
4. **Seamless integration** — Plugins feel like native tools to agents

---

## Decision Drivers

- **Extensibility:** Users should add skills without forking lobs-server
- **Security:** Untrusted plugins must run in isolated environments
- **Observability:** Plugin usage tracked for debugging and learning
- **Developer Experience:** Easy to create, test, and deploy plugins
- **Agent Integration:** Minimal changes to existing orchestrator/prompter flow
- **Performance:** Plugin loading/execution must not block orchestrator

---

## Considered Options

### Option 1: Script-Based Plugins (Chosen)
**Approach:** Plugins are executable scripts with JSON manifest descriptors.

**Pros:**
- Simple to implement and understand
- Language-agnostic (Python, Bash, Node.js, etc.)
- Easy to sandbox (run in subprocess with resource limits)
- Clear security model (filesystem, network, env var controls)

**Cons:**
- Inter-process communication overhead
- Limited to request/response patterns (no streaming initially)

### Option 2: Python Module Plugins
**Approach:** Plugins as importable Python modules with `execute()` method.

**Pros:**
- Native Python integration
- Better performance (no subprocess overhead)
- Easier debugging

**Cons:**
- Security risk (shared process space, harder to sandbox)
- Limited to Python only
- Complex isolation model

### Option 3: Containerized Plugins
**Approach:** Each plugin runs in its own Docker container.

**Pros:**
- Maximum isolation
- Language-agnostic
- Production-grade security

**Cons:**
- Heavy infrastructure requirement
- Slow startup times (100ms+ per invocation)
- Complexity overkill for simple scripts

**Decision:** **Option 1 (Script-Based Plugins)** for v1, with path to Option 3 for production.

---

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────┐
│               lobs-server                               │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Plugin Registry (SQLite)                 │  │
│  │  • plugin metadata (name, version, agent_type)   │  │
│  │  • permissions (filesystem, network, env)        │  │
│  │  • execution logs and metrics                    │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Plugin Executor Service                  │  │
│  │  • Load plugin manifest                          │  │
│  │  • Validate permissions                          │  │
│  │  • Run in subprocess with resource limits        │  │
│  │  • Capture stdout/stderr/exit code               │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Prompter Integration                     │  │
│  │  • Query available plugins for agent_type        │  │
│  │  • Inject plugin docs into system prompt         │  │
│  │  • Parse plugin calls from agent output          │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
           ┌───────────────────────────────┐
           │    Plugin Storage             │
           │  plugins/                     │
           │   ├── my-api-skill/           │
           │   │   ├── manifest.json       │
           │   │   ├── execute.py          │
           │   │   └── README.md           │
           │   └── deploy-script/          │
           │       ├── manifest.json       │
           │       └── run.sh              │
           └───────────────────────────────┘
```

### Plugin Manifest Format

```json
{
  "name": "github-pr-creator",
  "version": "1.0.0",
  "description": "Create GitHub pull requests from command line",
  "agent_types": ["programmer", "architect"],
  "author": "rafe@example.com",
  "executable": "execute.py",
  "runtime": "python3",
  "timeout_seconds": 30,
  "permissions": {
    "network": ["api.github.com"],
    "filesystem_read": [".git/config"],
    "filesystem_write": [],
    "environment_vars": ["GITHUB_TOKEN"]
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string", "description": "PR title"},
      "branch": {"type": "string", "description": "Source branch"},
      "base": {"type": "string", "description": "Target branch", "default": "main"}
    },
    "required": ["title", "branch"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "pr_url": {"type": "string"},
      "pr_number": {"type": "integer"}
    }
  },
  "examples": [
    {
      "input": {"title": "Fix auth bug", "branch": "fix/auth"},
      "output": {"pr_url": "https://github.com/...", "pr_number": 42}
    }
  ]
}
```

**Key Fields:**
- `name`: Unique plugin identifier (slug format)
- `agent_types`: Which agents can use this plugin
- `executable`: Script to run (relative to plugin directory)
- `runtime`: Execution environment (python3, bash, node, etc.)
- `permissions`: Security policy (whitelist-based)
- `input_schema`/`output_schema`: JSON Schema for validation
- `examples`: Help agents understand usage

### Plugin Execution Protocol

**1. Invocation Format (Agent Output)**
```
PLUGIN_CALL: github-pr-creator
INPUT: {"title": "Fix auth bug", "branch": "fix/auth"}
```

**2. Execution Flow**
1. Prompter detects `PLUGIN_CALL:` in agent output
2. Parse plugin name and input JSON
3. Load plugin manifest from registry
4. Validate input against `input_schema`
5. Check permissions (network, filesystem, env vars)
6. Execute plugin in subprocess:
   ```bash
   cd plugins/github-pr-creator
   timeout 30s python3 execute.py '{"title":"Fix auth bug","branch":"fix/auth"}'
   ```
7. Capture stdout (expected JSON output)
8. Validate output against `output_schema`
9. Return result to agent via next prompt turn
10. Log execution (duration, exit code, output) to database

**3. Plugin Script Interface**
All plugins receive input via:
- **Argument 1:** JSON string with input parameters

All plugins must:
- **Exit code 0:** Success
- **Exit code non-zero:** Failure
- **stdout:** JSON output matching `output_schema`
- **stderr:** Error messages/logs (captured separately)

Example `execute.py`:
```python
#!/usr/bin/env python3
import sys
import json
import os

def main():
    # Parse input
    input_data = json.loads(sys.argv[1])
    title = input_data['title']
    branch = input_data['branch']
    base = input_data.get('base', 'main')
    
    # Use allowed env vars
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print(json.dumps({"error": "GITHUB_TOKEN not set"}), file=sys.stderr)
        sys.exit(1)
    
    # Execute plugin logic
    # ... make API calls, run commands, etc ...
    
    # Return JSON output
    result = {
        "pr_url": "https://github.com/owner/repo/pull/42",
        "pr_number": 42
    }
    print(json.dumps(result))
    sys.exit(0)

if __name__ == "__main__":
    main()
```

---

## Database Schema

### plugins table
```sql
CREATE TABLE plugins (
    plugin_id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    version TEXT NOT NULL,
    description TEXT,
    author TEXT,
    manifest_json TEXT NOT NULL,  -- Full manifest
    enabled BOOLEAN DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_plugins_enabled ON plugins(enabled);
```

### plugin_permissions table
```sql
CREATE TABLE plugin_permissions (
    permission_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    permission_type TEXT NOT NULL,  -- 'network', 'filesystem_read', 'filesystem_write', 'env_var'
    resource TEXT NOT NULL,         -- e.g., 'api.github.com', '/tmp/*', 'GITHUB_TOKEN'
    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id),
    UNIQUE(plugin_id, permission_type, resource)
);

CREATE INDEX idx_plugin_permissions_plugin ON plugin_permissions(plugin_id);
```

### plugin_executions table
```sql
CREATE TABLE plugin_executions (
    execution_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    task_id TEXT,  -- Optional: link to task
    agent_type TEXT,
    input_json TEXT,
    output_json TEXT,
    error_message TEXT,
    exit_code INTEGER,
    duration_ms INTEGER,
    executed_at TEXT NOT NULL,
    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_plugin_executions_plugin ON plugin_executions(plugin_id);
CREATE INDEX idx_plugin_executions_task ON plugin_executions(task_id);
CREATE INDEX idx_plugin_executions_agent ON plugin_executions(agent_type);
```

---

## API Endpoints

### Plugin Management

**POST /api/plugins/register**
```json
Request:
{
  "manifest_path": "/path/to/plugin/manifest.json"
}

Response:
{
  "plugin_id": "plg_abc123",
  "name": "github-pr-creator",
  "status": "registered"
}
```

**GET /api/plugins**
```json
Query params: ?agent_type=programmer&enabled=true

Response:
{
  "plugins": [
    {
      "plugin_id": "plg_abc123",
      "name": "github-pr-creator",
      "version": "1.0.0",
      "description": "...",
      "agent_types": ["programmer"],
      "enabled": true
    }
  ]
}
```

**GET /api/plugins/{plugin_id}**
```json
Response:
{
  "plugin_id": "plg_abc123",
  "name": "github-pr-creator",
  "manifest": {...},
  "executions_count": 42,
  "success_rate": 0.95,
  "avg_duration_ms": 1200
}
```

**DELETE /api/plugins/{plugin_id}**
```json
Response:
{
  "status": "deleted"
}
```

### Plugin Execution (Internal)

**POST /api/plugins/execute** (Internal only - called by orchestrator)
```json
Request:
{
  "plugin_id": "plg_abc123",
  "input": {"title": "Fix bug", "branch": "fix/bug"},
  "task_id": "task_xyz",
  "agent_type": "programmer"
}

Response:
{
  "execution_id": "exec_def456",
  "output": {"pr_url": "...", "pr_number": 42},
  "exit_code": 0,
  "duration_ms": 1200
}
```

---

## Security Model

### Permission Types

1. **network:** Whitelist of domains/IPs plugin can access
   ```json
   "network": ["api.github.com", "192.168.1.0/24"]
   ```

2. **filesystem_read:** Paths plugin can read (glob patterns)
   ```json
   "filesystem_read": [".git/config", "/tmp/build/*"]
   ```

3. **filesystem_write:** Paths plugin can modify
   ```json
   "filesystem_write": ["/tmp/output/*"]
   ```

4. **environment_vars:** Env vars plugin can access
   ```json
   "environment_vars": ["GITHUB_TOKEN", "API_KEY"]
   ```

### Enforcement Strategy

**Phase 1 (v1):** Trust-based validation
- Check manifest permissions before execution
- Log violations
- Reject obvious violations (write to /etc, access secrets)

**Phase 2 (v2):** Sandboxed execution
- Use `bubblewrap` (Linux) or containers
- Enforce network restrictions via firewall rules
- Mount only allowed filesystem paths
- Filter environment variables

**Phase 3 (Production):** Container-based
- Migrate to Docker/Podman
- Full resource limits (CPU, memory, disk)
- Network policies
- Audit logging

---

## Prompter Integration

### Plugin Discovery Prompt Section

When building task prompts, prompter queries available plugins:

```python
# app/orchestrator/prompter.py

async def build_task_prompt(task: Task, agent_type: str) -> str:
    # ... existing prompt building ...
    
    # Add plugin section
    plugins = await plugin_registry.get_plugins(agent_type=agent_type, enabled=True)
    if plugins:
        prompt += "\n## Available Plugins\n\n"
        prompt += "You have access to these custom skills:\n\n"
        for plugin in plugins:
            prompt += f"### {plugin.name}\n"
            prompt += f"{plugin.description}\n\n"
            prompt += f"**Usage:**\n"
            prompt += f"```\n"
            prompt += f"PLUGIN_CALL: {plugin.name}\n"
            prompt += f"INPUT: {json.dumps(plugin.examples[0]['input'], indent=2)}\n"
            prompt += f"```\n\n"
            prompt += f"**Expected Output:** {json.dumps(plugin.examples[0]['output'])}\n\n"
    
    return prompt
```

### Plugin Response Handling

```python
# app/orchestrator/worker.py

async def process_agent_output(output: str, task: Task):
    # ... existing output processing ...
    
    # Detect plugin calls
    if "PLUGIN_CALL:" in output:
        plugin_calls = parse_plugin_calls(output)
        for call in plugin_calls:
            result = await plugin_executor.execute(
                plugin_name=call['name'],
                input_data=call['input'],
                task_id=task.task_id,
                agent_type=task.agent
            )
            # Inject result into next prompt turn
            await append_plugin_result(task, result)
```

---

## Implementation Phases

### Phase 1: Core Infrastructure (Week 1)
**Scope:** Basic plugin system without security

**Deliverables:**
- Database schema (migrations)
- Plugin models (SQLAlchemy)
- PluginRegistry service (CRUD operations)
- PluginExecutor service (subprocess execution)
- API endpoints (register, list, get, delete)
- Basic tests (unit + integration)

**Files to create:**
- `migrations/add_plugin_tables.sql`
- `app/models.py` (add Plugin, PluginPermission, PluginExecution)
- `app/services/plugin_registry.py`
- `app/services/plugin_executor.py`
- `app/routers/plugins.py`
- `tests/test_plugin_registry.py`
- `tests/test_plugin_executor.py`

**Acceptance:**
- Can register plugin from manifest
- Can execute simple Python plugin
- Execution logged to database
- All tests pass

### Phase 2: Orchestrator Integration (Week 2)
**Scope:** Connect plugins to agent workflow

**Deliverables:**
- Prompter integration (inject plugin docs)
- Worker integration (detect and execute plugin calls)
- Plugin call parser
- Result injection into agent context
- Example plugins (2-3 useful ones)

**Files to modify:**
- `app/orchestrator/prompter.py`
- `app/orchestrator/worker.py`

**Files to create:**
- `app/orchestrator/plugin_parser.py`
- `plugins/example-api-call/` (example plugin)
- `plugins/code-formatter/` (example plugin)
- `tests/test_plugin_integration.py`

**Acceptance:**
- Agent can discover plugins in prompt
- Agent can call plugin with correct syntax
- Plugin executes and returns result
- Result appears in agent's next prompt
- End-to-end test passes

### Phase 3: Security & Validation (Week 3)
**Scope:** Permission system and input validation

**Deliverables:**
- JSON Schema validation (input/output)
- Permission checker (before execution)
- Resource limits (timeout, max output size)
- Error handling and logging
- Security documentation

**Files to modify:**
- `app/services/plugin_executor.py` (add validation)

**Files to create:**
- `app/services/plugin_validator.py`
- `app/services/plugin_security.py`
- `docs/PLUGIN_SECURITY.md`
- `tests/test_plugin_security.py`

**Acceptance:**
- Invalid input rejected with clear error
- Permission violations blocked
- Timeout enforced (kills runaway plugins)
- Security tests pass

### Phase 4: Developer Experience (Week 4)
**Scope:** Tools for creating and testing plugins

**Deliverables:**
- Plugin template generator CLI
- Local testing tool
- Plugin documentation generator
- Developer guide
- 5+ example plugins

**Files to create:**
- `bin/create-plugin` (CLI tool)
- `bin/test-plugin` (local runner)
- `docs/PLUGIN_DEVELOPMENT.md`
- `plugins/*/` (example plugins)
- `tests/test_plugin_cli.py`

**Acceptance:**
- Can scaffold new plugin in 30 seconds
- Can test plugin locally without server
- Clear docs for plugin authors
- 5 diverse examples available

---

## Testing Strategy

### Unit Tests
- PluginRegistry CRUD operations
- PluginExecutor subprocess handling
- Plugin manifest validation
- Permission checking logic
- Input/output schema validation

### Integration Tests
- Register plugin → execute → verify output
- Prompter plugin injection
- Worker plugin call detection and execution
- Error handling (timeout, invalid input, permission denied)

### End-to-End Tests
1. Register GitHub PR plugin
2. Create task for programmer agent
3. Agent discovers plugin in prompt
4. Agent calls plugin with valid input
5. Plugin executes successfully
6. Result injected into agent context
7. Agent completes task using plugin output

### Security Tests
- Attempt to access forbidden filesystem paths
- Attempt to access forbidden network domains
- Attempt to access forbidden env vars
- Attempt to exceed timeout
- Attempt to produce invalid output schema

---

## Success Metrics

### Adoption Metrics
- **Plugin count:** 10+ plugins created within 4 weeks of launch
- **Usage rate:** 20% of tasks use at least one plugin
- **Agent coverage:** All 5 agent types have 2+ relevant plugins

### Performance Metrics
- **Execution time:** P95 < 2 seconds for typical plugins
- **Success rate:** 95%+ plugin executions succeed
- **Error rate:** < 5% due to plugin bugs (vs orchestrator bugs)

### Developer Experience
- **Time to create plugin:** < 15 minutes for simple script
- **Documentation completeness:** 100% of manifest fields documented
- **Developer satisfaction:** Survey plugin creators after 30 days

---

## Risks and Mitigations

### Risk 1: Security vulnerabilities
**Impact:** High - malicious plugins could compromise system

**Mitigations:**
- Phase 1: Manual review of all plugins before registration
- Phase 2: Automated permission validation
- Phase 3: Sandboxed execution (containers)
- Admin approval required for plugins with sensitive permissions
- Audit log all plugin executions

### Risk 2: Performance degradation
**Impact:** Medium - slow plugins block orchestrator

**Mitigations:**
- Enforce strict timeouts (30s default, configurable)
- Run plugin execution async (don't block orchestrator loop)
- Rate limit plugin calls per task (max 10)
- Monitor and alert on slow plugins

### Risk 3: Poor developer experience
**Impact:** Medium - no one creates plugins

**Mitigations:**
- Comprehensive documentation with examples
- Plugin template generator
- Local testing tools
- Community showcase (share best plugins)
- Regular plugin developer office hours

### Risk 4: Version compatibility
**Impact:** Low - plugin breaks after server update

**Mitigations:**
- Semantic versioning for plugin API
- Deprecation warnings (6 months notice)
- Compatibility test suite
- Plugin manifest version field

---

## Alternatives Considered

### Why not WebAssembly (WASM)?
**Pros:** Better sandboxing, faster execution  
**Cons:** Requires WASM compilation, limited ecosystem  
**Decision:** Too early - wait for WASM tooling to mature

### Why not gRPC/REST API plugins?
**Pros:** Language-agnostic, better for long-running services  
**Cons:** Requires infrastructure (hosting, discovery, auth)  
**Decision:** Good for v2 - start simple with scripts

### Why not embedded scripting (Lua/JavaScript)?
**Pros:** No subprocess overhead  
**Cons:** Limited ecosystem, harder to sandbox  
**Decision:** Consider for Phase 3 if performance critical

---

## Open Questions

1. **Plugin marketplace?** Should we build a central registry for sharing plugins?
   - **Decision:** Not yet - start with local-only, evaluate demand

2. **Plugin dependencies?** Can plugins depend on other plugins?
   - **Decision:** No for v1 - keep it simple

3. **Streaming output?** Can plugins stream results incrementally?
   - **Decision:** No for v1 - use async/polling pattern if needed in v2

4. **Plugin pricing?** Commercial plugins with usage fees?
   - **Decision:** Out of scope - all plugins free for v1

---

## References

- [OpenClaw Tool System](https://openclaw.ai/docs/tools)
- [JSON Schema Specification](https://json-schema.org/)
- [Linux Namespaces & Sandboxing](https://man7.org/linux/man-pages/man7/namespaces.7.html)
- [GitHub Actions Workflow Syntax](https://docs.github.com/en/actions/reference/workflow-syntax-for-github-actions) (inspiration for manifest format)

---

## Approval

**Recommendation:** APPROVE with Phase 1-2 for immediate implementation.

**Rationale:**
- Clear extensibility need (users already requesting custom integrations)
- Low-risk implementation path (start simple, iterate)
- Strong security plan (evolve from trust → validation → sandbox)
- Aligns with agent learning system (plugins become observable skills)

**Next Steps:**
1. Review this ADR with stakeholders
2. Get approval from Rafe
3. Create handoff for Phase 1 implementation
4. Implement MVP (4 weeks)
5. Launch with 3-5 example plugins
6. Gather feedback and iterate

---

**Signatures:**

Architect: _________________ Date: _______  
Product Owner (Rafe): _________________ Date: _______  
Lead Programmer: _________________ Date: _______
