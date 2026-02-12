"""Middleware for request logging and other cross-cutting concerns."""

import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("app.http")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP requests with method, path, status, and duration."""
    
    async def dispatch(self, request: Request, call_next):
        """Process request and log details."""
        # Skip logging for health checks (too noisy)
        if request.url.path in ["/api/health", "/api/health/ready"]:
            return await call_next(request)
        
        # Record start time
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Log request
        logger.info(
            f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms"
        )
        
        return response
