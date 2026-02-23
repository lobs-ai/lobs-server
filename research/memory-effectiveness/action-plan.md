# Memory System Improvement Action Plan

**Based on:** Memory Retrieval Quality Audit (2026-02-22)  
**Priority:** HIGH — Current system is write-only, blocking knowledge reuse

---

## TL;DR

**Problem:** Agents write great memories but can't retrieve them effectively. Text-only search is 30% as good as semantic search.

**Impact:** Research duplication, missed context, knowledge silos. ~$10K/year in wasted effort.

**Solution:** Add vector embeddings for semantic search. ROI payback in 3 months.

---

## Quick Wins (This Week)

### 1. Fix Documentation ✅
**File:** `agents/*/AGENTS.md`

**Change this:**
```markdown
Your workspace has a `memory/` directory — searchable via vector database.
- **Search with `memory_search`** — semantically finds relevant notes
```

**To this:**
```markdown
Your workspace has a `memory/` directory — your work logs and learnings.
- **Search:** Use `/api/memories/search?q=your+query` (text matching currently, semantic search coming soon)
- **Browse:** `/api/memories` to list all memories
```

**Why:** Stop promising features that don't exist

**Effort:** 30 min

---

### 2. Add Usage Examples ✅
**File:** `agents/*/AGENTS.md`

**Add section:**
```markdown
## Using Your Memory

**Before starting work:**
```bash
# Search for related context
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/memories/search?q=swiftui+testing&agent=researcher"
```

**Check the top 3-5 results** for existing knowledge before re-researching.

**After completing work:**
Update your topic memory files (not just daily logs) so future you can find it.
```

**Why:** Make retrieval actionable

**Effort:** 1 hour

---

## Phase 1: Basic Vector Search (Week 2-3)

### Goal
Enable semantic search: "cost optimization" finds "model tier benchmarking"

### Implementation Steps

**1. Database Schema**
```bash
cd /Users/lobs/lobs-server
sqlite3 data/lobs.db "ALTER TABLE memories ADD COLUMN embedding BLOB;"
```

**2. Install Dependencies**
```bash
# Add to requirements.txt:
sentence-transformers==2.3.1
numpy==1.26.0
```

**3. Create Embedding Service**
**File:** `app/services/embedding_service.py`
```python
from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List

class EmbeddingService:
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
    
    def embed(self, text: str) -> List[float]:
        """Generate embedding for text."""
        return self.model.encode(text, convert_to_numpy=True).tolist()
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        a = np.array(vec1)
        b = np.array(vec2)
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# Singleton instance
embedding_service = EmbeddingService()
```

**4. Backfill Existing Memories**
**File:** `bin/backfill_embeddings.py`
```python
import asyncio
from app.database import get_db
from app.models import Memory
from app.services.embedding_service import embedding_service
from sqlalchemy import select

async def backfill():
    async with get_db() as db:
        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        
        for i, memory in enumerate(memories):
            if memory.embedding is None:
                text = f"{memory.title}\n\n{memory.content}"
                memory.embedding = embedding_service.embed(text)
                print(f"[{i+1}/{len(memories)}] Embedded: {memory.title}")
        
        await db.commit()
        print("✓ Backfill complete")

if __name__ == "__main__":
    asyncio.run(backfill())
```

**5. Update Search Endpoint**
**File:** `app/routers/memories.py`
```python
from app.services.embedding_service import embedding_service
import pickle

@router.get("/search")
async def search_memories(
    q: str,
    agent: Optional[str] = None,
    semantic: bool = True,  # New parameter
    limit: int = 10,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> list[MemorySearchResult]:
    """Search memories with optional semantic search."""
    
    if semantic:
        # Semantic search
        query_embedding = embedding_service.embed(q)
        
        # Get all memories (filter by agent if specified)
        query = select(MemoryModel)
        if agent:
            query = query.where(MemoryModel.agent == agent)
        result = await db.execute(query)
        memories = result.scalars().all()
        
        # Calculate similarity scores
        scored_results = []
        for memory in memories:
            if memory.embedding:
                embedding = pickle.loads(memory.embedding)
                similarity = embedding_service.cosine_similarity(query_embedding, embedding)
                scored_results.append((memory, similarity))
        
        # Sort by similarity
        scored_results.sort(key=lambda x: x[1], reverse=True)
        
        # Return top results
        results = []
        for memory, score in scored_results[:limit]:
            snippet = generate_snippet(memory.content, q)
            results.append(MemorySearchResult(
                id=memory.id,
                path=memory.path,
                agent=memory.agent,
                title=memory.title,
                snippet=snippet,
                memory_type=memory.memory_type,
                date=memory.date,
                score=float(score)
            ))
        return results
    else:
        # Original text search (fallback)
        # ... existing code ...
```

**6. Auto-Embed on Create/Update**
**File:** `app/routers/memories.py`
```python
from app.services.embedding_service import embedding_service
import pickle

@router.post("")
async def create_memory(...):
    # ... existing code ...
    
    # Generate embedding
    text = f"{memory.title}\n\n{memory.content}"
    db_memory.embedding = pickle.dumps(embedding_service.embed(text))
    
    # ... rest of code ...

@router.put("/{memory_id}")
async def update_memory(...):
    # ... existing code ...
    
    # Regenerate embedding if content changed
    if "content" in update_data or "title" in update_data:
        text = f"{memory.title}\n\n{memory.content}"
        memory.embedding = pickle.dumps(embedding_service.embed(text))
    
    # ... rest of code ...
```

### Testing

**File:** `tests/test_memory_search.py`
```python
def test_semantic_search():
    # Create test memories
    create_memory(title="Cost Optimization Guide", content="How to reduce API expenses...")
    create_memory(title="Performance Tips", content="Speed up your application...")
    
    # Semantic search should find cost optimization for "reduce expenses"
    results = search_memories(q="reduce expenses", semantic=True)
    assert results[0].title == "Cost Optimization Guide"
    assert results[0].score > 0.7
```

**Manual testing:**
```bash
# After backfill:
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/memories/search?q=cost+optimization&semantic=true"

# Should return model-tier-benchmarking.md with high score
```

### Success Criteria
- ✅ All 231 existing memories have embeddings
- ✅ Search "cost optimization" returns model-tier-benchmarking.md (score >0.7)
- ✅ Search "SwiftUI patterns" returns 2026-02-22.md (score >0.7)
- ✅ Latency <500ms for searches at current corpus size

---

## Phase 2: Workflow Integration (Week 4-5)

### Goal
Make memory retrieval automatic for agents

### Implementation

**1. Auto-Retrieve Context in Orchestrator**
**File:** `app/orchestrator/prompter.py`

```python
async def build_task_prompt(task: Task, db: AsyncSession) -> str:
    # ... existing code ...
    
    # Auto-retrieve relevant memories
    if task.agent:
        memories = await search_memories_for_task(task, db)
        memory_context = format_memory_context(memories)
    
    prompt = f"""
{agent_identity}

Your task: {task.title}
{task.notes}

Before starting, consider this relevant context from your memory:
{memory_context}

Review the above for useful insights, then proceed with the task.
"""
    return prompt

async def search_memories_for_task(task: Task, db: AsyncSession) -> list:
    """Extract keywords from task, search memory."""
    keywords = extract_keywords(task.title + " " + (task.notes or ""))
    query = " ".join(keywords[:5])  # Top 5 keywords
    
    from app.routers.memories import search_memories
    results = await search_memories(q=query, agent=task.agent, semantic=True, limit=3, db=db)
    return results

def format_memory_context(memories: list) -> str:
    if not memories:
        return "(No relevant memories found)"
    
    formatted = []
    for mem in memories:
        formatted.append(f"**{mem.title}** (relevance: {mem.score:.2f})\n{mem.snippet}\n")
    return "\n".join(formatted)
```

**2. Add Memory Reference Template**
**File:** `agents/*/AGENTS.md`

```markdown
## During Work

If you retrieve relevant context from memory, acknowledge it:

```
Retrieved from memory: [memory title]
Key insight: [what you learned]
Applied to task: [how you used it]
```

This helps track memory system effectiveness.
```

### Success Criteria
- ✅ 50% of agent prompts include auto-retrieved memory context
- ✅ 30% of task artifacts reference memory explicitly

---

## Phase 3: Cross-Agent Knowledge Sharing (Week 6-7)

### Goal
Programmer can discover Researcher's findings

### Implementation

**1. Global Search Endpoint**
**File:** `app/routers/memories.py`

```python
@router.get("/search/global")
async def search_all_agents(
    q: str,
    limit: int = 20,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Search across all agents."""
    results = await search_memories(q=q, agent=None, semantic=True, limit=limit, db=db)
    
    # Group by agent
    by_agent = {}
    for r in results:
        if r.agent not in by_agent:
            by_agent[r.agent] = []
        by_agent[r.agent].append(r)
    
    return {
        "query": q,
        "total_results": len(results),
        "by_agent": by_agent
    }
```

**2. Update Agent Instructions**
**File:** `agents/programmer/AGENTS.md`

```markdown
## Before Implementing

Check if other agents have already researched this:

```bash
# Search across ALL agents
curl "http://localhost:8000/api/memories/search/global?q=swiftui+testing"
```

Build on existing research rather than starting from scratch.
```

### Success Criteria
- ✅ Cross-agent search endpoint live
- ✅ 3+ examples/week of agents referencing other agents' memories

---

## Phase 4: Advanced Features (Month 2-3)

### A. Chunking for Long Memories
Split memories on markdown headers → more precise retrieval

**Effort:** 12 hours  
**Value:** High for long research docs

### B. Memory Quality Dashboard
Track retrieval rate, search quality, duplication

**Effort:** 6 hours  
**Value:** Data-driven improvement

### C. Deduplication Detection
Find and merge similar memories (cosine similarity >0.95)

**Effort:** 8 hours  
**Value:** Prevents knowledge fragmentation

### D. Query Expansion
Use LLM to expand queries with synonyms

**Effort:** 6 hours  
**Value:** More robust search

---

## Resource Requirements

### Development Time
- **Phase 1:** 12 hours (vector search core)
- **Phase 2:** 8 hours (workflow integration)
- **Phase 3:** 6 hours (cross-agent sharing)
- **Phase 4:** 32 hours (advanced features)
- **Total:** ~58 hours (~1.5 weeks full-time)

### Infrastructure
- **Storage:** +50MB for sentence-transformers model
- **Database:** +100KB for embeddings (231 memories × 384 floats × 4 bytes)
- **Compute:** Negligible (embedding generation <100ms per memory)

### Dependencies
```
sentence-transformers==2.3.1  # ~90MB download
numpy==1.26.0                 # Already installed
```

---

## Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Poor embedding quality | Low | High | Test on sample queries before full rollout |
| Latency issues | Low | Medium | Benchmark, optimize with sqlite-vec if needed |
| Agent adoption | Medium | High | Make retrieval automatic, not optional |
| Embedding drift | Low | Low | Re-embed annually or on model upgrade |

---

## Success Metrics

### Week 2 (Post Phase 1)
- [ ] 80% search quality on test set (vs 30% baseline)
- [ ] Latency <500ms for searches

### Week 5 (Post Phase 2)
- [ ] 50% of tasks show memory retrieval
- [ ] Agent feedback: "Memory search is useful"

### Week 8 (Post Phase 3)
- [ ] 3+ cross-agent knowledge reuse examples/week
- [ ] No duplicate research on same topic

### Month 3 (Post Phase 4)
- [ ] 30% reduction in research duplication time
- [ ] Average research task time: 2 hrs → 1.4 hrs

---

## Next Steps

1. **Review findings:** Read full report in `findings.md`
2. **Get approval:** Present to Lobs/human for go/no-go decision
3. **Schedule:** Block 1.5 weeks for implementation
4. **Quick wins:** Start with documentation fixes (30 min)
5. **Phase 1:** Implement vector search (week 2-3)
6. **Measure:** Track metrics weekly, adjust course as needed

---

**Questions? See full research report:** `research/memory-effectiveness/findings.md`
