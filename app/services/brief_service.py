"""Daily Ops Brief service — aggregates calendar, email, GitHub, and agent tasks.

BriefService collects data from all configured integrations and formats it
into a concise markdown card for the assistant chat thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task as TaskModel, ScheduledEvent

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ── Normalized schema ──────────────────────────────────────────────────────────


@dataclass
class BriefItem:
    source: str  # "calendar" | "email" | "github" | "tasks"
    title: str
    detail: Optional[str] = None
    priority: str = "normal"  # "high" | "normal" | "low"
    url: Optional[str] = None
    time: Optional[datetime] = None


@dataclass
class BriefSection:
    name: str  # "Calendar", "Priority Messages", "GitHub Blockers", "Agent Tasks"
    icon: str  # emoji
    items: list[BriefItem] = field(default_factory=list)
    error: Optional[str] = None
    available: bool = True


@dataclass
class DailyBrief:
    generated_at: datetime
    sections: list[BriefSection] = field(default_factory=list)
    suggested_plan: str = ""


# ── Source adapters ────────────────────────────────────────────────────────────


class CalendarAdapter:
    """Fetches today's calendar events via GoogleCalendarService, falls back to ScheduledEvent table."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch(self) -> BriefSection:
        section = BriefSection(name="Calendar", icon="📅")
        try:
            items = await self._fetch_items()
            section.items = items
        except Exception as exc:
            logger.warning("[BRIEF] CalendarAdapter error: %s", exc)
            section.error = str(exc)
            section.available = False
        return section

    async def _fetch_items(self) -> list[BriefItem]:
        now_et = datetime.now(ET)
        today_start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        # Try Google Calendar first
        try:
            from app.services.google_calendar import GoogleCalendarService
            svc = GoogleCalendarService(self.db)
            events = await svc.get_rafe_schedule(days=1)
            if events:
                items: list[BriefItem] = []
                for ev in events:
                    start_raw = ev.get("start_time") or ev.get("start", {})
                    # Handle both datetime objects and dicts from the API
                    if isinstance(start_raw, datetime):
                        event_start = start_raw
                    elif isinstance(start_raw, dict):
                        dt_str = start_raw.get("dateTime") or start_raw.get("date")
                        if dt_str:
                            try:
                                event_start = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                            except ValueError:
                                event_start = None
                        else:
                            event_start = None
                    else:
                        event_start = None

                    end_raw = ev.get("end_time") or ev.get("end", {})
                    if isinstance(end_raw, datetime):
                        event_end = end_raw
                    elif isinstance(end_raw, dict):
                        dt_str2 = end_raw.get("dateTime") or end_raw.get("date")
                        if dt_str2:
                            try:
                                event_end = datetime.fromisoformat(dt_str2.replace("Z", "+00:00"))
                            except ValueError:
                                event_end = None
                        else:
                            event_end = None
                    else:
                        event_end = None

                    title = ev.get("summary") or ev.get("title") or "Untitled event"
                    detail = None
                    if event_start and event_end:
                        start_str = event_start.astimezone(ET).strftime("%H:%M")
                        end_str = event_end.astimezone(ET).strftime("%H:%M")
                        detail = f"{start_str}–{end_str}"
                    items.append(BriefItem(
                        source="calendar",
                        title=title,
                        detail=detail,
                        time=event_start,
                    ))
                # Sort by time
                items.sort(key=lambda i: i.time or datetime.max.replace(tzinfo=timezone.utc))
                return items
        except Exception as gcal_err:
            logger.debug("[BRIEF] Google Calendar unavailable, falling back to ScheduledEvent table: %s", gcal_err)

        # Fallback: internal ScheduledEvent table
        result = await self.db.execute(
            select(ScheduledEvent).where(
                ScheduledEvent.scheduled_at >= today_start.astimezone(timezone.utc),
                ScheduledEvent.scheduled_at < today_end.astimezone(timezone.utc),
                ScheduledEvent.status.in_(["pending", "recurring"]),
            ).order_by(ScheduledEvent.scheduled_at)
        )
        events_db = result.scalars().all()
        items_db: list[BriefItem] = []
        for ev in events_db:
            start_et = ev.scheduled_at.replace(tzinfo=timezone.utc).astimezone(ET) if ev.scheduled_at else None
            end_et = ev.end_at.replace(tzinfo=timezone.utc).astimezone(ET) if ev.end_at else None
            detail = None
            if start_et and end_et:
                detail = f"{start_et.strftime('%H:%M')}–{end_et.strftime('%H:%M')}"
            elif start_et:
                detail = start_et.strftime("%H:%M")
            items_db.append(BriefItem(
                source="calendar",
                title=ev.title,
                detail=detail,
                time=ev.scheduled_at,
            ))
        return items_db


class EmailAdapter:
    """Fetches priority unread emails via EmailService."""

    PRIORITY_KEYWORDS = {"urgent", "asap", "blocker", "critical", "action required", "important"}

    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch(self) -> BriefSection:
        section = BriefSection(name="Priority Messages", icon="📨")
        try:
            from app.services.email_service import EmailService
            svc = EmailService(self.db)
            emails = await svc.get_unread(max_results=20)
            section.items = self._filter_priority(emails)
        except ImportError:
            section.error = "Email service not available"
            section.available = False
        except Exception as exc:
            logger.warning("[BRIEF] EmailAdapter error: %s", exc)
            section.error = str(exc)
            section.available = False
        return section

    def _filter_priority(self, emails: list[dict]) -> list[BriefItem]:
        items: list[BriefItem] = []
        for em in emails:
            subject = (em.get("subject") or "").lower()
            sender = em.get("sender") or em.get("from") or ""
            date_raw = em.get("date") or em.get("received_at")
            if isinstance(date_raw, str):
                try:
                    received = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
                except ValueError:
                    received = None
            elif isinstance(date_raw, datetime):
                received = date_raw
            else:
                received = None

            # Time-ago detail
            detail = None
            if received:
                ago = datetime.now(timezone.utc) - received.replace(tzinfo=timezone.utc) if received.tzinfo is None else datetime.now(timezone.utc) - received
                hours = int(ago.total_seconds() / 3600)
                if hours < 1:
                    detail = f"{int(ago.total_seconds() / 60)}m ago"
                else:
                    detail = f"{hours}h ago"

            is_priority = any(kw in subject for kw in self.PRIORITY_KEYWORDS)
            items.append(BriefItem(
                source="email",
                title=f"{sender} — {em.get('subject') or '(no subject)'}",
                detail=detail,
                priority="high" if is_priority else "normal",
                time=received,
            ))

        # Sort by priority then recency, take top 5
        items.sort(key=lambda i: (0 if i.priority == "high" else 1, -(i.time.timestamp() if i.time else 0)))
        return items[:5]


class GitHubAdapter:
    """Fetches open blocker issues and PRs awaiting review via `gh` CLI."""

    def __init__(self):
        self.blocker_label = os.getenv("GITHUB_BLOCKER_LABEL", "blocker")

    async def fetch(self) -> BriefSection:
        section = BriefSection(name="GitHub Blockers", icon="🚧")
        try:
            items = await asyncio.to_thread(self._fetch_sync)
            section.items = items
        except Exception as exc:
            logger.warning("[BRIEF] GitHubAdapter error: %s", exc)
            section.error = str(exc)
            section.available = False
        return section

    def _fetch_sync(self) -> list[BriefItem]:
        items: list[BriefItem] = []

        # Issues with blocker label
        try:
            result = subprocess.run(
                [
                    "gh", "issue", "list",
                    "--state", "open",
                    "--label", self.blocker_label,
                    "--json", "number,title,url,updatedAt",
                    "--limit", "10",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                issues = json.loads(result.stdout)
                for issue in issues:
                    updated_raw = issue.get("updatedAt")
                    updated = None
                    if updated_raw:
                        try:
                            updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    items.append(BriefItem(
                        source="github",
                        title=f"#{issue.get('number')} — {issue.get('title')}",
                        priority="high",
                        url=issue.get("url"),
                        time=updated,
                    ))
        except FileNotFoundError:
            raise RuntimeError("gh CLI not installed")
        except subprocess.TimeoutExpired:
            raise RuntimeError("gh CLI timed out")

        # PRs needing my review
        try:
            pr_result = subprocess.run(
                [
                    "gh", "pr", "list",
                    "--state", "open",
                    "--review-requested", "@me",
                    "--json", "number,title,url,updatedAt",
                    "--limit", "5",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if pr_result.returncode == 0 and pr_result.stdout.strip():
                prs = json.loads(pr_result.stdout)
                for pr in prs:
                    updated_raw = pr.get("updatedAt")
                    updated = None
                    if updated_raw:
                        try:
                            updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    items.append(BriefItem(
                        source="github",
                        title=f"PR #{pr.get('number')} — {pr.get('title')} (review requested)",
                        priority="normal",
                        url=pr.get("url"),
                        time=updated,
                    ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # Best-effort

        # Sort by updatedAt descending
        items.sort(key=lambda i: i.time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items


class TasksAdapter:
    """Fetches top agent tasks from the database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch(self) -> BriefSection:
        section = BriefSection(name="Agent Tasks", icon="🤖")
        try:
            result = await self.db.execute(
                select(TaskModel)
                .where(TaskModel.status.in_(["inbox", "active"]))
                .order_by(TaskModel.sort_order.asc(), TaskModel.created_at.asc())
                .limit(5)
            )
            tasks = result.scalars().all()
            items: list[BriefItem] = []
            for task in tasks:
                agent_info = f" [{task.agent}]" if task.agent else ""
                items.append(BriefItem(
                    source="tasks",
                    title=f"{task.title}{agent_info}",
                    detail=task.work_state,
                    priority="normal",
                ))
            section.items = items
        except Exception as exc:
            logger.warning("[BRIEF] TasksAdapter error: %s", exc)
            section.error = str(exc)
            section.available = False
        return section


# ── BriefService ───────────────────────────────────────────────────────────────


class BriefService:
    """Aggregates all sources and returns a DailyBrief."""

    ADAPTER_TIMEOUT = 10.0  # seconds per adapter

    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate(self) -> DailyBrief:
        """Fetch all sections concurrently with per-adapter timeout. Always returns a DailyBrief."""
        generated_at = datetime.now(timezone.utc)

        adapters = [
            CalendarAdapter(self.db).fetch(),
            EmailAdapter(self.db).fetch(),
            GitHubAdapter().fetch(),
            TasksAdapter(self.db).fetch(),
        ]

        # Run concurrently with timeout
        async def _with_timeout(coro, name: str) -> BriefSection:
            icon_map = {"calendar": "📅", "email": "📨", "github": "🚧", "tasks": "🤖"}
            try:
                return await asyncio.wait_for(coro, timeout=self.ADAPTER_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[BRIEF] Adapter %s timed out after %.0fs", name, self.ADAPTER_TIMEOUT)
                return BriefSection(
                    name=name.title(),
                    icon=icon_map.get(name, "❓"),
                    error=f"Timed out after {self.ADAPTER_TIMEOUT:.0f}s",
                    available=False,
                )
            except Exception as exc:
                logger.warning("[BRIEF] Adapter %s failed: %s", name, exc)
                return BriefSection(
                    name=name.title(),
                    icon=icon_map.get(name, "❓"),
                    error=str(exc),
                    available=False,
                )

        sections_raw = await asyncio.gather(
            _with_timeout(adapters[0], "calendar"),
            _with_timeout(adapters[1], "email"),
            _with_timeout(adapters[2], "github"),
            _with_timeout(adapters[3], "tasks"),
            return_exceptions=False,
        )

        sections = list(sections_raw)

        brief = DailyBrief(
            generated_at=generated_at,
            sections=sections,
        )
        brief.suggested_plan = BriefFormatter.suggest_plan(brief)
        return brief


# ── BriefFormatter ─────────────────────────────────────────────────────────────


class BriefFormatter:
    """Renders a DailyBrief as markdown."""

    @staticmethod
    def to_markdown(brief: DailyBrief) -> str:
        """Convert a DailyBrief to a concise markdown card."""
        now_et = brief.generated_at.astimezone(ET)
        date_str = now_et.strftime("%A %b %-d")

        lines: list[str] = [
            f"## 🗓 Daily Ops Brief — {date_str}",
            "",
        ]

        for section in brief.sections:
            lines.append(f"### {section.icon} {section.name}")
            if not section.available or section.error:
                lines.append(f"*(not configured — {section.error or 'unavailable'})*" if section.error else "*(not configured)*")
            elif not section.items:
                lines.append("*Nothing scheduled.*")
            else:
                for item in section.items:
                    bullet = "- "
                    if item.detail:
                        bullet += f"**{item.detail}** — {item.title}"
                    else:
                        bullet += item.title
                    if item.url:
                        bullet += f" [↗]({item.url})"
                    lines.append(bullet)
            lines.append("")

        # Suggested plan
        if brief.suggested_plan:
            lines.append(f"**Suggested plan:** {brief.suggested_plan}")

        return "\n".join(lines)

    @staticmethod
    def suggest_plan(brief: DailyBrief) -> str:
        """Generate a 1–3 sentence suggested plan based on the brief contents."""
        suggestions: list[str] = []

        # Collect high-priority items from all sections
        blockers: list[str] = []
        meetings: list[str] = []
        high_tasks: list[str] = []

        for section in brief.sections:
            for item in section.items:
                if section.name == "GitHub Blockers":
                    blockers.append(item.title)
                elif section.name == "Calendar":
                    meetings.append(item.title)
                elif item.priority == "high":
                    high_tasks.append(item.title)

        if blockers:
            first_blocker = blockers[0].split(" — ", 1)[-1] if " — " in blockers[0] else blockers[0]
            suggestions.append(f"Start by clearing {first_blocker[:60]} (blocking other work).")

        if high_tasks:
            first_task = high_tasks[0].split(" [")[0]
            suggestions.append(f"Prioritize: {first_task[:60]}.")

        if meetings:
            first_meeting = meetings[0]
            suggestions.append(f"Review agenda for {first_meeting[:60]} before the meeting.")

        if not suggestions:
            # Look for active tasks
            for section in brief.sections:
                if section.name == "Agent Tasks" and section.items:
                    suggestions.append(f"Focus on: {section.items[0].title[:60]}.")
                    break

        if not suggestions:
            suggestions.append("No urgent items — a good day for deep work.")

        return " ".join(suggestions[:3])
