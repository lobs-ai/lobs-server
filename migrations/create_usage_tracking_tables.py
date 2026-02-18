#!/usr/bin/env python3
"""Create usage tracking and model pricing tables."""

import sqlite3
from pathlib import Path


def main() -> None:
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS model_usage_events (
            id TEXT PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL DEFAULT 'unknown',
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            route_type TEXT NOT NULL DEFAULT 'api',
            task_type TEXT NOT NULL DEFAULT 'other',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 1,
            latency_ms INTEGER,
            status TEXT NOT NULL DEFAULT 'success',
            estimated_cost_usd FLOAT NOT NULL DEFAULT 0.0,
            error_code TEXT,
            event_metadata JSON,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_model_usage_events_timestamp ON model_usage_events(timestamp)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_model_usage_events_provider_model ON model_usage_events(provider, model)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS model_pricing (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            route_type TEXT NOT NULL DEFAULT 'api',
            input_per_1m_usd FLOAT NOT NULL DEFAULT 0.0,
            output_per_1m_usd FLOAT NOT NULL DEFAULT 0.0,
            cached_input_per_1m_usd FLOAT NOT NULL DEFAULT 0.0,
            effective_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_model_pricing_provider_model_effective ON model_pricing(provider, model, effective_date)
        """
    )

    conn.commit()
    conn.close()
    print("Usage tracking migration complete")


if __name__ == "__main__":
    main()
