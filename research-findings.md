# Performance Baseline Metrics — Research Findings

**Date:** 2026-02-22  
**Researcher:** Researcher Agent  
**Task ID:** 48204293-a568-437f-b4bb-1f7029aecc00  
**Context:** No performance baseline data currently exists. We're optimizing blind.

---

## Executive Summary

This research establishes **what to measure**, **how to measure it**, and **how to compare** performance for the lobs-server FastAPI + SQLite backend with task orchestration.

**Key Recommendations:**
1. **Implement Prometheus metrics** via `starlette_exporter` for API response times
2. **Add SQLAlchemy query instrumentation** to track database performance
3. **Create load testing suite** using Locust for reproducible benchmarks
4. **Establish baseline targets** based on industry standards for API latency
5. **Monitor orchestrator-specific metrics** for task completion times

**Estimated Implementation Time:** 3-6 hours (Programmer phase)

---

## Part 1: What to Measure

### 1.1 API Performance Metrics

Based on FastAPI best practices and the existing RequestLoggingMiddleware, measure:

#### **Response Time (Latency)**
- **p50 (median)**: Target < 50ms for simple queries
- **p95**: Target < 200ms for complex queries
- **p99**: Target < 500ms (catch outliers)
- **max**: Track worst-case performance

**Current State:** Middleware logs duration in milliseconds, but doesn't aggregate or expose metrics.

**Why it matters:** User-facing responsiveness. Mission Control dashboard queries hit these endpoints constantly.

#### **Throughput**
- **Requests per second** (RPS) per endpoint
- **Total requests** counter by method/path/status_code

**Industry Standard:** FastAPI can handle 10,000+ RPS for simple queries. For this application with SQLite, expect 500-2000 RPS sustainable load.

#### **Error Rate**
- **5xx errors** (server errors) — should be < 0.1%
- **4xx errors** (client errors) — track separately, often user mistakes
- **Timeout rate** — requests exceeding threshold

**Current State:** Logged but not tracked over time.

---

### 1.2 Database Performance Metrics

SQLite-specific considerations (from [sqlite.org research](https://www.sqlite.org/np1queryprob.html)):

#### **Query Latency**
- **Simple SELECT** (single table, indexed): Target < 1ms
- **Complex JOIN** (2-3 tables): Target < 10ms
- **Aggregate queries** (COUNT, SUM with GROUP BY): Target < 50ms

**SQLite Advantage:** No network round-trip. 200+ queries per page is acceptable (unlike client/server databases).

**Known Issue:** Activity endpoint has N+1 query problem (see KNOWN_ISSUES.md).

#### **Connection Pool Stats**
- **Active connections** (should stay low with async SQLite)
- **Connection wait time** (should be near-zero)
- **SQLAlchemy query cache hit rate**

#### **Database Size and Growth**
- **DB file size** over time
- **Table row counts** (tasks, projects, memories)
- **WAL file size** (Write-Ahead Log for concurrency)

**Current State:** No tracking. Could grow indefinitely.

---

### 1.3 Orchestrator Performance Metrics

Task orchestration is the core value of lobs-server. Measure:

#### **Task Execution Times**
- **Time to assignment** (from `not_started` → `in_progress`)
- **Total execution time** per task type
- **Agent-specific completion times** (programmer vs researcher vs reviewer)

**Baseline Needed:** No current data. Expect:
- Simple tasks: 1-5 minutes
- Complex tasks: 15-60 minutes
- Research tasks: 5-20 minutes

#### **Worker Throughput**
- **Concurrent workers** (current vs max)
- **Tasks completed per hour**
- **Worker spawn time** (subprocess startup overhead)
- **Worker failure rate**

**Current State:** worker_runs table exists but no aggregated metrics.

#### **Stuck Task Detection**
- **Mean time to detection** (how long before monitor flags stuck tasks)
- **False positive rate** (tasks flagged but not actually stuck)

**Current Setting:** 15-minute timeout (from monitor_enhanced.py).

---

### 1.4 System Resource Metrics

#### **Memory Usage**
- **RSS** (Resident Set Size): Total memory footprint
- **SQLite cache size** (page cache)
- **Python heap size**

**Concern:** Multiple concurrent agents could cause memory pressure.

#### **CPU Usage**
- **Per-worker CPU** %
- **Database query CPU** time
- **Idle time** %

#### **Disk I/O**
- **SQLite read/write IOPS**
- **WAL checkpoint frequency**
- **Backup operation impact**

---

## Part 2: How to Measure (Implementation Approach)

### 2.1 Prometheus + starlette_exporter (Recommended)

**Tool:** [starlette_exporter](https://github.com/stephenhillier/starlette_exporter)  
**Why:** Purpose-built for FastAPI/Starlette, minimal code changes, industry-standard.

#### Implementation

```python
# app/main.py
from starlette_exporter import PrometheusMiddleware, handle_metrics

app.add_middleware(
    PrometheusMiddleware,
    app_name="lobs_server",
    prefix="lobs",
    group_paths=True,  # Group /api/tasks/{id} together
    skip_paths=['/health', '/metrics'],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    optional_metrics=[response_body_size, request_body_size]
)

app.add_route("/metrics", handle_metrics)
```

**Metrics Exposed (automatically):**
- `lobs_requests_total{method, path, status_code}`
- `lobs_request_duration_seconds{method, path, status_code}` (histogram)
- `lobs_requests_in_progress{method, path}`

**Installation:**
```bash
pip install starlette-exporter
```

**Grafana Integration:** Scrape `/metrics` endpoint, visualize in Grafana dashboard.

---

### 2.2 Database Query Instrumentation

#### Option A: SQLAlchemy Event Listeners

```python
# app/database.py
from sqlalchemy import event
from prometheus_client import Histogram

query_duration = Histogram(
    'lobs_db_query_duration_seconds',
    'Database query duration',
    ['operation']
)

@event.listens_for(Engine, "before_cursor_execute")
def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault('query_start_time', []).append(time.time())

@event.listens_for(Engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total = time.time() - conn.info['query_start_time'].pop()
    query_duration.labels(operation=statement.split()[0]).observe(total)
```

**Metrics:**
- `lobs_db_query_duration_seconds{operation="SELECT"}`
- `lobs_db_query_duration_seconds{operation="UPDATE"}`
- etc.

#### Option B: OpenTelemetry Auto-Instrumentation

**Tool:** [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python)

**Pros:** 
- Auto-instruments FastAPI, SQLAlchemy, and more
- Distributed tracing support
- Standard observability format

**Cons:**
- Heavier than Prometheus-only
- More complex setup
- Overkill for current needs

**Recommendation:** Start with Prometheus/starlette_exporter. Add OpenTelemetry later if distributed tracing is needed.

---

### 2.3 Custom Orchestrator Metrics

Add to orchestrator engine:

```python
# app/orchestrator/engine.py
from prometheus_client import Counter, Histogram, Gauge

tasks_assigned = Counter('lobs_tasks_assigned_total', 'Tasks assigned to workers', ['agent_type'])
tasks_completed = Counter('lobs_tasks_completed_total', 'Tasks completed', ['agent_type', 'status'])
task_duration = Histogram('lobs_task_duration_seconds', 'Task execution time', ['agent_type'])
active_workers = Gauge('lobs_active_workers', 'Currently active workers')

# In worker spawn:
tasks_assigned.labels(agent_type=agent).inc()

# In worker completion:
tasks_completed.labels(agent_type=agent, status='success').inc()
task_duration.labels(agent_type=agent).observe(elapsed_time)
```

---

### 2.4 Load Testing with Locust

**Tool:** [Locust](https://locust.io) — Python-based load testing  
**Why:** Easy to write tests, simulates realistic user behavior, generates RPS/latency graphs.

#### Example Test Suite

```python
# tests/load/locustfile.py
from locust import HttpUser, task, between

class LoberUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        # Login / get auth token
        self.token = "test-token"
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})
    
    @task(10)
    def list_tasks(self):
        """Most common operation"""
        self.client.get("/api/tasks")
    
    @task(5)
    def get_project(self):
        """Second most common"""
        self.client.get("/api/projects")
    
    @task(3)
    def create_task(self):
        """Less frequent write operation"""
        self.client.post("/api/tasks", json={
            "title": "Load test task",
            "project_id": "test-proj"
        })
    
    @task(2)
    def worker_activity(self):
        """Known N+1 query issue"""
        self.client.get("/api/worker/activity")
```

**Run:**
```bash
locust -f tests/load/locustfile.py --host=http://localhost:8000
# Open web UI at http://localhost:8089
# Configure users, spawn rate, run test
```

**Output:**
- RPS sustained
- Response time percentiles
- Failure rate
- Detailed graphs and CSV export

---

## Part 3: Baseline Targets

### 3.1 API Response Time Targets

Based on industry standards for REST APIs:

| Endpoint Type | p50 | p95 | p99 | Max |
|--------------|-----|-----|-----|-----|
| Health check | < 5ms | < 10ms | < 20ms | < 50ms |
| Simple list (tasks, projects) | < 50ms | < 100ms | < 200ms | < 500ms |
| Single resource GET | < 20ms | < 50ms | < 100ms | < 250ms |
| POST/PUT/DELETE | < 100ms | < 250ms | < 500ms | < 1s |
| Complex aggregation (stats) | < 200ms | < 500ms | < 1s | < 2s |
| Worker activity (N+1 issue) | < 100ms | < 500ms | < 1s | < 2s |

**After N+1 fix:**
- Worker activity should drop to < 50ms p50, < 150ms p95

---

### 3.2 Database Query Targets

SQLite performance characteristics:

| Query Type | Target Latency | Notes |
|-----------|---------------|-------|
| Indexed SELECT (single row) | < 1ms | Fast B-tree lookup |
| Full table scan (< 1000 rows) | < 10ms | Acceptable for small tables |
| Complex JOIN (2-3 tables) | < 10ms | With proper indexes |
| Aggregate (COUNT, GROUP BY) | < 50ms | Depends on table size |
| INSERT/UPDATE | < 5ms | WAL mode is fast |

**Known Issues:**
- Activity endpoint N+1 query: Currently making 1 + N queries for N worker runs
- Should be reduced to 1-2 queries with `joinedload()`

---

### 3.3 Orchestrator Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Time to task assignment | < 20s | Orchestrator polling interval is 10s |
| Task completion (simple) | 1-5 min | Depends on agent and task complexity |
| Task completion (complex) | 15-60 min | Research, large refactors |
| Worker spawn time | < 2s | Subprocess overhead |
| Concurrent workers | 3-5 | Configurable, memory-dependent |
| Stuck task detection | 15 min | Current timeout setting |

---

### 3.4 System Resource Targets

| Resource | Target | Warning Threshold | Critical Threshold |
|----------|--------|------------------|-------------------|
| Memory (RSS) | < 500 MB | 1 GB | 2 GB |
| CPU (average) | < 30% | 60% | 80% |
| Disk I/O (read) | < 10 MB/s | 50 MB/s | 100 MB/s |
| SQLite DB size | < 1 GB | 5 GB | 10 GB |
| Active connections | < 10 | 50 | 100 |

---

## Part 4: Comparison — Similar Systems

### 4.1 FastAPI + SQLite Benchmarks (Public Data)

**Reference:** [TechEmpower Benchmarks](https://www.techempower.com/benchmarks/)

- **FastAPI (single query):** ~40,000 RPS
- **FastAPI (multi query):** ~10,000 RPS
- **FastAPI (data updates):** ~5,000 RPS

**Note:** These are stress tests with minimal business logic. Expect 10-20% of these numbers for real-world apps.

**Realistic Targets for lobs-server:**
- **Simple queries:** 500-2,000 RPS
- **Complex queries:** 100-500 RPS
- **Write operations:** 200-1,000 RPS

---

### 4.2 Task Orchestration Systems

Comparable systems:

#### **Celery (task queue)**
- Task assignment: < 1s (with Redis)
- Task completion: Varies (depends on worker)
- Monitoring: Built-in with Flower dashboard

#### **Airflow (workflow orchestration)**
- Task scheduling overhead: 5-30s
- Task completion: Minutes to hours (data pipelines)
- Monitoring: Built-in UI with Gantt charts

#### **Jenkins (CI/CD)**
- Job assignment: < 10s
- Job completion: 1-30 minutes
- Monitoring: Built-in with historical graphs

**lobs-server Positioning:**
- **Lighter than Airflow** (single-node, no DAGs)
- **Simpler than Celery** (no message broker)
- **More flexible than Jenkins** (general-purpose agents)

---

## Part 5: Recommended Benchmark Suite

### 5.1 Automated Performance Tests

Create `tests/performance/` directory with:

#### **1. API Latency Benchmark**

```python
# tests/performance/test_api_latency.py
import pytest
import time
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_task_list_latency(client: AsyncClient):
    """Task list should respond in < 50ms p50"""
    times = []
    for _ in range(100):
        start = time.perf_counter()
        response = await client.get("/api/tasks")
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        assert response.status_code == 200
    
    p50 = sorted(times)[50]
    p95 = sorted(times)[95]
    
    print(f"\nTask list latency: p50={p50:.2f}ms, p95={p95:.2f}ms")
    assert p50 < 50, f"p50 latency {p50:.2f}ms exceeds 50ms target"
    assert p95 < 200, f"p95 latency {p95:.2f}ms exceeds 200ms target"
```

#### **2. Database Query Benchmark**

```python
# tests/performance/test_db_queries.py
import pytest
from sqlalchemy import select
from app.models import Task

@pytest.mark.asyncio
async def test_task_query_performance(db):
    """Single task query should be < 5ms"""
    times = []
    for _ in range(100):
        start = time.perf_counter()
        result = await db.execute(select(Task).limit(1))
        _ = result.scalar_one_or_none()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    p50 = sorted(times)[50]
    print(f"\nDB query latency: p50={p50:.2f}ms")
    assert p50 < 5, f"DB query p50 {p50:.2f}ms exceeds 5ms target"
```

#### **3. Load Test Suite**

```python
# tests/load/locustfile.py
# (See section 2.4 above for full example)
```

---

### 5.2 Benchmark Execution Plan

**Phase 1: Establish Baseline (Week 1)**
1. Install Prometheus + starlette_exporter
2. Add custom orchestrator metrics
3. Run Locust load tests (50 users, 5 min duration)
4. Document baseline numbers in README

**Phase 2: Fix Known Issues (Week 2)**
1. Fix N+1 query in activity endpoint
2. Re-run benchmarks
3. Compare before/after

**Phase 3: Continuous Monitoring (Ongoing)**
1. Set up Grafana dashboard
2. Add alerting for p95 > threshold
3. Run weekly load tests
4. Track trends over time

---

## Part 6: Implementation Recommendations

### 6.1 Immediate Actions (Programmer Phase)

**Priority 1: Prometheus Metrics**
- Install `starlette-exporter`
- Add middleware to `app/main.py`
- Create `/metrics` endpoint
- **Time estimate:** 1 hour

**Priority 2: Database Instrumentation**
- Add SQLAlchemy event listeners
- Track query duration by operation type
- **Time estimate:** 1 hour

**Priority 3: Orchestrator Metrics**
- Add Prometheus counters/gauges to engine
- Track task assignment, completion, duration
- **Time estimate:** 1-2 hours

**Priority 4: Load Test Suite**
- Create `tests/load/locustfile.py`
- Define realistic user scenarios
- Run baseline benchmark
- **Time estimate:** 2 hours

**Priority 5: Documentation**
- Add baseline numbers to README
- Create performance monitoring guide
- **Time estimate:** 30 minutes

**Total: 5.5-6.5 hours**

---

### 6.2 Monitoring Dashboard (Optional, Future Work)

**Tool:** Grafana + Prometheus

**Key Panels:**
1. **API Latency** (p50/p95/p99 over time)
2. **Request Rate** (RPS per endpoint)
3. **Error Rate** (5xx/4xx percentage)
4. **Database Performance** (query duration histogram)
5. **Orchestrator Health** (active workers, task queue depth)
6. **System Resources** (memory, CPU, disk I/O)

**Setup Time:** 2-3 hours (including Prometheus server setup)

---

## Part 7: Risks and Gotchas

### 7.1 SQLite Concurrency Limits

**Issue:** SQLite has limited write concurrency (WAL mode allows concurrent reads, but writes are serialized).

**Mitigation:**
- Keep writes fast (< 5ms per transaction)
- Use connection pooling wisely
- Monitor for `SQLITE_BUSY` errors

**Reference:** [SQLite docs on concurrency](https://www.sqlite.org/wal.html)

---

### 7.2 Memory Pressure from Multiple Workers

**Issue:** Each OpenClaw worker is a separate Python process. 5 concurrent workers = 5x memory footprint.

**Mitigation:**
- Set `ORCHESTRATOR_MAX_WORKERS=3` (conservative default)
- Monitor RSS per worker
- Implement worker memory limits

---

### 7.3 N+1 Query Anti-Pattern

**Known Issue:** Activity endpoint makes 1 query to get worker runs, then 1 query per run to fetch related data.

**Solution:** Use `joinedload()` or `selectinload()`:

```python
# Bad (N+1):
runs = await db.execute(select(WorkerRun).limit(50))
for run in runs:
    task = await db.execute(select(Task).where(Task.id == run.task_id))

# Good (2 queries total):
from sqlalchemy.orm import joinedload
runs = await db.execute(
    select(WorkerRun).options(joinedload(WorkerRun.task)).limit(50)
)
```

**Reference:** [SQLAlchemy docs on eager loading](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#joined-eager-loading)

---

### 7.4 Prometheus Cardinality Explosion

**Issue:** Too many unique label values can cause memory issues in Prometheus.

**Avoid:**
- User IDs as labels
- Task IDs as labels
- High-cardinality UUIDs

**Safe Labels:**
- HTTP method (GET, POST, etc.)
- Status code (200, 404, 500)
- Endpoint path (grouped, e.g., `/api/tasks/{id}`)
- Agent type (programmer, researcher, reviewer)

**Reference:** [Prometheus best practices](https://prometheus.io/docs/practices/naming/)

---

## Part 8: References and Sources

### Research Sources

1. **FastAPI Middleware Documentation**  
   https://fastapi.tiangolo.com/advanced/middleware/  
   → Confirmed middleware approach for instrumentation

2. **Prometheus Python Client**  
   https://prometheus.github.io/client_python/  
   → Standard metrics library, decorator-based API

3. **starlette_exporter (Recommended)**  
   https://github.com/stephenhillier/starlette_exporter  
   → Purpose-built for FastAPI, minimal code changes

4. **SQLite Performance — N+1 Queries**  
   https://www.sqlite.org/np1queryprob.html  
   → "200 queries per page is fine with SQLite" (no network overhead)

5. **OpenTelemetry Python**  
   https://github.com/open-telemetry/opentelemetry-python  
   → Auto-instrumentation for FastAPI + SQLAlchemy (overkill for now)

6. **Locust Load Testing**  
   https://locust.io  
   → Python-based, easy to write tests, realistic user simulation

7. **TechEmpower Benchmarks**  
   https://www.techempower.com/benchmarks/  
   → FastAPI performance baseline data

8. **SQLAlchemy Eager Loading**  
   https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#joined-eager-loading  
   → Solution for N+1 query problems

### Existing Codebase References

- `app/middleware.py` — RequestLoggingMiddleware already tracks duration
- `app/orchestrator/monitor_enhanced.py` — Stuck task detection (15min timeout)
- `KNOWN_ISSUES.md` — Documents activity endpoint N+1 query issue
- `ORCHESTRATOR_INTEGRATION.md` — Recommendation to add Prometheus metrics

---

## Part 9: Next Steps

### For Programmer Agent (Phase 2)

**Accept handoff when ready to implement:**

1. **Install dependencies**
   ```bash
   pip install starlette-exporter prometheus-client locust
   echo "starlette-exporter>=0.18.0" >> requirements.txt
   echo "prometheus-client>=0.19.0" >> requirements.txt
   echo "locust>=2.20.0" >> requirements.txt  # dev dependency
   ```

2. **Add Prometheus middleware** (see 6.1 Priority 1)

3. **Instrument database queries** (see 6.1 Priority 2)

4. **Add orchestrator metrics** (see 6.1 Priority 3)

5. **Create load test suite** (see 5.2)

6. **Run baseline benchmark**
   ```bash
   # Start server
   ./bin/run
   
   # In another terminal
   locust -f tests/load/locustfile.py --host=http://localhost:8000 \
          --users=50 --spawn-rate=10 --run-time=5m --headless
   ```

7. **Document results** in README:
   ```markdown
   ## Performance Baseline
   
   Measured on 2026-02-22 with 50 concurrent users:
   
   - List tasks: p50=23ms, p95=87ms
   - Get project: p50=18ms, p95=65ms
   - Worker activity: p50=145ms, p95=420ms (N+1 query issue)
   
   Target: < 50ms p50, < 200ms p95 for all endpoints.
   ```

8. **Set up Grafana** (optional, future work)

---

## Conclusion

**What we learned:**
- Prometheus + starlette_exporter is the industry-standard approach for FastAPI monitoring
- SQLite's N+1 query pattern is acceptable (unlike client/server DBs) but still should be optimized
- Current middleware logs duration but doesn't expose metrics
- Known N+1 query issue in activity endpoint should be fixed and benchmarked
- Locust is the right tool for load testing Python web apps

**Key Metrics to Track:**
1. API response time (p50/p95/p99) per endpoint
2. Database query duration by operation type
3. Orchestrator task assignment and completion times
4. System resources (memory, CPU)

**Baseline Targets:**
- API: < 50ms p50, < 200ms p95
- DB queries: < 5ms for simple SELECT
- Task completion: 1-5 min for simple tasks

**Implementation Complexity:** Low to Medium (5-6 hours total)

**ROI:** High — enables data-driven optimization, prevents performance regressions, supports SLA monitoring.

---

**Ready for handoff to Programmer for Phase 2 implementation.**
