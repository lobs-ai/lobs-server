#!/usr/bin/env python3
"""Seed baseline model pricing entries for routing/cost estimation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lobs.db"

# Placeholder baseline values — update as providers change pricing.
# Rates are USD per 1M tokens.
PRICING = [
    ("openai", "openai-codex/gpt-5.3-codex", "api", 5.0, 15.0, 1.25, "baseline"),
    ("claude", "anthropic/claude-sonnet-4-5", "api", 3.0, 15.0, 0.3, "baseline"),
    ("claude", "anthropic/claude-opus-4-6", "api", 15.0, 75.0, 1.5, "baseline"),
    ("kimi", "moonshotai/kimi-k2.5", "api", 2.0, 8.0, 0.2, "baseline"),
    ("minimax", "minimax/minimax-2.5", "api", 1.5, 6.0, 0.15, "baseline"),
    ("gemini", "google-gemini-cli/gemini-3-pro-preview", "subscription", 0.0, 0.0, 0.0, "subscription route"),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    for provider, model, route_type, in_rate, out_rate, cached_rate, notes in PRICING:
        cur.execute(
            """
            SELECT id FROM model_pricing
            WHERE provider = ? AND model = ? AND route_type = ? AND active = 1
            ORDER BY effective_date DESC
            LIMIT 1
            """,
            (provider, model, route_type),
        )
        existing = cur.fetchone()
        if existing:
            continue

        cur.execute(
            """
            INSERT INTO model_pricing (
                id, provider, model, route_type,
                input_per_1m_usd, output_per_1m_usd, cached_input_per_1m_usd,
                effective_date, active, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (str(uuid4()), provider, model, route_type, in_rate, out_rate, cached_rate, now, notes),
        )

    conn.commit()
    conn.close()
    print("Pricing seed complete")


if __name__ == "__main__":
    main()
