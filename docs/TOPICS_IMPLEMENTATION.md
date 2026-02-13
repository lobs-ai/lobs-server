# Topics Implementation Documentation

## Overview

The Topics feature provides a first-class knowledge organization system for lobs-server. Topics replace the previous string-based topic field on agent documents with a proper relational model.

## Database Schema

### Topic Model

```python
class Topic(Base):
    __tablename__ = "topics"
    
    id: str                          # Primary key
    title: str                       # Unique topic title
    description: str (optional)      # Topic description
    icon: str (optional)             # Emoji or icon name
    linked_project_id: str (optional) # FK to projects
    auto_created: bool               # Whether auto-created from migration
    created_at: datetime
    updated_at: datetime
```

### Updated Models

**AgentDocument**:
- Removed: `topic: str`
- Added: `topic_id: str` (FK to topics.id)

**ResearchRequest**:
- Added: `topic_id: str` (FK to topics.id)

## API Endpoints

All endpoints require Bearer token authentication.

### List Topics
```
GET /api/topics?limit=100&offset=0
```

Returns paginated list of topics, ordered by title.

**Response**:
```json
[
  {
    "id": "topic-lobs-orchestrator-368713c1",
    "title": "lobs-orchestrator",
    "description": null,
    "icon": null,
    "linked_project_id": null,
    "auto_created": true,
    "created_at": "2026-02-13T23:24:00Z",
    "updated_at": "2026-02-13T23:24:00Z"
  }
]
```

### Create Topic
```
POST /api/topics
```

**Request**:
```json
{
  "id": "topic-my-topic",
  "title": "My Topic",
  "description": "A custom topic",
  "icon": "📚",
  "linked_project_id": "project-123",
  "auto_created": false
}
```

**Response**: Topic object (200 OK)  
**Errors**: 
- 400 if topic with same title already exists
- 400 if linked_project_id doesn't exist (FK constraint)

### Get Topic
```
GET /api/topics/{topic_id}
```

**Response**: Topic object (200 OK)  
**Errors**: 404 if not found

### Update Topic
```
PUT /api/topics/{topic_id}
```

**Request** (all fields optional):
```json
{
  "title": "Updated Title",
  "description": "New description",
  "icon": "📝"
}
```

**Response**: Updated topic object (200 OK)  
**Errors**:
- 404 if not found
- 400 if updated title conflicts with existing topic

### Delete Topic
```
DELETE /api/topics/{topic_id}
```

**Response**: `{"status": "deleted"}` (200 OK)  
**Errors**:
- 404 if not found
- FK constraint error if documents still reference this topic

### Get Topic Documents
```
GET /api/topics/{topic_id}/documents?limit=100&offset=0
```

Returns all agent documents linked to this topic.

**Response**: Array of AgentDocument objects (200 OK)  
**Errors**: 404 if topic not found

## Migration

A migration script (`migrations/create_topics_table.py`) was executed to:

1. Create the `topics` table
2. Extract 13 distinct topic strings from existing agent_documents
3. Create Topic records with auto_created=true
4. Add topic_id column to agent_documents
5. Update 69 agent documents with proper FK references
6. Remove the old topic string column
7. Add topic_id column to research_requests

### Migration Results

- Created 13 topics from existing data:
  - lobs-orchestrator
  - chicago
  - lobs-dashboard
  - default
  - self-improvement
  - eecs-291
  - flock
  - proposals
  - personal-assistant-ideas
  - cse-590
  - prairielearn
  - eecs-545
  - project-ideas

- Updated 69 agent documents with topic_id references

## Pydantic Schemas

### TopicBase
```python
title: str
description: Optional[str]
icon: Optional[str]
linked_project_id: Optional[str]
auto_created: bool = False
```

### TopicCreate (extends TopicBase)
```python
id: str  # Required for creation
```

### TopicUpdate
```python
# All fields optional
title: Optional[str]
description: Optional[str]
icon: Optional[str]
linked_project_id: Optional[str]
auto_created: Optional[bool]
```

### Topic (extends TopicBase)
```python
id: str
created_at: datetime
updated_at: datetime
```

## Updated Document Schemas

**AgentDocumentBase**:
- Removed: `topic: Optional[str]`
- Added: `topic_id: Optional[str]`

**ResearchRequestBase**:
- Added: `topic_id: Optional[str]`

## Testing

All endpoints are covered by comprehensive tests in `tests/test_topics.py`:

- ✅ List topics (empty, with data, pagination)
- ✅ Create topic (success, duplicate prevention, minimal fields)
- ✅ Get topic (success, not found)
- ✅ Update topic (description/icon, title, not found)
- ✅ Delete topic (success, not found)
- ✅ Get topic documents (empty, not found)
- ✅ Auto-created flag functionality

**Test Results**: 15/15 passed (100% success rate)

## Usage Examples

### Create a Topic for a Project
```python
import httpx

topic = {
    "id": "topic-ai-research",
    "title": "AI Research",
    "description": "Research related to artificial intelligence",
    "icon": "🤖",
    "linked_project_id": "research-project-123",
    "auto_created": False
}

response = httpx.post(
    "http://localhost:8000/api/topics",
    json=topic,
    headers={"Authorization": "Bearer your-token"}
)
```

### Link a Document to a Topic
```python
document = {
    "id": "doc-123",
    "title": "GPT-4 Analysis",
    "content": "...",
    "topic_id": "topic-ai-research",  # Link to topic
    "source": "researcher"
}

response = httpx.post(
    "http://localhost:8000/api/documents",
    json=document,
    headers={"Authorization": "Bearer your-token"}
)
```

### Get All Documents for a Topic
```python
response = httpx.get(
    "http://localhost:8000/api/topics/topic-ai-research/documents",
    headers={"Authorization": "Bearer your-token"}
)

documents = response.json()
```

## Future Enhancements

Potential improvements for the Topics system:

1. **Bulk Operations**: Add endpoints for bulk topic assignment to documents
2. **Topic Stats**: Add document count, last updated, etc. to topic responses
3. **Topic Hierarchy**: Support parent-child relationships between topics
4. **Auto-suggestion**: ML-based topic suggestion for new documents
5. **Topic Merging**: Endpoint to merge two topics
6. **Soft Delete**: Instead of hard delete, mark topics as archived
7. **Topic Colors**: Add color field for UI visualization
8. **Topic Templates**: Predefined topic sets for different use cases

## Breaking Changes

### For Clients

If you were using the old `topic` string field on agent_documents:

**Before**:
```json
{
  "id": "doc-1",
  "title": "My Doc",
  "topic": "lobs-orchestrator"
}
```

**After**:
```json
{
  "id": "doc-1",
  "title": "My Doc",
  "topic_id": "topic-lobs-orchestrator-368713c1"
}
```

### Migration Path

1. Update your client code to use `topic_id` instead of `topic`
2. To find the topic_id for a topic name:
   ```
   GET /api/topics -> find topic by title
   ```
3. Consider caching topic_id mappings in your client

## Files Modified

### Created:
- `app/routers/topics.py` - API endpoints
- `migrations/create_topics_table.py` - Migration script
- `tests/test_topics.py` - Test suite
- `docs/TOPICS_IMPLEMENTATION.md` - This documentation

### Modified:
- `app/models.py` - Added Topic model, updated AgentDocument & ResearchRequest
- `app/schemas.py` - Added Topic schemas, updated document schemas
- `app/main.py` - Registered topics router

## Support

For questions or issues with the Topics feature, refer to:
- API documentation in AGENTS.md
- Test examples in tests/test_topics.py
- This implementation guide
