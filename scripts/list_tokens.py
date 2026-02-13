#!/usr/bin/env python3
"""List all API tokens."""
import asyncio
import sys
import os
from sqlalchemy import select

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.database import AsyncSessionLocal, init_db
from app.models import APIToken


async def main():
    # Initialize database
    await init_db()
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(APIToken).order_by(APIToken.created_at))
        tokens = result.scalars().all()
        
        if not tokens:
            print("No tokens found.")
            return
        
        print(f"\n{'ID':<5} {'Name':<20} {'Active':<8} {'Created':<20} {'Last Used':<20}")
        print("-" * 80)
        
        for token in tokens:
            active_str = "✓" if token.active else "✗"
            created_str = token.created_at.strftime("%Y-%m-%d %H:%M:%S")
            last_used_str = token.last_used_at.strftime("%Y-%m-%d %H:%M:%S") if token.last_used_at else "never"
            
            print(f"{token.id:<5} {token.name:<20} {active_str:<8} {created_str:<20} {last_used_str:<20}")
        
        print()


if __name__ == "__main__":
    asyncio.run(main())
