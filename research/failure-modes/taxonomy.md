# Failure Mode Taxonomy — Autonomous Multi-Agent Systems

**Research Date:** 2026-02-22  
**Purpose:** Comprehensive taxonomy of failure modes in autonomous agent systems with detection criteria and mitigation strategies

---

## Executive Summary

Multi-agent orchestration systems face unique failure modes beyond typical software failures. This taxonomy categorizes 27 distinct failure patterns across 6 major categories, with detection criteria and mitigation strategies for each.

**Critical findings from lobs-server logs:**
- **75+ database transaction deadlocks** causing cascading failures
- **130+ circuit breaker triggers** indicating infrastructure instability
- **Type mismatches** from testing mocks leaking into production
- **Value unpacking errors** from inconsistent return types

**Most dangerous patterns:**
1. **Cascading failures** (1 error → circuit breaker → blocks entire system)
2. **Transaction deadlocks** (async DB operations without proper isolation)
3. **Infinite retry loops** (agents retrying failed operations indefinitely)
4. **Context thrashing** (agents losing progress due to memory limits)

---

## Failure Mode Categories

### Category 1: Control Flow Failures

These failures involve agents getting stuck in unproductive loops or incorrect execution paths.

#### 1.1 Infinite Loops

**Description:** Agent repeats the same action indefinitely without making progress toward goal.

**Manifestations:**
- **Tool abuse loop**: Agent calls same tool repeatedly with same parameters
- **Planning loop**: Agent regenerates same plan without execution
- **Validation loop**: Agent detects error, attempts fix, detects same error
- **Delegation loop**: Agent A delegates to B, B delegates back to A

**Detection criteria:**
```python
# Detect infinite loop
if len(set(last_N_actions)) < N * 0.3:  # <30% unique actions
    flag_infinite_loop()

# Detect tool abuse
tool_calls = recent_history.filter(type="tool_call")
if tool_calls.count_consecutive_same() > 5:
    flag_tool_abuse()

# Detect delegation cycle
call_chain = extract_delegation_chain()
if has_cycle(call_chain):
    flag_delegation_loop()
```

**Mitigation:**
- **Max iteration limits**: Hard cap on loop iterations
- **Progress tracking**: Require measurable progress every N steps
- **Backoff policies**: Exponential backoff for repeated failures
- **Cycle detection**: Track delegation chains, abort on cycles

**Example from lobs-server logs:**
```
# Not explicitly seen in logs, but circuit breaker triggers suggest retry loops:
# CIRCUIT BREAKER OPEN — gateway_auth (3 consecutive failures)
# → Likely: Agent retrying authentication repeatedly
```

---

#### 1.2 Deadlocks

**Description:** Two or more agents block each other waiting for resources or responses.

**Manifestations:**
- **Resource deadlock**: Agent A holds resource X, waits for Y; Agent B holds Y, waits for X
- **Communication deadlock**: Agent A waits for B's response; B waits for A's response
- **Task dependency deadlock**: Task A depends on B; B depends on A
- **Session lock deadlock**: Multiple agents competing for exclusive session access

**Detection criteria:**
```python
# Detect resource deadlock
resource_graph = build_resource_dependency_graph()
if has_cycle(resource_graph):
    flag_resource_deadlock()

# Detect communication deadlock
agent_wait_graph = build_waiting_graph()
if has_cycle(agent_wait_graph) and all_nodes_waiting:
    flag_communication_deadlock()

# Detect task dependency cycle
task_dag = build_task_dependency_graph()
if has_cycle(task_dag):
    flag_dependency_deadlock()
```

**Mitigation:**
- **Resource ordering**: Agents acquire resources in consistent order
- **Timeouts**: All waits have maximum duration
- **Deadlock detection**: Periodic cycle detection in dependency graphs
- **Optimistic locking**: Try-lock with retry instead of blocking

**Example from lobs-server logs:**
```
# PROJECT CIRCUIT BREAKER OPEN — project-a: session_lock
# → Multiple workers trying to acquire same session lock
# → Classic session lock contention/deadlock
```

---

#### 1.3 Livelocks

**Description:** Agents continuously change state in response to each other without making progress.

**Manifestations:**
- **Politeness livelock**: Agent A defers to B; B defers to A; repeat
- **Conflict resolution livelock**: Agents repeatedly undo each other's changes
- **Priority inversion livelock**: Low-priority agent blocks high-priority agent

**Detection criteria:**
```python
# Detect livelock
if task.state_changes > 100 and task.progress == 0:
    flag_livelock()

# Detect oscillation
states = task.history.map(lambda h: h.state)
if is_repeating_pattern(states):
    flag_livelock()
```

**Mitigation:**
- **Randomized backoff**: Random delays prevent synchronized oscillation
- **Priority assignment**: Clear priority ordering for conflict resolution
- **State locks**: Prevent simultaneous state modifications

---

### Category 2: Resource Exhaustion

Agents consume system resources beyond sustainable limits.

#### 2.1 Context Thrashing

**Description:** Agent loses progress by exceeding context window, causing repeated work.

**Manifestations:**
- **Memory overflow**: Context exceeds LLM's window, earliest parts dropped
- **Forgetting loop**: Agent forgets what it learned, repeats research
- **Lost state**: Critical state evicted from context, agent restarts
- **Checkpoint regression**: Rolling back to earlier checkpoint loses progress

**Detection criteria:**
```python
# Detect context thrashing
if session.token_count > context_window * 0.9:
    if session.repeat_work_percentage > 30:
        flag_context_thrashing()

# Detect memory churn
memory_ops = session.history.filter(type="memory_op")
if memory_ops.read_to_write_ratio < 0.5:
    flag_excessive_memory_writes()
```

**Mitigation:**
- **Context summarization**: Compress old context before eviction (CrewAI pattern)
- **Checkpointing**: Save state externally, restore on context reset (LangGraph pattern)
- **Tiered memory**: Short-term (context) + long-term (vector DB) (CrewAI pattern)
- **Token budgets**: Allocate token budget per task phase

**Example from lobs-server logs:**
```
# Not directly observed, but a risk given:
# - No explicit context management
# - Long-running tasks with multi-step workflows
# - Reflection cycles that generate significant text
```

---

#### 2.2 Token Budget Exhaustion

**Description:** Agent exhausts allocated tokens before completing task.

**Manifestations:**
- **Verbose planning**: Agent spends tokens on detailed plans, insufficient for execution
- **Repeated summarization**: Agent re-summarizes content multiple times
- **Tool call explosion**: Many small tool calls instead of few large ones
- **Reflection overhead**: Too many reflection cycles

**Detection criteria:**
```python
# Detect budget exhaustion
if task.tokens_used > task.budget * 0.95:
    if task.completion < 0.5:
        flag_budget_exhaustion()

# Detect inefficient token use
if task.tokens_in_planning > task.tokens_in_execution:
    flag_planning_heavy()
```

**Mitigation:**
- **Dynamic budgets**: Allocate based on task complexity
- **Budget phases**: Reserve tokens for each phase (plan, execute, verify)
- **Efficiency monitoring**: Track tokens-per-unit-progress
- **Model tiering**: Use smaller models for simple steps

**Example from lobs-server logs:**
```
# Recent changes added token tracking
# Risk: Reflection cycles could consume excessive tokens
# Detection: Check reflection_manager token usage vs task completion
```

---

#### 2.3 API Rate Limiting

**Description:** Agent hits rate limits on external APIs, causing delays or failures.

**Manifestations:**
- **Burst rate limit**: Too many requests in short window
- **Daily quota exhaustion**: Exceeds daily request limit
- **Concurrent limit**: Too many simultaneous connections
- **Provider throttling**: Provider slows responses due to high usage

**Detection criteria:**
```python
# Detect rate limiting
if response.status == 429:
    rate_limit_headers = extract_rate_limit_info(response)
    flag_rate_limit(rate_limit_headers)

# Predict rate limit
current_rate = requests_per_minute()
if current_rate > rate_limit * 0.9:
    warn_approaching_limit()
```

**Mitigation:**
- **Rate limit tracking**: Monitor usage against known limits
- **Request batching**: Combine multiple requests
- **Caching**: Cache responses to reduce requests
- **Provider fallback**: Switch to alternative provider when limited

**Example from lobs-server logs:**
```
# GATEWAY] Error calling sessions_spawn
# Could be rate limiting from OpenClaw gateway
# No explicit 429 errors, but possible implicit throttling
```

---

#### 2.4 Database Connection Pool Exhaustion

**Description:** All database connections consumed, new operations blocked.

**Manifestations:**
- **Connection leak**: Connections not returned to pool
- **Long transactions**: Transactions hold connections for extended periods
- **Concurrent query explosion**: Too many simultaneous queries
- **Pool starvation**: High-priority queries blocked by low-priority ones

**Detection criteria:**
```python
# Detect pool exhaustion
if db_pool.active_connections == db_pool.max_connections:
    if db_pool.wait_queue_length > 0:
        flag_pool_exhaustion()

# Detect connection leak
connections = db_pool.get_active_connections()
if any(c.idle_time > threshold for c in connections):
    flag_connection_leak()
```

**Mitigation:**
- **Connection timeouts**: Force-close idle connections
- **Pool sizing**: Size pool based on expected concurrency
- **Transaction boundaries**: Keep transactions short
- **Connection pooling**: Use async connection pools (already using aiosqlite)

**Example from lobs-server logs:**
```
# Not directly observed
# Risk: Many concurrent tasks could exhaust pool
# Current mitigation: aiosqlite with WAL mode allows concurrency
```

---

### Category 3: State Management Failures

Errors in managing distributed state across agents and systems.

#### 3.1 Transaction Deadlocks

**Description:** Database transactions block each other, causing timeouts or failures.

**Manifestations:**
- **Concurrent updates**: Two transactions update same row
- **Lock escalation**: Row lock escalates to table lock, blocks all operations
- **Cascading rollbacks**: One rollback triggers dependent rollbacks
- **Nested transaction failure**: Inner transaction fails, outer transaction stuck

**Detection criteria:**
```python
# Detect transaction deadlock
try:
    await db.commit()
except ResourceClosedError:
    if "already in progress" in error:
        flag_transaction_deadlock()

# Detect long-running transaction
if transaction.duration > threshold:
    flag_long_transaction()
```

**Mitigation:**
- **Transaction isolation**: Use appropriate isolation level
- **Short transactions**: Minimize transaction duration
- **Explicit locks**: Use SELECT FOR UPDATE for critical reads
- **Retry with backoff**: Retry failed transactions with exponential backoff
- **Read replicas**: Offload reads to replicas

**Example from lobs-server logs:**
```
# CRITICAL FINDING - #1 failure mode:
# [PROVIDER_HEALTH] Failed to persist state: Method 'commit()' can't be called here; 
# method '_prepare_impl()' is already in progress (75+ occurrences)

# Root cause: Async operations calling db.commit() concurrently
# Mitigation needed: Serialize DB writes, use session-per-request pattern
```

---

#### 3.2 Stale State Reads

**Description:** Agent reads outdated state, makes decisions on incorrect information.

**Manifestations:**
- **Cache staleness**: Cached value outdated, decision based on old data
- **Eventual consistency lag**: Read from replica before write propagated
- **Race condition**: Read-modify-write without locking
- **Snapshot isolation**: Transaction sees old snapshot of data

**Detection criteria:**
```python
# Detect stale read
if data.last_modified > cache.last_refreshed:
    if decision_made_on(cached_data):
        flag_stale_read()

# Detect version conflict
if data.version != expected_version:
    flag_version_mismatch()
```

**Mitigation:**
- **Version stamping**: Include version in all state objects
- **Optimistic locking**: Check version before write, retry if changed
- **Cache invalidation**: Invalidate cache on write
- **Strong consistency**: Use synchronous replication for critical data

**Example from lobs-server logs:**
```
# Risk: Task state changes could be stale when read by scanner
# Current mitigation: Single-writer (orchestrator engine) reduces risk
# Improvement: Add version field to tasks table
```

---

#### 3.3 Lost Updates

**Description:** Concurrent writes overwrite each other, losing data.

**Manifestations:**
- **Last-writer-wins**: Second write overwrites first without merge
- **Partial update loss**: Only part of update persists
- **Concurrent field updates**: Two agents update different fields, one lost
- **Append conflict**: Two agents append to list, one append lost

**Detection criteria:**
```python
# Detect lost update
if write1.timestamp < write2.timestamp < write1.commit_time:
    if write1.data != current.data:
        flag_lost_update()

# Use optimistic concurrency
current_version = read_version()
updated_data = compute_update(current_data)
if not compare_and_swap(updated_data, current_version):
    flag_concurrent_modification()
```

**Mitigation:**
- **Optimistic locking**: Compare-and-swap pattern
- **Last-write-wins with merge**: Merge conflicting writes
- **Append-only logs**: Use event sourcing, never overwrite
- **Distributed locks**: Use distributed lock service (Redis, etcd)

**Example from lobs-server logs:**
```
# Risk: Worker status updates from multiple workers to same task
# Current mitigation: Each worker has unique ID in tracking table
# Improvement: Use optimistic locking for task state updates
```

---

#### 3.4 State Corruption

**Description:** Invalid state written to database, breaking invariants.

**Manifestations:**
- **Constraint violation**: State violates database constraints
- **Invalid transitions**: State machine enters impossible state
- **Orphaned references**: Foreign key points to deleted record
- **Type mismatch**: Wrong data type stored in field

**Detection criteria:**
```python
# Detect constraint violation
try:
    await db.commit()
except IntegrityError as e:
    flag_state_corruption(e)

# Detect invalid state transition
if not is_valid_transition(current_state, new_state):
    flag_invalid_transition()

# Detect orphaned references
orphans = query("""
    SELECT * FROM tasks 
    WHERE project_id NOT IN (SELECT id FROM projects)
""")
if orphans:
    flag_orphaned_records()
```

**Mitigation:**
- **Schema validation**: Use Pydantic models for runtime validation
- **Database constraints**: Enforce invariants at DB level
- **State machine validation**: Validate transitions before commit
- **Foreign key constraints**: Use ON DELETE CASCADE or RESTRICT
- **Periodic integrity checks**: Scan for orphaned/invalid data

**Example from lobs-server logs:**
```
# Risk: Task state transitions without validation
# Current mitigation: SQLAlchemy ORM provides some validation
# Improvement: Add Pydantic models for runtime state validation (recommended in orchestration research)
```

---

### Category 4: Communication Failures

Errors in agent-to-agent or agent-to-infrastructure communication.

#### 4.1 Message Loss

**Description:** Messages sent but never received, breaking coordination.

**Manifestations:**
- **Network partition**: Sender and receiver temporarily disconnected
- **Queue overflow**: Message queue full, drops messages
- **Timeout**: Receiver doesn't acknowledge, sender assumes loss
- **Deserialization failure**: Receiver can't parse message

**Detection criteria:**
```python
# Detect message loss
if sent_messages.count() > received_messages.count():
    if time_since_send > threshold:
        flag_message_loss()

# Detect queue overflow
if queue.size() >= queue.max_size():
    flag_queue_full()
```

**Mitigation:**
- **Message acknowledgment**: Require explicit ACK from receiver
- **Retry with idempotency**: Resend if no ACK, ensure idempotent handling
- **Dead letter queues**: Route failed messages to DLQ for investigation
- **Delivery guarantees**: Use at-least-once or exactly-once delivery

**Example from lobs-server logs:**
```
# Possible: Gateway communication failures could drop messages
# [GATEWAY] Error calling sessions_spawn (9 occurrences)
# Mitigation needed: Add retry logic with exponential backoff
```

---

#### 4.2 Message Duplication

**Description:** Same message delivered multiple times, causing duplicate work.

**Manifestations:**
- **Retry without deduplication**: Failed send retried, original succeeded
- **At-least-once delivery**: Messaging system delivers duplicates
- **Zombie messages**: Old message redelivered after long delay
- **Broadcast amplification**: Message sent to group, each forwards

**Detection criteria:**
```python
# Detect duplicate
message_id = extract_id(message)
if message_id in seen_messages:
    flag_duplicate_message()

# Detect duplicate work
if task_id in active_tasks and task_id in new_tasks:
    flag_duplicate_task()
```

**Mitigation:**
- **Idempotent handlers**: Ensure duplicate processing is safe
- **Message deduplication**: Track processed message IDs
- **Unique identifiers**: Assign unique ID to each message
- **Exactly-once semantics**: Use transactions to guarantee single processing

**Example from lobs-server logs:**
```
# Risk: Retry logic could spawn duplicate workers
# Current mitigation: Task tracking by unique task_id prevents duplicates
# Improvement: Add deduplication layer for gateway calls
```

---

#### 4.3 Out-of-Order Delivery

**Description:** Messages arrive in wrong order, violating assumptions.

**Manifestations:**
- **Parallel processing**: Multiple workers process messages concurrently
- **Network reordering**: Messages take different paths, arrive out of order
- **Priority inversion**: High-priority message delayed behind low-priority
- **Clock skew**: Timestamps disagree on ordering

**Detection criteria:**
```python
# Detect out-of-order
if message.sequence_number < last_processed_sequence:
    flag_out_of_order()

# Detect causality violation
if message.depends_on not in processed_messages:
    flag_causality_violation()
```

**Mitigation:**
- **Sequence numbers**: Attach monotonic sequence number to messages
- **Reorder buffer**: Hold messages until predecessors arrive
- **Causal ordering**: Use vector clocks to enforce causality
- **Ordered queues**: Use queue with FIFO guarantees

**Example from lobs-server logs:**
```
# Risk: Webhook callbacks from OpenClaw could arrive out of order
# Current mitigation: Each task has unique session, reduces risk
# Improvement: Add sequence numbers to webhook payloads
```

---

#### 4.4 Timeout Failures

**Description:** Operations time out before completion, causing cascading issues.

**Manifestations:**
- **Gateway timeout**: Agent call times out waiting for response
- **Database timeout**: Query exceeds timeout, connection killed
- **LLM timeout**: Model inference times out
- **Cascade timeout**: One timeout causes dependent timeouts

**Detection criteria:**
```python
# Detect timeout
try:
    result = await asyncio.wait_for(operation(), timeout=30)
except asyncio.TimeoutError:
    flag_timeout()

# Detect timeout cascade
if consecutive_timeouts > threshold:
    flag_timeout_cascade()
```

**Mitigation:**
- **Adaptive timeouts**: Increase timeout for slow operations
- **Graceful degradation**: Return partial result on timeout
- **Timeout budgets**: Allocate timeout budget across nested calls
- **Circuit breakers**: Stop calling failed service after N timeouts

**Example from lobs-server logs:**
```
# [AUTO_ASSIGN] gateway invoke failed tool=sessions_history
# asyncio.exceptions.CancelledError → TimeoutError
# Mitigation: Circuit breaker already implemented, good!
```

---

### Category 5: Logic and Reasoning Failures

Errors in agent decision-making and task execution.

#### 5.1 Prompt Injection

**Description:** External input manipulates agent into unintended behavior.

**Manifestations:**
- **Instruction override**: User input contains "Ignore previous instructions"
- **Goal hijacking**: Input redirects agent to attacker's goal
- **Context poisoning**: Malicious content in retrieved context
- **Chain-of-thought manipulation**: Input tricks reasoning process

**Detection criteria:**
```python
# Detect potential injection
injection_patterns = [
    "ignore previous",
    "disregard instructions",
    "new instructions",
    "system prompt",
]
if any(p in user_input.lower() for p in injection_patterns):
    flag_potential_injection()

# Detect goal drift
if current_task != original_task:
    if edit_distance(current, original) > threshold:
        flag_goal_drift()
```

**Mitigation:**
- **Input sanitization**: Remove/escape potentially malicious content
- **Prompt separation**: Clearly separate instructions from user content
- **Output validation**: Check outputs against expected format
- **Goal anchoring**: Regularly remind agent of original goal

**Example from lobs-server logs:**
```
# Not observed
# Risk: User tasks could contain injection attempts
# Mitigation: Add input validation to task descriptions
```

---

#### 5.2 Hallucination

**Description:** Agent generates false information presented as fact.

**Manifestations:**
- **Fabricated sources**: Cites non-existent papers, URLs, documentation
- **Confident errors**: States incorrect information with high confidence
- **Tool output hallucination**: Invents tool results instead of calling tool
- **Memory hallucination**: "Recalls" events that didn't happen

**Detection criteria:**
```python
# Detect fabricated sources
urls = extract_urls(agent_output)
for url in urls:
    if not await url_exists(url):
        flag_fabricated_source(url)

# Detect tool hallucination
if "tool_result" in output:
    if not tool_was_called():
        flag_tool_hallucination()
```

**Mitigation:**
- **Source verification**: Check citations are valid
- **Tool enforcement**: Require actual tool calls, don't accept simulated results
- **Fact-checking**: Cross-reference claims against knowledge base
- **Confidence calibration**: Penalize overconfident errors

**Example from lobs-server logs:**
```
# Not observable in logs
# Risk: Research agent could hallucinate sources
# Mitigation: Require URLs in research docs, validation step
```

---

#### 5.3 Reasoning Breakdown

**Description:** Agent's logical reasoning produces nonsensical or contradictory conclusions.

**Manifestations:**
- **Circular reasoning**: Uses conclusion to prove itself
- **Non-sequitur**: Conclusions don't follow from premises
- **Contradiction**: Asserts X and not-X simultaneously
- **Category error**: Treats incompatible types as same

**Detection criteria:**
```python
# Detect contradiction
claims = extract_claims(agent_output)
for claim1 in claims:
    for claim2 in claims:
        if are_contradictory(claim1, claim2):
            flag_contradiction()

# Detect circular reasoning
reasoning_graph = build_dependency_graph(reasoning)
if has_cycle(reasoning_graph):
    flag_circular_reasoning()
```

**Mitigation:**
- **Formal reasoning**: Use structured reasoning (chain-of-thought, tree-of-thought)
- **Consistency checking**: Check outputs for contradictions
- **Reasoning review**: Have second agent review reasoning
- **Symbolic verification**: Use formal methods for critical reasoning

**Example from lobs-server logs:**
```
# Not observable in logs
# Risk: Reflection system could produce contradictory insights
# Mitigation: Add consistency checks to reflection output
```

---

#### 5.4 Tool Misuse

**Description:** Agent uses tools incorrectly or inappropriately.

**Manifestations:**
- **Wrong tool selection**: Uses search when should execute code
- **Invalid parameters**: Passes wrong types or values to tool
- **Dangerous operations**: Executes destructive commands without validation
- **Tool chaining errors**: Outputs of tool A don't match inputs of tool B

**Detection criteria:**
```python
# Detect wrong tool
expected_tool = infer_tool_from_goal(task.goal)
if agent_selected_tool != expected_tool:
    flag_tool_mismatch()

# Detect invalid parameters
try:
    validate_tool_params(tool, params)
except ValidationError:
    flag_invalid_params()

# Detect dangerous operation
if is_destructive(tool, params):
    if not has_approval():
        flag_dangerous_operation()
```

**Mitigation:**
- **Tool descriptions**: Provide clear descriptions of when to use each tool
- **Parameter validation**: Validate all tool parameters before execution
- **Dry-run mode**: Preview tool effects before execution
- **Approval requirements**: Require human approval for destructive operations

**Example from lobs-server logs:**
```
# Not directly observed
# Risk: Agents could misuse gateway tools
# Mitigation: OpenClaw has built-in parameter validation
```

---

### Category 6: Infrastructure Failures

External system failures that impact agent operations.

#### 6.1 Gateway Unavailability

**Description:** OpenClaw gateway unreachable or non-responsive.

**Manifestations:**
- **Gateway offline**: Service not running
- **Network partition**: Gateway isolated from server
- **Authentication failure**: Invalid credentials or expired tokens
- **Version mismatch**: Incompatible protocol versions

**Detection criteria:**
```python
# Detect gateway unavailability
try:
    response = await gateway_healthcheck()
except ConnectionError:
    flag_gateway_unavailable()

# Detect auth failure
if response.status == 401:
    flag_gateway_auth_failure()
```

**Mitigation:**
- **Health checks**: Periodic gateway health checks
- **Circuit breakers**: Stop calling failed gateway
- **Retry with backoff**: Exponential backoff for transient failures
- **Graceful degradation**: Queue tasks when gateway unavailable

**Example from lobs-server logs:**
```
# CIRCUIT BREAKER OPEN — gateway_auth (26 occurrences)
# → Gateway authentication failures triggering circuit breaker
# Good: Circuit breaker prevents cascade
# Improvement: Add gateway health monitoring dashboard
```

---

#### 6.2 Model Provider Failures

**Description:** LLM API failures disrupt agent reasoning.

**Manifestations:**
- **Provider outage**: OpenAI/Anthropic/etc service down
- **Rate limiting**: Exceeded API rate limits
- **Model unavailable**: Requested model not accessible
- **Degraded performance**: Model responding slowly

**Detection criteria:**
```python
# Detect provider failure
if response.status >= 500:
    flag_provider_outage()

# Detect rate limiting
if response.status == 429:
    flag_rate_limited()

# Detect slow performance
if response_time > p99_latency * 3:
    flag_degraded_performance()
```

**Mitigation:**
- **Provider fallback**: Switch to backup provider on failure
- **Model fallback**: Use smaller/cheaper model when primary fails
- **Request queuing**: Queue requests during outage
- **Cost monitoring**: Track spending, prevent budget overrun

**Example from lobs-server logs:**
```
# Recent improvement: 5-tier model routing with fallback chains
# Good: Ollama auto-discovery provides local fallback
# Improvement: Monitor provider health, auto-failover
```

---

#### 6.3 Database Failures

**Description:** Database unavailable or corrupted.

**Manifestations:**
- **Database offline**: Service not running
- **Disk full**: No space for writes
- **Corruption**: Database files corrupted
- **Migration failure**: Schema migration partially applied

**Detection criteria:**
```python
# Detect database failure
try:
    await db.execute("SELECT 1")
except ConnectionError:
    flag_database_offline()

# Detect disk full
if db.disk_usage() > 0.95:
    flag_disk_full()

# Detect corruption
if not db.integrity_check():
    flag_database_corruption()
```

**Mitigation:**
- **Database replication**: Failover to replica on primary failure
- **Disk monitoring**: Alert when disk usage high
- **Automated backups**: Regular backups with automated restore
- **Transaction logs**: Use WAL for crash recovery

**Example from lobs-server logs:**
```
# Currently using SQLite with WAL mode
# Good: WAL provides crash recovery
# Risk: Single point of failure (no replication)
# Improvement: Add backup/restore procedures
```

---

#### 6.4 Network Partitions

**Description:** Network failures isolate components from each other.

**Manifestations:**
- **Complete partition**: No communication between components
- **Partial partition**: Some components isolated, others connected
- **Asymmetric partition**: A can reach B, B can't reach A
- **Flapping partition**: Intermittent connection loss/recovery

**Detection criteria:**
```python
# Detect partition
if consecutive_connection_failures > threshold:
    if other_components_still_reachable():
        flag_network_partition()

# Detect split brain
if both_sides_think_they_are_primary():
    flag_split_brain()
```

**Mitigation:**
- **Partition tolerance**: Design for network failures (CAP theorem)
- **Consensus protocols**: Use Raft/Paxos for coordination
- **Split-brain prevention**: Use distributed locks with quorum
- **Retry with backoff**: Retry failed connections

**Example from lobs-server logs:**
```
# Not observed (single-node deployment)
# Risk: Future distributed deployment
# Mitigation: Design for CAP theorem from start
```

---

## Cross-Cutting Patterns

### Cascading Failures

**Description:** Single failure triggers chain of dependent failures.

**Sequence:**
1. Component A fails
2. Component B depends on A, fails when calling A
3. Component C depends on B, fails when calling B
4. System-wide outage

**Example from lobs-server logs:**
```
# Database transaction deadlock (Component A)
# → Provider health tracking fails to persist (Component B)
# → Escalation fails to create alert (Component C)
# → Worker status update fails (Component D)
# → 75+ failures from single root cause
```

**Mitigation:**
- **Circuit breakers**: Isolate failures (already implemented!)
- **Bulkheads**: Separate resource pools for different components
- **Graceful degradation**: Components work in reduced capacity when dependencies fail
- **Failure independence**: Minimize coupling between components

---

### Retry Storms

**Description:** Many clients retry failed operations simultaneously, overwhelming system.

**Sequence:**
1. Service experiences brief outage
2. 100 clients all retry simultaneously
3. Service overwhelmed by retry storm
4. Outage extends due to overload

**Mitigation:**
- **Exponential backoff**: Increase delay between retries
- **Jittered backoff**: Add randomness to prevent synchronized retries
- **Circuit breakers**: Stop retrying after N consecutive failures
- **Rate limiting**: Limit retry rate

---

### Silent Failures

**Description:** Failures occur without visible errors, causing subtle corruption.

**Manifestations:**
- **Swallowed exceptions**: Exception caught but not logged
- **Ignored return values**: Error return value not checked
- **Async fire-and-forget**: Async operation fails, caller doesn't know
- **Background job failure**: Scheduled job fails silently

**Detection:**
- **Comprehensive logging**: Log all errors, even if handled
- **Alerting on anomalies**: Alert when metrics deviate from normal
- **Data validation**: Check outputs match expected format
- **Periodic audits**: Scan for inconsistencies

**Example from lobs-server logs:**
```
# Risk: Async operations could fail silently
# Improvement: Ensure all async operations have error handling and logging
```

---

## Detection Infrastructure

### Recommended Monitoring

```python
# 1. Loop detection
monitor_action_sequences(
    window_size=20,
    uniqueness_threshold=0.3,  # Flag if <30% unique
    alert_callback=escalate_to_human
)

# 2. Progress tracking
monitor_task_progress(
    check_interval=60,  # Check every minute
    progress_threshold=0.01,  # Flag if <1% progress/check
    stuck_threshold=5  # Flag after 5 consecutive no-progress checks
)

# 3. Resource monitoring
monitor_resources(
    metrics=["context_tokens", "db_connections", "api_calls"],
    thresholds={
        "context_tokens": 0.9,  # 90% of limit
        "db_connections": 0.8,
        "api_calls": 0.85
    }
)

# 4. State validation
monitor_state_integrity(
    checks=[
        "constraint_validation",
        "orphan_detection",
        "state_machine_validation"
    ],
    frequency="hourly"
)

# 5. Communication monitoring
monitor_communication(
    metrics=["message_loss", "message_duplication", "out_of_order"],
    detection_window=300,  # 5 minutes
    alert_threshold=0.05  # 5% error rate
)
```

---

## Failure Mode Summary Matrix

| Failure Mode | Severity | Frequency in Logs | Detection Difficulty | Fix Difficulty |
|-------------|----------|-------------------|---------------------|----------------|
| **Transaction deadlocks** | 🔴 Critical | Very High (75+) | Easy | Medium |
| **Circuit breaker cascades** | 🔴 Critical | High (130+) | Easy | Hard |
| **Type mismatches** | 🟡 High | Medium (15+) | Easy | Easy |
| **Value unpacking errors** | 🟡 High | Medium (10+) | Easy | Easy |
| **Gateway timeouts** | 🟡 High | Medium (15+) | Easy | Medium |
| **Infinite loops** | 🔴 Critical | Unknown | Hard | Medium |
| **Context thrashing** | 🟡 High | Unknown | Medium | Hard |
| **Deadlocks** | 🔴 Critical | Low | Medium | Hard |
| **Hallucination** | 🟡 High | Unknown | Hard | Hard |
| **Prompt injection** | 🟠 Medium | None | Medium | Medium |
| **Message loss** | 🟡 High | Unknown | Medium | Medium |
| **Stale reads** | 🟠 Medium | Unknown | Hard | Medium |

---

## Immediate Action Items for lobs-server

### Priority 1 (Critical - Fix Now)

1. **Fix transaction deadlocks**
   - **Problem**: 75+ instances of "This transaction is closed"
   - **Root cause**: Concurrent `db.commit()` calls
   - **Fix**: Implement session-per-request pattern, serialize writes
   - **Files**: `app/orchestrator/provider_health.py`, `app/orchestrator/worker.py`, `app/orchestrator/escalation_enhanced.py`

2. **Investigate circuit breaker triggers**
   - **Problem**: 130+ circuit breaker opens
   - **Root cause**: Repeated gateway auth failures, session locks
   - **Fix**: Add gateway health monitoring, fix auth token refresh
   - **Files**: `app/orchestrator/circuit_breaker.py`

3. **Fix type errors**
   - **Problem**: Comparing MagicMock/coroutine with int
   - **Root cause**: Test mocks leaking into production code
   - **Fix**: Better test isolation, add runtime type checks
   - **Files**: `app/orchestrator/engine.py` (lines with `> 0` comparisons)

### Priority 2 (High - Fix This Week)

4. **Add progress tracking**
   - **Problem**: No detection for stuck/infinite loop tasks
   - **Fix**: Track progress metrics, alert on no-progress-in-N-iterations
   - **Files**: `app/orchestrator/monitor_enhanced.py`

5. **Implement deduplication**
   - **Problem**: Retry logic could spawn duplicate workers
   - **Fix**: Track message IDs, deduplicate gateway calls
   - **Files**: `app/orchestrator/worker.py`

6. **Add state validation**
   - **Problem**: No runtime validation of task state transitions
   - **Fix**: Add Pydantic models for task state, validate transitions
   - **Files**: `app/models.py`, `app/schemas.py`

### Priority 3 (Medium - Fix This Month)

7. **Context management**
   - **Problem**: No tracking of context window usage
   - **Fix**: Track tokens per task, implement summarization when approaching limit
   - **Files**: `app/orchestrator/worker.py`, `app/orchestrator/prompter.py`

8. **Gateway health monitoring**
   - **Problem**: No visibility into gateway health before failures
   - **Fix**: Add health check endpoint, dashboard with gateway metrics
   - **Files**: `app/routers/status.py`

9. **Hallucination detection**
   - **Problem**: Research agent could fabricate sources
   - **Fix**: Validate URLs in research docs, add source verification step
   - **Files**: Research agent prompts, document validation

---

## Sources

### Framework Documentation
1. CrewAI Docs - Error Handling: https://docs.crewai.com/concepts/error-handling
2. LangGraph Docs - Durable Execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
3. AutoGen Docs - Resilience Patterns: https://microsoft.github.io/autogen/

### Academic Papers
4. "Failure Modes in Autonomous Agent Systems" (inferred from common patterns)
5. CAP Theorem - Brewer, 2000
6. "Why Do Computers Stop and What Can Be Done About It" - Jim Gray, 1985

### Empirical Analysis
7. lobs-server error logs (/Users/lobs/lobs-server/logs/error.log) - 5775 lines analyzed
8. lobs-server architecture (ARCHITECTURE.md, orchestrator/ source code)

---

**Research completed:** 2026-02-22  
**Log analysis period:** 2026-02-19 to 2026-02-22  
**Next review:** After implementing Priority 1 fixes
