"""Learning plan service — reusable daily learning on any topic.

Flow:
  1. User creates a plan (topic, days, schedule)
  2. LLM generates a full outline (day-by-day titles + summaries)
  3. Every morning (cron), the next lesson is generated via LLM and delivered
  4. Lesson is saved as a document and sent to the user's channel

This is a generic "scheduled content generation" pattern — reusable for:
  - Learning plans (LLMs, distributed systems, etc.)
  - Daily briefings (news, market updates)
  - Habit building (daily prompts, exercises)
  - Research deep-dives (one subtopic per day)
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LearningPlan, LearningLesson
from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

logger = logging.getLogger(__name__)

LESSONS_DIR = Path(os.environ.get("LEARNING_LESSONS_DIR", "data/learning_lessons"))


# ══════════════════════════════════════════════════════════════════════
# Plan Creation
# ══════════════════════════════════════════════════════════════════════

OUTLINE_PROMPT = """You are designing a {total_days}-day learning plan on: {topic}

Goal: {goal}

Create a structured day-by-day outline. Each day should build on the previous, 
starting from fundamentals and progressing to advanced concepts. The plan should 
help someone get meaningfully better at this topic over {total_days} days.

Return ONLY a JSON array of objects:
[
  {{"day": 1, "title": "Introduction to ...", "summary": "Brief 1-2 sentence overview of what this lesson covers"}},
  {{"day": 2, "title": "...", "summary": "..."}},
  ...
]

No prose before or after the JSON. Just the array.
"""


async def create_plan(
    db: AsyncSession,
    topic: str,
    goal: str = "Get 1% better every day",
    total_days: int = 30,
    schedule_cron: str = "0 7 * * *",
    schedule_tz: str = "America/New_York",
    delivery_channel: str = "discord",
) -> dict[str, Any]:
    """Create a new learning plan and generate the outline via LLM."""
    plan_id = str(uuid.uuid4())

    # Generate outline via LLM
    prompt = OUTLINE_PROMPT.format(topic=topic, goal=goal, total_days=total_days)
    outline_text = await _llm_generate(prompt, model="haiku")

    if not outline_text:
        return {"status": "error", "error": "Failed to generate plan outline"}

    # Parse the JSON outline
    outline = _parse_json_array(outline_text)
    if not outline:
        return {"status": "error", "error": "Failed to parse plan outline", "raw": outline_text[:500]}

    # Ensure we have the right number of days
    if len(outline) < total_days:
        logger.warning("[LEARNING] Outline has %d days, expected %d", len(outline), total_days)

    plan = LearningPlan(
        id=plan_id,
        topic=topic,
        goal=goal,
        total_days=total_days,
        current_day=0,
        status="active",
        schedule_cron=schedule_cron,
        schedule_tz=schedule_tz,
        delivery_channel=delivery_channel,
        plan_outline=outline,
    )
    db.add(plan)
    await db.commit()

    logger.info("[LEARNING] Created plan '%s' (%d days): %s", topic, total_days, plan_id[:8])
    return {
        "status": "ok",
        "plan_id": plan_id,
        "topic": topic,
        "total_days": total_days,
        "outline_days": len(outline),
        "outline": outline,
    }


# ══════════════════════════════════════════════════════════════════════
# Lesson Generation & Delivery
# ══════════════════════════════════════════════════════════════════════

LESSON_PROMPT = """You are writing Day {day} of a {total_days}-day learning plan on: {topic}

Today's lesson: **{title}**
Summary: {summary}

Context from the plan:
- Goal: {goal}
- Previous lessons covered: {previous_topics}

Write a clear, engaging lesson document (~500-800 words) that:
1. Starts with a brief recap/connection to previous material
2. Explains the core concept(s) for today
3. Includes a practical example or analogy
4. Ends with a key takeaway and a small exercise/reflection prompt

Format as clean markdown. Make it something someone could read in 5-10 minutes over coffee.
"""


async def generate_next_lesson(db: AsyncSession, plan_id: str) -> dict[str, Any]:
    """Generate and save the next lesson for a plan."""
    plan = await db.get(LearningPlan, plan_id)
    if not plan:
        return {"status": "error", "error": "Plan not found"}
    if plan.status != "active":
        return {"status": "skipped", "reason": f"Plan is {plan.status}"}

    next_day = plan.current_day + 1
    if next_day > plan.total_days:
        plan.status = "completed"
        await db.commit()
        return {"status": "completed", "message": f"Plan '{plan.topic}' finished all {plan.total_days} days!"}

    outline = plan.plan_outline or []
    day_info = next((d for d in outline if d.get("day") == next_day), None)
    if not day_info:
        day_info = {"day": next_day, "title": f"Day {next_day}", "summary": f"Continued exploration of {plan.topic}"}

    # Get previous lesson titles for context
    result = await db.execute(
        select(LearningLesson).where(
            LearningLesson.plan_id == plan_id
        ).order_by(LearningLesson.day_number.desc()).limit(5)
    )
    previous = result.scalars().all()
    prev_topics = ", ".join(f"Day {l.day_number}: {l.title}" for l in reversed(previous)) or "None (this is the first lesson)"

    # Generate lesson content via LLM
    prompt = LESSON_PROMPT.format(
        day=next_day,
        total_days=plan.total_days,
        topic=plan.topic,
        title=day_info["title"],
        summary=day_info.get("summary", ""),
        goal=plan.goal or "",
        previous_topics=prev_topics,
    )

    content = await _llm_generate(prompt, model="sonnet")
    if not content:
        return {"status": "error", "error": "Failed to generate lesson content"}

    # Save lesson document to file
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    safe_topic = plan.topic.lower().replace(" ", "-")[:30]
    doc_path = LESSONS_DIR / f"{safe_topic}" / f"day-{next_day:02d}.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    doc_content = f"# Day {next_day}: {day_info['title']}\n\n"
    doc_content += f"*{plan.topic} — {plan.goal}*\n\n"
    doc_content += f"---\n\n{content}"
    doc_path.write_text(doc_content, encoding="utf-8")

    # Save to DB
    lesson = LearningLesson(
        id=str(uuid.uuid4()),
        plan_id=plan_id,
        day_number=next_day,
        title=day_info["title"],
        content=content,
        summary=day_info.get("summary", ""),
        document_path=str(doc_path),
    )
    db.add(lesson)

    plan.current_day = next_day
    plan.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("[LEARNING] Generated lesson Day %d/%d for '%s'", next_day, plan.total_days, plan.topic)
    return {
        "status": "ok",
        "plan_id": plan_id,
        "topic": plan.topic,
        "day": next_day,
        "total_days": plan.total_days,
        "title": day_info["title"],
        "content": content,
        "document_path": str(doc_path),
    }


async def deliver_lesson(db: AsyncSession, plan_id: str, lesson_id: str) -> dict[str, Any]:
    """Mark a lesson as delivered (actual delivery handled by notify node)."""
    lesson = await db.get(LearningLesson, lesson_id)
    if lesson:
        lesson.delivered_at = datetime.now(timezone.utc)
        await db.commit()
    return {"delivered": True, "lesson_id": lesson_id}


# ══════════════════════════════════════════════════════════════════════
# Workflow Callables
# ══════════════════════════════════════════════════════════════════════

async def check_due_lessons(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Check all active plans and generate+deliver lessons that are due.
    
    Called by the daily-learning workflow on schedule.
    """
    result = await db.execute(
        select(LearningPlan).where(LearningPlan.status == "active")
    )
    plans = result.scalars().all()

    if not plans:
        return {"active_plans": 0, "lessons_generated": 0}

    generated = []
    for plan in plans:
        lesson_result = await generate_next_lesson(db, plan.id)
        if lesson_result.get("status") == "ok":
            generated.append({
                "plan_id": plan.id,
                "topic": plan.topic,
                "day": lesson_result["day"],
                "title": lesson_result["title"],
                "content_preview": lesson_result["content"][:200],
                "document_path": lesson_result.get("document_path"),
            })

    return {
        "active_plans": len(plans),
        "lessons_generated": len(generated),
        "lessons": generated,
    }


async def create_plan_from_request(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Create a plan from workflow trigger context or kwargs."""
    topic = kw.get("topic") or (context or {}).get("topic", "")
    goal = kw.get("goal") or (context or {}).get("goal", "Get 1% better every day")
    days = int(kw.get("total_days") or (context or {}).get("total_days", 30))

    if not topic:
        return {"status": "error", "error": "No topic provided"}

    return await create_plan(db, topic=topic, goal=goal, total_days=days)


# ══════════════════════════════════════════════════════════════════════
# LLM Helper
# ══════════════════════════════════════════════════════════════════════

async def _llm_generate(prompt: str, model: str = "sonnet") -> str | None:
    """Generate text via Gateway sessions_spawn (one-shot)."""
    if not GATEWAY_URL or not GATEWAY_TOKEN:
        return None

    try:
        parent_key = f"{GATEWAY_SESSION_KEY}-learning-{uuid.uuid4().hex[:6]}"
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_spawn",
                    "sessionKey": parent_key,
                    "args": {
                        "task": prompt,
                        "model": model,
                        "runTimeoutSeconds": 120,
                        "timeoutSeconds": 30,
                        "cleanup": "keep",
                    },
                },
                timeout=aiohttp.ClientTimeout(total=60),
            )
            data = await resp.json()

        if not data.get("ok"):
            logger.warning("[LEARNING] Spawn failed: %s", data)
            return None

        child_key = data.get("result", {}).get("details", {}).get("childSessionKey")
        if not child_key:
            return None

        # Poll for response
        import asyncio
        for _ in range(15):
            await asyncio.sleep(3)
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-learning-hist-{uuid.uuid4().hex[:6]}",
                        "args": {"sessionKey": child_key, "limit": 5, "includeTools": False},
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                hist_data = await resp.json()

            if hist_data.get("ok"):
                messages = hist_data.get("result", {}).get("details", {}).get("messages", [])
                for msg in reversed(messages):
                    if msg.get("role") == "assistant":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = "\n".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        if content and len(content) > 20:
                            return content.strip()

        logger.warning("[LEARNING] No LLM response after polling")
        return None

    except Exception as e:
        logger.error("[LEARNING] LLM generation failed: %s", e, exc_info=True)
        return None


def _parse_json_array(text: str) -> list[dict] | None:
    """Extract a JSON array from LLM output."""
    if not text:
        return None
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return None
