# Design: Research Project Data Model

**Date:** 2026-02-13  
**Task ID:** 6FD6B232-F35A-455D-A16B-EDCA8587103D  
**Author:** Architect Agent

---

## Problem Statement

Research is currently bolted onto the project/task system. A "research project" is just a `Project` with `type="research"`, and research data (docs, sources, requests) are child tables keyed by `project_id`. This creates confusion:

- Research requests live alongside tasks but have a completely different lifecycle (prompt → response, not kanban)
- The `Project` model serves triple duty (kanban/research/tracker) with `type` field disambiguation
- Research has its own concepts (sources, documents, requests with deliverables) that don't map to tasks at all
- The dashboard already has separate views for research — the data model should match

Rafe wants research as a first-class concept, cleanly separated from the task workflow.

## Current State

**What exists:**
- `Project.type = "research"` — projects can be research projects
- `ResearchDoc` — one markdown document per project (content blob)
- `ResearchSource` — URLs/references attached to a project
- `ResearchRequest` — prompts with responses, status, deliverables, parent chaining
- Router: `GET/PUT /research/{project_id}/doc`, CRUD for sources and requests

**What's wrong:**
- Research is a project subtype, not its own entity
- No way to have research *within* a kanban project (research is always a separate project)
- ResearchDoc is a single blob — no structure, no sections, no versioning
- No lifecycle states for research projects themselves (exploring/writing/complete)

## Proposed Solution

### Keep Research Under Projects (Don't Split)

After reviewing the codebase, **I recommend NOT creating a separate top-level entity.** Here's why:

1. The `Project` model already supports `type="research"` and it works
2. The router already nests research under `/{project_id}/`
3. The dashboard already filters projects by type
4. Creating a parallel entity doubles the CRUD surface for marginal benefit
5. The real problem isn't the project association — it's that the research-specific models are too thin

**Instead: enrich the research models within the existing project structure.**

### Model Changes

#### 1. Add `ResearchProject` metadata table

New table for research-specific project metadata (separate from generic `Project`):

```python
class ResearchProjectMeta(Base):
    """Research-specific metadata for projects with type='research'."""
    __tablename__ = "research_project_meta"
    
    project_id = Column(String, ForeignKey("projects.id"), primary_key=True)
    status = Column(String, default="exploring")  # exploring/active/writing/complete/archived
    methodology = Column(String)  # freeform: "literature review", "empirical", etc.
    hypothesis = Column(Text)  # what we're investigating
    conclusion = Column(Text)  # what we found (filled when complete)
    tags = Column(JSON)  # searchable tags
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
```

This enriches research projects without polluting the generic `Project` table. One-to-one with `Project` where `type='research'`.

#### 2. Evolve ResearchDoc into sections

Replace the single-blob `ResearchDoc` with a structured document:

```python
class ResearchSection(Base):
    """A section within a research document."""
    __tablename__ = "research_sections"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text)
    sort_order = Column(Integer, default=0)
    section_type = Column(String, default="body")  # summary/body/findings/methodology/references
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
```

Keep `ResearchDoc` as-is for backward compat (the single blob). Add `ResearchSection` as the new structured approach. Migration path: dashboard can read either format.

#### 3. Improve ResearchRequest lifecycle

The existing `ResearchRequest` model is decent but needs clearer status values and linkage:

**Status values (document, don't change the column):**
- `pending` — submitted, waiting for agent
- `in_progress` — agent is working on it
- `completed` — response delivered
- `rejected` — not pursuing
- `follow_up` — spawned from another request (use `parent_request_id`)

**Add to ResearchRequest:**
```python
# These columns already exist or can be added:
completed_at = Column(DateTime)  # NEW: when response was delivered
section_id = Column(String, ForeignKey("research_sections.id"))  # NEW: link response to a doc section
```

#### 4. Add ResearchFinding for discrete insights

```python
class ResearchFinding(Base):
    """A discrete finding/insight from research."""
    __tablename__ = "research_findings"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    request_id = Column(String, ForeignKey("research_requests.id"))  # which request produced this
    title = Column(String, nullable=False)
    content = Column(Text)
    confidence = Column(String)  # high/medium/low/speculative
    tags = Column(JSON)
    created_at = Column(DateTime, default=func.now())
```

This lets agents and humans capture individual insights that can be organized into sections later.

### API Changes

Add to the existing `/research/{project_id}/` router:

```
# Research project metadata
GET    /research/{project_id}/meta
PUT    /research/{project_id}/meta

# Structured document sections (new)
GET    /research/{project_id}/sections
POST   /research/{project_id}/sections
PUT    /research/{project_id}/sections/{section_id}
DELETE /research/{project_id}/sections/{section_id}
PATCH  /research/{project_id}/sections/reorder

# Findings (new)
GET    /research/{project_id}/findings
POST   /research/{project_id}/findings
PUT    /research/{project_id}/findings/{finding_id}
DELETE /research/{project_id}/findings/{finding_id}

# Existing (unchanged)
GET/PUT    /research/{project_id}/doc          # legacy blob, keep for compat
GET/POST   /research/{project_id}/sources
DELETE     /research/{project_id}/sources/{id}
GET/POST   /research/{project_id}/requests
GET/PUT/DELETE /research/{project_id}/requests/{id}
```

### Research Lifecycle

```
exploring → active → writing → complete
    ↑                            |
    └────── (reopen) ────────────┘
```

- **exploring**: Gathering sources, making requests, collecting findings
- **active**: Main research phase, requests being fulfilled  
- **writing**: Organizing findings into sections, drafting conclusions
- **complete**: Research is done, conclusion written

Status lives on `ResearchProjectMeta.status`, not on `Project`.

---

## Tradeoffs

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Keep under Project vs new entity | Keep under Project | Separate `Research` table | Less disruption. Project already handles this. Adding a parallel entity doubles CRUD. |
| Sections vs single blob | Both (sections + legacy blob) | Replace blob | Backward compat. Dashboard currently reads the blob. Migrate incrementally. |
| Findings as separate table | Yes | Inline in sections | Findings come from requests/agents. They need to exist before being organized into sections. |
| Status on meta table vs Project | Meta table | Add to Project | Keeps Project generic. Research lifecycle is research-specific. |

---

## Implementation Plan

### Task 1: Add ResearchProjectMeta model + API (Small)

- Add `ResearchProjectMeta` to `models.py`
- Add schemas to `schemas.py`
- Add `GET/PUT /research/{project_id}/meta` to `research.py` router
- Auto-create meta row when a research project is created (or lazily on first GET)
- Migration: Alembic migration for new table

**Acceptance:** Can create/read/update research project metadata. Status lifecycle works.

### Task 2: Add ResearchSection model + API (Small-Medium)

- Add `ResearchSection` to `models.py`
- Add schemas + CRUD endpoints to research router
- Add reorder endpoint (PATCH with list of section IDs)
- Keep existing `ResearchDoc` blob endpoints working

**Acceptance:** Can create/list/update/delete/reorder sections. Legacy doc endpoint still works.

### Task 3: Add ResearchFinding model + API (Small)

- Add `ResearchFinding` to `models.py`
- Add schemas + CRUD endpoints
- Link findings to requests (optional `request_id`)

**Acceptance:** Can create/list/update/delete findings. Findings linked to requests.

### Task 4: Add completed_at and section_id to ResearchRequest (Small)

- Alembic migration to add columns
- Update schemas to include new fields
- Auto-set `completed_at` when status changes to `completed`

**Acceptance:** Requests track completion time. Responses can link to document sections.

---

## Testing Strategy

- **Unit tests** for each new model (CRUD operations)
- **API tests** for each new endpoint (happy path + 404s + validation)
- **Migration test** — ensure Alembic migration runs cleanly on existing DB
- **Backward compat test** — existing `/research/{project_id}/doc` endpoint still works

---

## Handoffs

```json
[
  {
    "to": "programmer",
    "initiative": "research-model",
    "title": "Add ResearchProjectMeta model, schema, and API endpoints",
    "context": "See docs/designs/research-data-model.md Task 1. New table for research-specific project metadata with status lifecycle. Auto-create on first access.",
    "acceptance": "GET/PUT /research/{project_id}/meta works. Alembic migration clean. Tests pass.",
    "files": ["docs/designs/research-data-model.md", "app/models.py", "app/schemas.py", "app/routers/research.py"]
  },
  {
    "to": "programmer",
    "initiative": "research-model",
    "title": "Add ResearchSection model and CRUD API",
    "context": "See docs/designs/research-data-model.md Task 2. Structured document sections replacing the single-blob approach. Keep legacy doc endpoint.",
    "acceptance": "CRUD for sections works. Reorder endpoint works. Legacy doc endpoint still works. Tests pass.",
    "files": ["docs/designs/research-data-model.md", "app/models.py", "app/schemas.py", "app/routers/research.py"]
  },
  {
    "to": "programmer",
    "initiative": "research-model",
    "title": "Add ResearchFinding model and CRUD API",
    "context": "See docs/designs/research-data-model.md Task 3. Discrete findings linked to requests.",
    "acceptance": "CRUD for findings works. Optional request_id linkage. Tests pass.",
    "files": ["docs/designs/research-data-model.md", "app/models.py", "app/schemas.py", "app/routers/research.py"]
  },
  {
    "to": "programmer",
    "initiative": "research-model",
    "title": "Add completed_at and section_id columns to ResearchRequest",
    "context": "See docs/designs/research-data-model.md Task 4. Migration + schema update. Auto-set completed_at on status change.",
    "acceptance": "Migration runs clean. completed_at auto-set. section_id linkage works. Tests pass.",
    "files": ["docs/designs/research-data-model.md", "app/models.py", "app/schemas.py", "app/routers/research.py"]
  }
]
```
