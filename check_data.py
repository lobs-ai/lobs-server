#!/usr/bin/env python3
"""Quick script to check task and usage data."""
import asyncio
import sys
from app.database import get_db_session
from app.models import Task, ModelUsageEvent
from sqlalchemy import select, func

async def main():
    async for db in get_db_session():
        # Count completed tasks by agent
        result = await db.execute(
            select(Task.agent, func.count(Task.id))
            .where(Task.status == 'completed')
            .group_by(Task.agent)
        )
        tasks = result.all()
        print('Completed tasks by agent:')
        for agent, count in tasks:
            print(f'  {agent or "(unassigned)"}: {count}')
        
        # Total usage events
        result = await db.execute(select(func.count(ModelUsageEvent.id)))
        usage_count = result.scalar()
        print(f'\nTotal model usage events: {usage_count or 0}')
        
        # Sample of usage data
        result = await db.execute(
            select(ModelUsageEvent)
            .order_by(ModelUsageEvent.timestamp.desc())
            .limit(5)
        )
        events = result.scalars().all()
        if events:
            print('\nRecent usage events:')
            for e in events:
                print(f'  {e.timestamp} | {e.model} | {e.provider} | cost: ${e.estimated_cost_usd:.6f}')
        
        break

if __name__ == '__main__':
    asyncio.run(main())
