#!/usr/bin/env python3
"""Generate a new API token. Run on the server machine only."""
import secrets
import asyncio
import sys
import os

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.database import AsyncSessionLocal, init_db
from app.models import APIToken


async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "default"
    
    # Initialize database
    await init_db()
    
    # Generate secure random token
    token = secrets.token_urlsafe(32)
    
    async with AsyncSessionLocal() as db:
        api_token = APIToken(token=token, name=name)
        db.add(api_token)
        await db.commit()
    
    print(f"Token generated for '{name}':")
    print(f"  {token}")
    print(f"\nUse as: Authorization: Bearer {token}")


if __name__ == "__main__":
    asyncio.run(main())
