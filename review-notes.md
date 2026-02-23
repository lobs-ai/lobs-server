# Memory Retrieval Quality Audit

**Date:** 2026-02-23  
**Reviewer:** reviewer  
**Task:** Audit memory retrieval quality  
**Database:** 253 memories across multiple agents

---

## Executive Summary

The memory search implementation uses **simple substring matching (ILIKE)** which provides adequate results for basic single-term queries (70% success rate) but fails completely on multi-word phrases (20% failure rate). The system shows **NO semantic understanding** and cannot match queries where words are separated or appear in different order.

**Overall Metrics:**
- ✅ **7/10 queries** performed well (≥50% precision)
- ⚠️ **1/10 queries** had poor relevance (0% precision)
- ❌ **2/10 queries** returned zero results despite relevant content existing
- 📊 Average precision@10: **68%**

---

## 🔴 Critical Issues

### 1. Multi-Word Query Failure (Zero Recall)

**Query:** `"orchestrator task routing"`  
**Expected:** Memories about orchestrator, task delegation, routing logic  
**Actual:** 0 results

**Root Cause:** ILIKE requires exact substring match. Query looks for literal string "orchestrator task routing" but content contains these words separated:
- "orchestrator handles task assignment"  
- "routing tasks to agents"  
- "task orchestrator logic"

**Impact:** Users cannot find relevant information using natural multi-word queries. This is a **fundamental usability issue**.

**Evidence:**
```python
# Current implementation (app/routers/memories.py:95-98)
query = select(MemoryModel).where(
    or_(
        MemoryModel.title.ilike(f"%{q}%"),
        MemoryModel.content.ilike(f"%{q}%")
    )
)
```

This looks for exact substring `%orchestrator task routing%` which doesn't exist.

---

### 2. Recent Feature Not Discoverable

**Query:** `"learning feedback outcome"`  
**Expected:** Memories about the agent learning system (documented in ARCHITECTURE.md as recent change)  
**Actual:** 0 results

**Root Cause:** Same substring matching limitation. Even though the learning system is documented, the exact phrase isn't used.

**Impact:** **New architectural features are not discoverable** through search, forcing users to manually browse or rely on external documentation.

---

## 🟡 Important Issues

### 3. Generic Query Poor Relevance

**Query:** `"how to"`  
**Expected:** Tutorials, guides, procedural documentation  
**Actual:** 27 results, 0% relevant (0/10 in top 10)

**Root Cause:** Over-matching on common phrase "how to" appearing in random contexts:
- "...clients know **how to** use safely..."  
- "...overview ✓ **How to** improve SWE-bench..."

**Impact:** High-frequency phrases pollute results with false positives. **No ranking by relevance** beyond title/content distinction.

**Example False Positive:**
```
"...standard tool interface that LLM clients know how to use safely and consistently."
```
This matches "how to" but is NOT a tutorial/guide.

---

### 4. No Semantic Understanding

**Observation:** Search cannot match:
- **Synonyms:** "authentication" won't find "login", "auth", "security"
- **Related concepts:** "bug" won't find "error", "crash", "failure" (unless explicitly mentioned)
- **Paraphrased queries:** Different wording of the same concept

**Current workaround:** Users must guess exact terminology used in memories.

---

### 5. Primitive Scoring Algorithm

**Current scoring (app/routers/memories.py:107-111):**
```python
score = 0.0
if query_lower in memory.title.lower():
    score += 2.0
if query_lower in memory.content.lower():
    score += 1.0
```

**Problems:**
1. **Binary scoring:** Either matches or doesn't, no partial credit
2. **No term frequency consideration:** 1 mention = 100 mentions
3. **No position weighting:** Match at start vs. end treated equally
4. **No recency bias:** Old memories rank same as new ones
5. **Ties not broken meaningfully:** Many results have same score

**Evidence:** 17 results for "authentication" all scored 1.0 (tied), order is essentially random among ties.

---

## ✅ What Works Well

### Strong Single-Term Performance

**Queries with 100% precision:**
- `"authentication"` → 17 results, 10/10 relevant
- `"API endpoint"` → 22 results, 10/10 relevant
- `"database migration"` → 3 results, 3/3 relevant
- `"memory system"` → 9 results, 9/9 relevant
- `"test coverage"` → 22 results, 10/10 relevant

**Why it works:** Single terms or exact 2-word phrases that appear as-is in content. Substring matching is sufficient for literal matches.

### Good Error Handling

- Empty queries return empty results (correct behavior)
- Agent filtering works correctly
- No crashes or exceptions during testing

---

## 📊 Detailed Test Results

| Query | Results | Relevant/10 | Precision | Status |
|-------|---------|-------------|-----------|--------|
| authentication | 17 | 10 | 100% | ✅ Good |
| bug | 72 | 8 | 80% | ✅ Good |
| API endpoint | 22 | 10 | 100% | ✅ Good |
| database migration | 3 | 3 | 100% | ✅ Good |
| **orchestrator task routing** | **0** | **0** | **N/A** | ❌ **Failed** |
| memory system | 9 | 9 | 100% | ✅ Good |
| how to | 27 | 0 | 0% | ⚠️ Poor |
| performance optimization | 2 | 2 | 100% | ✅ Good |
| test coverage | 22 | 10 | 100% | ✅ Good |
| **learning feedback outcome** | **0** | **0** | **N/A** | ❌ **Failed** |

---

## 🎯 Recommendations

### Priority 1: Multi-Word Query Support (Critical)

**Problem:** 20% of queries return zero results due to word separation.

**Solutions (pick one):**

#### Option A: Token-Based Search (Quick Fix)
Split query into tokens, match ANY token:
```python
tokens = q.lower().split()
conditions = []
for token in tokens:
    conditions.append(MemoryModel.title.ilike(f"%{token}%"))
    conditions.append(MemoryModel.content.ilike(f"%{token}%"))
query = select(MemoryModel).where(or_(*conditions))
```

**Pros:** Easy to implement, immediate improvement  
**Cons:** Will over-match, lots of false positives  
**Effort:** 1-2 hours

#### Option B: SQLite FTS5 (Better Solution)
Use SQLite's full-text search with `MATCH`:
```python
# Requires FTS5 virtual table
# CREATE VIRTUAL TABLE memories_fts USING fts5(title, content);
query = "SELECT * FROM memories_fts WHERE memories_fts MATCH ?"
```

**Pros:** 
- Built-in ranking (BM25)
- Handles multi-word queries properly
- Phrase queries with quotes
- Boolean operators (AND, OR, NOT)

**Cons:** Requires schema migration, index maintenance  
**Effort:** 4-8 hours (migration + testing)

#### Option C: Vector/Semantic Search (Future)
Use embeddings for semantic similarity:
- Requires embedding model (OpenAI, local Sentence Transformers)
- Vector database (pgvector, ChromaDB, Qdrant)
- Async embedding generation

**Pros:** True semantic search, synonym matching  
**Cons:** Complex infrastructure, cost/latency  
**Effort:** 2-3 days

**Recommendation:** Start with **Option B (FTS5)** for immediate multi-word support with good ranking. Consider Option C for future semantic capabilities.

---

### Priority 2: Improve Scoring Algorithm (Important)

**Current scoring is too simplistic.** Implement TF-IDF or BM25-style scoring:

```python
# Pseudo-code for better scoring
score = 0.0

# Term frequency (multiple mentions = higher score)
title_matches = title.lower().count(query_lower)
content_matches = content.lower().count(query_lower)

score += title_matches * 5.0  # Title matches worth more
score += content_matches * 1.0

# Position bonus (earlier = better)
first_pos = content.lower().find(query_lower)
if first_pos >= 0:
    position_score = 1.0 / (1.0 + first_pos / 1000.0)
    score += position_score

# Recency bonus (newer = slightly better)
age_days = (now - memory.updated_at).days
recency_score = 1.0 / (1.0 + age_days / 30.0)
score += recency_score
```

**Effort:** 2-3 hours

---

### Priority 3: Add Query Preprocessing (Quick Win)

Clean and normalize queries:
```python
def preprocess_query(q: str) -> str:
    q = q.strip().lower()
    q = re.sub(r'\s+', ' ', q)  # Normalize whitespace
    q = re.sub(r'[^\w\s-]', '', q)  # Remove special chars
    return q
```

Add common expansions:
```python
SYNONYMS = {
    "auth": ["authentication", "login", "token"],
    "bug": ["error", "issue", "problem", "fix"],
    "api": ["endpoint", "route", "REST"],
}
```

**Effort:** 1-2 hours

---

### Priority 4: Add Search Analytics (Monitoring)

Track search quality over time:
```python
class SearchLog(Base):
    query = Column(String)
    result_count = Column(Integer)
    clicked_result_id = Column(Integer, nullable=True)
    timestamp = Column(DateTime)
```

**Metrics to track:**
- Zero-result queries (should be <5%)
- Click-through rate (users finding what they need?)
- Most common queries (optimize for these)

**Effort:** 2-3 hours

---

## 🔬 Testing Gaps

### Missing Test Coverage

**Current tests (test_memories.py):**
- ✅ Basic CRUD operations
- ✅ Search with single terms
- ✅ Agent filtering
- ✅ Pagination

**Missing tests:**
- ❌ Multi-word phrase queries
- ❌ Search result ranking/scoring
- ❌ Search relevance (precision/recall)
- ❌ Query edge cases (special chars, very long queries)
- ❌ Performance with large result sets

**Recommendation:** Add `test_search_relevance.py` with:
```python
@pytest.mark.asyncio
async def test_multiword_query():
    """Multi-word queries should match words in any order."""
    # Create memory with "orchestrator handles task routing"
    # Query "task routing orchestrator"
    # Should return the memory

@pytest.mark.asyncio
async def test_search_ranking():
    """Results should be ranked by relevance."""
    # Create 3 memories with different match quality
    # Verify best match comes first
```

---

## 💡 Future Enhancements

### 1. Faceted Search
- Filter by agent, date range, memory_type
- Sort by date, relevance, length

### 2. Query Suggestions
- "Did you mean..." for typos
- "Related searches" based on query patterns

### 3. Search Highlighting
- Return match positions for highlighting in UI
- Show multiple snippets per result

### 4. Advanced Syntax
- Boolean operators: `+required -excluded "exact phrase"`
- Field-specific: `title:bug agent:programmer`
- Date filters: `after:2026-02-01`

---

## 📝 Implementation Checklist

If implementing FTS5 (recommended):

- [ ] Create migration script for FTS5 virtual table
- [ ] Add triggers to keep FTS5 in sync with memories table
- [ ] Update search endpoint to use FTS5 MATCH
- [ ] Implement BM25 ranking
- [ ] Add phrase query support (quotes)
- [ ] Write integration tests for multi-word queries
- [ ] Update API documentation
- [ ] Add search analytics logging
- [ ] Monitor zero-result rate in production

**Estimated effort:** 1-2 days for full implementation + testing

---

## 🎓 Lessons Learned

1. **Simple substring matching is insufficient** for production search - works for 70% of cases but fails catastrophically on the remaining 30%

2. **Multi-word queries are table stakes** - users expect this to work, it's not an advanced feature

3. **Search is a product feature, not just technical implementation** - requires ranking, relevance tuning, and analytics

4. **SQLite FTS5 is underutilized** - provides excellent full-text search without external dependencies

5. **Test with realistic queries** - single-word tests pass but don't represent actual usage patterns

---

## Conclusion

The current memory search is **functional for basic single-term queries but inadequate for production use**. The 20% failure rate on multi-word queries and 0% precision on common phrases like "how to" indicate fundamental limitations.

**Immediate action required:**
1. Implement SQLite FTS5 for multi-word query support (1-2 days)
2. Improve scoring beyond binary title/content matching (2-3 hours)
3. Add search relevance tests (2-3 hours)

**Without these improvements, users will:**
- Miss relevant memories due to phrasing differences
- Get frustrated with zero-result searches
- Resort to manual browsing or external grep searches
- Lose confidence in the memory system

The good news: SQLite FTS5 provides a solid foundation for production-quality search without external dependencies. This is a solvable problem with well-established solutions.
