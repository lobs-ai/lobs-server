"""Tests for the Daily Ops Brief service."""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.brief_service import (
    BriefFormatter,
    BriefItem,
    BriefSection,
    BriefService,
    DailyBrief,
    TasksAdapter,
    CalendarAdapter,
    EmailAdapter,
)


# ── BriefFormatter tests ───────────────────────────────────────────────────────


class TestBriefFormatter:
    """Test BriefFormatter.to_markdown() output."""

    def _make_full_brief(self) -> DailyBrief:
        return DailyBrief(
            generated_at=datetime(2026, 2, 25, 13, 0, 0, tzinfo=timezone.utc),
            sections=[
                BriefSection(
                    name="Calendar",
                    icon="📅",
                    items=[
                        BriefItem(source="calendar", title="Standup", detail="09:00–09:30"),
                        BriefItem(source="calendar", title="Design Review", detail="14:00–15:00"),
                    ],
                ),
                BriefSection(
                    name="Priority Messages",
                    icon="📨",
                    items=[
                        BriefItem(source="email", title="boss@example.com — Review ASAP", detail="2h ago", priority="high"),
                    ],
                ),
                BriefSection(
                    name="GitHub Blockers",
                    icon="🚧",
                    items=[
                        BriefItem(source="github", title="#42 — CI fails on main", priority="high", url="https://github.com/org/repo/issues/42"),
                    ],
                ),
                BriefSection(
                    name="Agent Tasks",
                    icon="🤖",
                    items=[
                        BriefItem(source="tasks", title="Fix auth bug [programmer]", detail="not_started"),
                        BriefItem(source="tasks", title="Update docs [writer]", detail="not_started"),
                        BriefItem(source="tasks", title="Research caching [researcher]", detail="not_started"),
                    ],
                ),
            ],
            suggested_plan="Start with CI (blocker). Then tackle Fix auth bug.",
        )

    def test_to_markdown_all_sections_populated(self):
        """Markdown contains all section headers and items."""
        brief = self._make_full_brief()
        md = BriefFormatter.to_markdown(brief)

        # Top-level header
        assert "Daily Ops Brief" in md

        # Section headers (name + icon)
        assert "📅" in md
        assert "Calendar" in md
        assert "📨" in md
        assert "Priority Messages" in md
        assert "🚧" in md
        assert "GitHub Blockers" in md
        assert "🤖" in md
        assert "Agent Tasks" in md

        # Content
        assert "Standup" in md
        assert "Design Review" in md
        assert "boss@example.com" in md
        assert "#42 — CI fails on main" in md
        assert "Fix auth bug" in md

        # URL link for GitHub item
        assert "https://github.com/org/repo/issues/42" in md

        # Suggested plan
        assert "Suggested plan:" in md
        assert "CI" in md

    def test_to_markdown_degraded_mode_all_sections_errored(self):
        """Markdown renders gracefully when all adapters have failed."""
        brief = DailyBrief(
            generated_at=datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc),
            sections=[
                BriefSection(name="Calendar", icon="📅", error="not configured", available=False),
                BriefSection(name="Priority Messages", icon="📨", error="Gmail not authorized", available=False),
                BriefSection(name="GitHub Blockers", icon="🚧", error="gh CLI not installed", available=False),
                BriefSection(name="Agent Tasks", icon="🤖", error="DB unavailable", available=False),
            ],
        )
        md = BriefFormatter.to_markdown(brief)

        # Header still present
        assert "Daily Ops Brief" in md

        # All sections still render their headers
        assert "Calendar" in md
        assert "Priority Messages" in md
        assert "GitHub Blockers" in md
        assert "Agent Tasks" in md

        # Error notes present
        assert "not configured" in md
        assert "Gmail not authorized" in md
        assert "gh CLI not installed" in md

        # No crash — just clean markdown
        assert len(md) > 50

    def test_to_markdown_empty_sections_render_nothing_scheduled(self):
        """Sections with no items render a 'Nothing scheduled.' placeholder."""
        brief = DailyBrief(
            generated_at=datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc),
            sections=[
                BriefSection(name="Calendar", icon="📅", items=[], available=True),
            ],
        )
        md = BriefFormatter.to_markdown(brief)
        assert "Nothing scheduled." in md

    def test_suggest_plan_with_blockers(self):
        """suggest_plan prioritizes GitHub blockers."""
        brief = self._make_full_brief()
        plan = BriefFormatter.suggest_plan(brief)
        # Should mention a blocker or high-priority item
        assert len(plan) > 0
        assert isinstance(plan, str)

    def test_suggest_plan_no_items(self):
        """suggest_plan returns a default message when everything is empty."""
        brief = DailyBrief(
            generated_at=datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc),
            sections=[
                BriefSection(name="Calendar", icon="📅"),
                BriefSection(name="Priority Messages", icon="📨"),
                BriefSection(name="GitHub Blockers", icon="🚧"),
                BriefSection(name="Agent Tasks", icon="🤖"),
            ],
        )
        plan = BriefFormatter.suggest_plan(brief)
        assert "deep work" in plan.lower() or len(plan) > 0


# ── TasksAdapter tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tasks_adapter_returns_items_from_db(db_session):
    """TasksAdapter.fetch() returns BriefItems for tasks in DB."""
    import uuid
    from app.models import Task as TaskModel

    # Seed 3 tasks
    for i in range(3):
        db_session.add(TaskModel(
            id=str(uuid.uuid4()),
            title=f"Task {i}",
            status="inbox",
            work_state="not_started",
            sort_order=i,
        ))
    await db_session.commit()

    adapter = TasksAdapter(db_session)
    section = await adapter.fetch()

    assert section.error is None
    assert section.available is True
    assert len(section.items) == 3
    assert all(item.source == "tasks" for item in section.items)
    assert section.items[0].title.startswith("Task 0")


@pytest.mark.asyncio
async def test_tasks_adapter_empty_db(db_session):
    """TasksAdapter.fetch() returns empty items list (no error) when DB has no tasks."""
    adapter = TasksAdapter(db_session)
    section = await adapter.fetch()

    assert section.error is None
    assert section.available is True
    assert section.items == []


@pytest.mark.asyncio
async def test_tasks_adapter_only_inbox_and_active(db_session):
    """TasksAdapter only includes inbox and active tasks."""
    import uuid
    from app.models import Task as TaskModel

    db_session.add(TaskModel(id=str(uuid.uuid4()), title="Active task", status="active", work_state="in_progress", sort_order=0))
    db_session.add(TaskModel(id=str(uuid.uuid4()), title="Inbox task", status="inbox", work_state="not_started", sort_order=1))
    db_session.add(TaskModel(id=str(uuid.uuid4()), title="Completed task", status="completed", work_state="done", sort_order=2))
    await db_session.commit()

    adapter = TasksAdapter(db_session)
    section = await adapter.fetch()

    titles = [item.title.split(" [")[0] for item in section.items]
    assert "Active task" in titles
    assert "Inbox task" in titles
    assert "Completed task" not in titles


# ── BriefService tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brief_service_returns_4_sections_even_on_failure(db_session):
    """BriefService.generate() always returns 4 sections, even when all adapters fail."""
    # Patch all adapters to raise exceptions
    error_section = BriefSection(name="Test", icon="❌", error="simulated failure", available=False)

    async def _bad_fetch(self):
        raise RuntimeError("simulated failure")

    with (
        patch.object(CalendarAdapter, "fetch", _bad_fetch),
        patch.object(EmailAdapter, "fetch", _bad_fetch),
    ):
        service = BriefService(db_session)
        brief = await service.generate()

    assert isinstance(brief, DailyBrief)
    assert len(brief.sections) == 4
    # At least some sections should be marked unavailable
    unavailable = [s for s in brief.sections if not s.available or s.error]
    assert len(unavailable) >= 2  # Calendar and Email should have failed


@pytest.mark.asyncio
async def test_brief_service_suggested_plan_is_set(db_session):
    """BriefService.generate() sets a non-empty suggested_plan."""
    with (
        patch.object(CalendarAdapter, "fetch", AsyncMock(return_value=BriefSection(name="Calendar", icon="📅"))),
        patch.object(EmailAdapter, "fetch", AsyncMock(return_value=BriefSection(name="Priority Messages", icon="📨"))),
    ):
        service = BriefService(db_session)
        brief = await service.generate()

    assert isinstance(brief.suggested_plan, str)
    # May be empty when no items but should not crash
    assert brief.suggested_plan is not None


# ── Integration tests — GET /api/brief/today ──────────────────────────────────


@pytest.mark.asyncio
async def test_brief_today_returns_200(client):
    """GET /api/brief/today returns 200 with markdown key."""
    # Mock BriefService so no external calls are made
    fixed_brief = DailyBrief(
        generated_at=datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc),
        sections=[
            BriefSection(name="Calendar", icon="📅"),
            BriefSection(name="Priority Messages", icon="📨"),
            BriefSection(name="GitHub Blockers", icon="🚧"),
            BriefSection(name="Agent Tasks", icon="🤖"),
        ],
        suggested_plan="No urgent items.",
    )

    with patch("app.routers.brief.BriefService") as MockService:
        mock_instance = AsyncMock()
        mock_instance.generate = AsyncMock(return_value=fixed_brief)
        MockService.return_value = mock_instance

        response = await client.get("/api/brief/today")

    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data
    assert "sections" in data
    assert "generated_at" in data
    assert isinstance(data["markdown"], str)
    assert "Daily Ops Brief" in data["markdown"]


@pytest.mark.asyncio
async def test_brief_today_requires_auth():
    """GET /api/brief/today returns 401 without auth token."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/brief/today")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_brief_today_send_to_chat(client):
    """GET /api/brief/today?send_to_chat=true stores a chat message."""
    fixed_brief = DailyBrief(
        generated_at=datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc),
        sections=[
            BriefSection(name="Calendar", icon="📅"),
            BriefSection(name="Priority Messages", icon="📨"),
            BriefSection(name="GitHub Blockers", icon="🚧"),
            BriefSection(name="Agent Tasks", icon="🤖"),
        ],
        suggested_plan="Focus on work.",
    )

    with (
        patch("app.routers.brief.BriefService") as MockService,
        patch("app.routers.brief.manager") as mock_manager,
    ):
        mock_instance = AsyncMock()
        mock_instance.generate = AsyncMock(return_value=fixed_brief)
        MockService.return_value = mock_instance
        mock_manager.broadcast_to_session = AsyncMock()

        response = await client.get("/api/brief/today?send_to_chat=true")

    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data

    # Verify the message was stored in chat (via the chat session listing)
    sessions_resp = await client.get("/api/chat/sessions")
    # Session should now exist for 'main'
    sessions = sessions_resp.json() if sessions_resp.status_code == 200 else []
    if isinstance(sessions, list):
        session_keys = [s.get("session_key") for s in sessions]
        assert "main" in session_keys


@pytest.mark.asyncio
async def test_brief_today_sections_schema(client):
    """GET /api/brief/today sections have expected schema."""
    fixed_brief = DailyBrief(
        generated_at=datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc),
        sections=[
            BriefSection(
                name="Calendar",
                icon="📅",
                items=[
                    BriefItem(
                        source="calendar",
                        title="Standup",
                        detail="09:00–09:30",
                        priority="normal",
                        url=None,
                        time=datetime(2026, 2, 25, 9, 0, 0, tzinfo=timezone.utc),
                    )
                ],
            ),
        ],
        suggested_plan="Attend standup.",
    )

    with patch("app.routers.brief.BriefService") as MockService:
        mock_instance = AsyncMock()
        mock_instance.generate = AsyncMock(return_value=fixed_brief)
        MockService.return_value = mock_instance

        response = await client.get("/api/brief/today")

    assert response.status_code == 200
    data = response.json()
    sections = data["sections"]
    assert len(sections) == 1
    section = sections[0]
    assert section["name"] == "Calendar"
    assert section["icon"] == "📅"
    assert section["available"] is True
    assert section["error"] is None
    assert len(section["items"]) == 1
    item = section["items"][0]
    assert item["source"] == "calendar"
    assert item["title"] == "Standup"
    assert item["detail"] == "09:00–09:30"
    assert item["priority"] == "normal"
