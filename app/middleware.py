"""Middleware for request logging, network access control, and security."""

import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("app.http")
security_logger = logging.getLogger("app.security")


def _is_allowed_ip(ip: str) -> bool:
    """Allow localhost and Tailscale (100.x.x.x) only."""
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if ip.startswith("100."):
        return True
    return False


class NetworkGuardMiddleware(BaseHTTPMiddleware):
    """Block requests from LAN — only allow localhost and Tailscale."""
    
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        if not _is_allowed_ip(client_ip):
            logger.warning(f"Blocked request from {client_ip} {request.method} {request.url.path}")
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP requests with method, path, status, duration, and client info."""
    
    # Paths to skip logging (too noisy)
    SKIP_PATHS = {"/api/health", "/api/health/ready"}
    
    async def dispatch(self, request: Request, call_next):
        """Process request and log details."""
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)
        
        start_time = time.time()
        
        # Get client info
        client_ip = request.client.host if request.client else "unknown"
        
        # Get token name from auth header (just the first 8 chars for identification)
        auth = request.headers.get("authorization", "")
        token_hint = ""
        if auth.startswith("Bearer "):
            token = auth[7:]
            token_hint = f" token=...{token[-8:]}" if len(token) > 8 else ""
        
        # Query string
        query = f"?{request.url.query}" if request.url.query else ""
        
        try:
            response = await call_next(request)
            duration_ms = int((time.time() - start_time) * 1000)
            
            logger.info(
                f"{client_ip} {request.method} {request.url.path}{query} "
                f"{response.status_code} {duration_ms}ms{token_hint}"
            )
            
            return response
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"{client_ip} {request.method} {request.url.path}{query} "
                f"500 {duration_ms}ms{token_hint} error={e}"
            )
            raise


class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Reject requests with body size exceeding max_bytes.
    
    Security control: Prevents DoS attacks via oversized payloads.
    Particularly important for webhook endpoints accepting external input.
    """
    
    def __init__(self, app, max_bytes: int = 1_048_576):  # 1MB default
        super().__init__(app)
        self.max_bytes = max_bytes
    
    async def dispatch(self, request: Request, call_next):
        """Check Content-Length before reading body."""
        content_length = request.headers.get("content-length")
        
        if content_length:
            try:
                size = int(content_length)
                if size > self.max_bytes:
                    client_ip = request.client.host if request.client else "unknown"
                    security_logger.warning(
                        f"Rejected oversized request: {size} bytes "
                        f"(limit: {self.max_bytes}) from {client_ip} "
                        f"to {request.url.path}"
                    )
                    return JSONResponse(
                        {"detail": "Payload too large"},
                        status_code=413
                    )
            except ValueError:
                # Invalid Content-Length header - let it through,
                # will fail at JSON parsing stage
                pass
        
        return await call_next(request)
