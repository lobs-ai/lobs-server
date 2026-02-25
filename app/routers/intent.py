"""Intent router — task routing recommendations + fast capture classifier."""

import re
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import InboxItem as InboxItemModel, Task as TaskModel
from app.schemas import InboxItem, Task as TaskSchema

router = APIRouter(prefix="/intent", tags=["intent"])


# ---------------------------------------------------------------------------
# Legacy route endpoint (unchanged)
# ---------------------------------------------------------------------------

class IntentRequest(BaseModel):
    text: str


class IntentResponse(BaseModel):
    intent: str
    recommended_agent: str
    confidence: float


@router.post("/route")
async def route_intent(payload: IntentRequest) -> IntentResponse:
    text = payload.text.lower()
    if any(k in text for k in ["research", "investigate", "compare", "analyze"]):
        return IntentResponse(intent="research", recommended_agent="researcher", confidence=0.85)
    if any(k in text for k in ["write", "draft", "edit content", "copy"]):
        return IntentResponse(intent="writing", recommended_agent="writer", confidence=0.8)
    if any(k in text for k in ["review", "qa", "test", "validate"]):
        return IntentResponse(intent="review", recommended_agent="reviewer", confidence=0.78)
    if any(k in text for k in ["design", "architecture", "refactor plan"]):
        return IntentResponse(intent="architecture", recommended_agent="architect", confidence=0.76)
    return IntentResponse(intent="implementation", recommended_agent="programmer", confidence=0.7)


# ---------------------------------------------------------------------------
# Capture classifier schemas
# ---------------------------------------------------------------------------

class CaptureIntent(BaseModel):
    intent_type: str          # task | reminder | research | reply_needed
    confidence: float
    suggested_title: str
    proposed_action: str
    agent: Optional[str] = None  # for task intents
    tags: list[str] = []


class CaptureRequest(BaseModel):
    text: str


class CaptureResponse(BaseModel):
    intents: list[CaptureIntent]   # sorted by confidence desc
    raw_text: str
    detected_url: Optional[str] = None
    word_count: int


class CaptureConfirmRequest(BaseModel):
    text: str
    intent_type: str                  # chosen intent_type
    suggested_title: Optional[str] = None
    agent: Optional[str] = None
    project_id: Optional[str] = None


class CaptureConfirmResponse(BaseModel):
    entity_type: str   # task | inbox_item
    entity_id: str
    title: str
    nav_path: str      # e.g. "tasks" or "inbox"
    message: str


# ---------------------------------------------------------------------------
# Keyword scoring tables
# ---------------------------------------------------------------------------

# Each entry: (pattern_list, score_delta)
_TASK_KEYWORDS = [
    (["implement", "fix", "build", "add", "create", "develop", "make", "write code",
      "refactor", "deploy", "migrate", "integrate", "set up", "configure", "update",
      "change", "remove", "delete", "rename", "move", "convert", "port", "upgrade",
      "debug", "troubleshoot", "resolve", "close issue", "open pr", "submit"],
     0.90),
]

_REMINDER_KEYWORDS = [
    (["remind", "remember", "don't forget", "do not forget", "follow up",
      "follow-up", "deadline", "due", "by tomorrow", "next week", "by monday",
      "by friday", "by end of", "before", "schedule", "book", "book a", "ping",
      "check back", "revisit"],
     0.88),
]

_RESEARCH_KEYWORDS = [
    (["research", "investigate", "compare", "analyze", "analyse", "look into",
      "find out", "what is", "how does", "which is better", "explore", "survey",
      "read about", "learn about", "understand", "why is", "is it possible",
      "pros and cons", "evaluate", "assessment", "benchmark", "profile", "audit"],
     0.87),
]

_REPLY_KEYWORDS = [
    (["reply", "respond", "response", "get back to", "email", "message", "reach out",
      "let them know", "let him know", "let her know", "need to tell", "send a message",
      "send an email", "follow up with", "reply to", "answer", "get in touch",
      "contact", "notify", "dm", "slack", "text"],
     0.85),
]

# URL pattern
_URL_RE = re.compile(r"https?://[^\s]+")

# Interrogative sentence endings (bump research)
_QUESTION_RE = re.compile(r"\?")

# Temporal markers (bump reminder)
_TEMPORAL_RE = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|next week|next month|by \w+|in \d+ days?|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
    re.IGNORECASE,
)


def _agent_for_task(text: str) -> str:
    """Suggest an agent for a task based on keyword signals."""
    t = text.lower()
    if any(k in t for k in ["research", "investigate", "compare", "analyze", "analyse", "look into"]):
        return "researcher"
    if any(k in t for k in ["write", "draft", "document", "blog", "post", "copy", "content"]):
        return "writer"
    if any(k in t for k in ["review", "qa", "test", "validate", "audit"]):
        return "reviewer"
    if any(k in t for k in ["design", "architecture", "refactor plan", "system design"]):
        return "architect"
    return "programmer"


def _extract_url(text: str) -> Optional[str]:
    m = _URL_RE.search(text)
    return m.group(0) if m else None


def _summarize_title(text: str, max_len: int = 80) -> str:
    """Extract a short title from raw text (first non-empty line, truncated)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "Untitled"
    first = lines[0]
    # Strip leading # (markdown) or bullet markers
    first = re.sub(r"^[#\-\*>]+\s*", "", first)
    return first[:max_len] + ("…" if len(first) > max_len else "")


def _score_intents(text: str) -> list[CaptureIntent]:
    """Classify *text* into a ranked list of CaptureIntents (deterministic, no LLM)."""
    low = text.lower()
    url = _extract_url(text)
    has_question = bool(_QUESTION_RE.search(text))
    has_temporal = bool(_TEMPORAL_RE.search(text))
    word_count = len(text.split())

    scores: dict[str, float] = {
        "task": 0.55,
        "reminder": 0.40,
        "research": 0.40,
        "reply_needed": 0.38,
    }

    # Apply keyword tables
    for kw_list, delta in _TASK_KEYWORDS:
        if any(k in low for k in kw_list):
            scores["task"] = max(scores["task"], delta)

    for kw_list, delta in _REMINDER_KEYWORDS:
        if any(k in low for k in kw_list):
            scores["reminder"] = max(scores["reminder"], delta)

    for kw_list, delta in _RESEARCH_KEYWORDS:
        if any(k in low for k in kw_list):
            scores["research"] = max(scores["research"], delta)

    for kw_list, delta in _REPLY_KEYWORDS:
        if any(k in low for k in kw_list):
            scores["reply_needed"] = max(scores["reply_needed"], delta)

    # Heuristic boosts
    if has_question:
        scores["research"] = min(scores["research"] + 0.12, 1.0)
        scores["reply_needed"] = min(scores["reply_needed"] + 0.05, 1.0)

    if has_temporal:
        scores["reminder"] = min(scores["reminder"] + 0.15, 1.0)

    if url:
        # A bare URL is likely research material or a reply target
        scores["research"] = min(scores["research"] + 0.10, 1.0)
        scores["reply_needed"] = min(scores["reply_needed"] + 0.08, 1.0)

    if word_count <= 10 and scores["task"] < 0.70:
        # Very short text without explicit task verbs → probably a quick reminder
        scores["reminder"] = min(scores["reminder"] + 0.10, 1.0)

    # Build title
    title = _summarize_title(text)

    # Build proposed action strings
    agent = _agent_for_task(text)
    actions: dict[str, str] = {
        "task": f'Create a task assigned to {agent}: "{title}"',
        "reminder": f'Save as reminder: "{title}"',
        "research": f'Queue a research request: "{title}"',
        "reply_needed": f'Flag as reply-needed in inbox: "{title}"',
    }

    # Tags (simple heuristic)
    tags: list[str] = []
    if url:
        tags.append("url")
    if has_temporal:
        tags.append("time-sensitive")
    if has_question:
        tags.append("question")

    results: list[CaptureIntent] = []
    for intent_type, score in sorted(scores.items(), key=lambda x: -x[1]):
        results.append(
            CaptureIntent(
                intent_type=intent_type,
                confidence=round(score, 2),
                suggested_title=title,
                proposed_action=actions[intent_type],
                agent=agent if intent_type == "task" else None,
                tags=list(tags),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Capture endpoints
# ---------------------------------------------------------------------------

@router.post("/capture")
async def classify_capture(payload: CaptureRequest) -> CaptureResponse:
    """Classify raw text into ranked intents (task/reminder/research/reply_needed)."""
    text = payload.text.strip()
    intents = _score_intents(text) if text else [
        CaptureIntent(
            intent_type="task",
            confidence=0.55,
            suggested_title="Untitled",
            proposed_action="Create a task",
            agent="programmer",
            tags=[],
        )
    ]
    return CaptureResponse(
        intents=intents,
        raw_text=text,
        detected_url=_extract_url(text),
        word_count=len(text.split()) if text else 0,
    )


@router.post("/capture/confirm")
async def confirm_capture(
    payload: CaptureConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> CaptureConfirmResponse:
    """Confirm a capture intent and create the appropriate entity."""
    title = (payload.suggested_title or _summarize_title(payload.text) or "Captured item").strip()
    intent = payload.intent_type
    entity_id = str(uuid4())

    if intent == "task":
        agent = payload.agent or _agent_for_task(payload.text)
        db_task = TaskModel(
            id=entity_id,
            title=title,
            status="inbox",
            owner="lobs",
            agent=agent,
            project_id=payload.project_id or None,
            notes=payload.text if payload.text != title else None,
            work_state="not_started",
        )
        db.add(db_task)
        await db.flush()
        return CaptureConfirmResponse(
            entity_type="task",
            entity_id=entity_id,
            title=title,
            nav_path="tasks",
            message=f'Task created and assigned to {agent}: "{title}"',
        )

    # All other intent types → InboxItem with metadata in content/summary
    triage_label = {
        "reminder": "reminder",
        "research": "needs_response",
        "reply_needed": "needs_response",
    }.get(intent, "needs_response")

    content_prefix = {
        "reminder": "[Reminder] ",
        "research": "[Research] ",
        "reply_needed": "[Reply needed] ",
    }.get(intent, "")

    db_item = InboxItemModel(
        id=entity_id,
        title=title,
        content=payload.text,
        summary=f"{content_prefix}{title}",
        is_read=False,
    )
    db.add(db_item)
    await db.flush()

    action_labels = {
        "reminder": "Reminder saved",
        "research": "Research request queued",
        "reply_needed": "Flagged as reply-needed",
    }
    action_msg = action_labels.get(intent, "Item saved")

    return CaptureConfirmResponse(
        entity_type="inbox_item",
        entity_id=entity_id,
        title=title,
        nav_path="inbox",
        message=f'{action_msg}: "{title}"',
    )
