"""Task router - pure logic, no I/O dependencies."""

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

VALID_AGENTS = {"programmer", "researcher", "reviewer", "writer", "architect"}


@dataclass(frozen=True, slots=True)
class _Rule:
    agent_type: str
    pattern: re.Pattern[str]


def _compile_keywords(words: Iterable[str]) -> re.Pattern[str]:
    escaped = [re.escape(w) for w in words]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


# Default routing rules - route to specialized agents when signal is clear
_DEFAULT_RULES: tuple[_Rule, ...] = (
    _Rule(
        agent_type="researcher",
        pattern=_compile_keywords([
            "research", "investigate", "explore", "compare alternatives",
            "ideas", "analysis", "analyze", "evaluate", "study",
            "proof of concept", "proof-of-concept", "feasibility",
            "batch", "suggestions", "propose", "proposal",
        ]),
    ),
    _Rule(
        agent_type="writer",
        pattern=re.compile(
            r"\b(?:write\s+(?:a\s+)?(?:doc|summary|report|guide|readme)|draft\s+(?:doc|summary|report|guide|readme)|write\s+up|documentation\s+for)\b",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        agent_type="architect",
        pattern=_compile_keywords([
            "design system", "architect", "rework architecture", "restructure",
            "design proposal", "system design", "framework",
        ]),
    ),
    _Rule(
        agent_type="reviewer",
        pattern=_compile_keywords([
            "code review", "audit code", "review PR",
            "failure analysis", "hygiene", "cleanup",
        ]),
    ),
)


class Router:
    """Task router using regex patterns."""

    def __init__(
        self,
        rules: Optional[Iterable[tuple[str, re.Pattern[str]]]] = None,
        default_agent_type: str = "programmer",
    ):
        self.default_agent_type = default_agent_type

        if rules is None:
            self._rules: tuple[_Rule, ...] = _DEFAULT_RULES
        else:
            self._rules = tuple(_Rule(agent_type=a, pattern=p) for a, p in rules)

    def route(self, task: dict[str, Any]) -> str:
        """Return an agent type for the given task.

        Priority:
        1. Explicit `agent` field on the task
        2. Regex keyword matching
        3. Default (programmer)
        """
        # 1. Explicit agent field
        explicit = (task.get("agent") or "").strip()
        if explicit and explicit in VALID_AGENTS:
            logger.info(f"[ROUTER] Explicit agent field: {explicit}")
            return explicit

        kind = task.get("kind", "task")
        task_text = self._task_text(task)

        # 2. Regex matching
        for rule in self._rules:
            if rule.pattern.search(task_text):
                logger.info(f"[ROUTER] Regex matched '{rule.agent_type}' for {kind}")
                return rule.agent_type

        # 3. Default
        logger.info(f"[ROUTER] No match for {kind}, defaulting to {self.default_agent_type}")
        return self.default_agent_type

    @staticmethod
    def _task_text(task: dict[str, Any]) -> str:
        """Build searchable text from all task fields."""
        title = task.get("title") or ""
        notes = task.get("notes") or ""
        parts = [p for p in [title, notes] if p]
        return "\n".join(parts).strip()
