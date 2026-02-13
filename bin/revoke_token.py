#!/usr/bin/env python3
"""Revoke (deactivate) an API token."""
import asyncio
import sys
import os
from sqlalchemy import select

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.database import AsyncSessionLocal, init_db
from app.models import APIToken


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 revoke_token.py <token_id or token_name>")
        print("\nUse list_tokens.py to see available tokens.")
        sys.exit(1)
    
    identifier = sys.argv[1]
    
    # Initialize database
    await init_db()
    
    async with AsyncSessionLocal() as db:
        # Try to find by ID first (if numeric)
        token = None
        try:
            token_id = int(identifier)
            result = await db.execute(select(APIToken).where(APIToken.id == token_id))
            token = result.scalar_one_or_none()
        except ValueError:
            pass
        
        # If not found, try by name
        if not token:
            result = await db.execute(select(APIToken).where(APIToken.name == identifier))
            token = result.scalar_one_or_none()
        
        if not token:
            print(f"Token not found: {identifier}")
            sys.exit(1)
        
        if not token.active:
            print(f"Token '{token.name}' (ID: {token.id}) is already revoked.")
            sys.exit(0)
        
        # Revoke the token
        token.active = False
        await db.commit()
        
        print(f"Token '{token.name}' (ID: {token.id}) has been revoked.")


if __name__ == "__main__":
    asyncio.run(main())
