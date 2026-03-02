"""Authentication dependencies and utilities."""

from datetime import datetime
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import APIToken


security = HTTPBearer()


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db)
) -> APIToken:
    """Validate bearer token against database. Returns the token record."""
    token = credentials.credentials
    result = await db.execute(
        select(APIToken).where(APIToken.token == token, APIToken.active == True)
    )
    api_token = result.scalar_one_or_none()
    
    if not api_token:
        raise HTTPException(status_code=401, detail="Invalid or inactive token")
    
    # Update last_used_at — best-effort, non-blocking.
    # This must never block a request or cascade into DB lock contention.
    from datetime import timezone
    try:
        api_token.last_used_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
    
    return api_token
