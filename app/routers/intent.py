"""Intent router v1 for lightweight task routing recommendations."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/intent", tags=["intent"])


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
