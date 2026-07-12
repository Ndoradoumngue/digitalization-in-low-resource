"""
Rate limiting — shared Limiter instance imported by all routers.

Key function: authenticated requests are keyed by JWT user ID (sub claim) so
that a shared login is not penalised by a single heavy client.  Unauthenticated
requests fall back to client IP.

default_limits=["200/minute"] is applied to every endpoint via SlowAPIMiddleware;
individual endpoints that need a stricter cap are decorated with @limiter.limit().
"""

import os

from fastapi import Request
from jose import ExpiredSignatureError, JWTError, jwt
from slowapi import Limiter

_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "")
_ALGORITHM  = "HS256"


def _user_or_ip(request: Request) -> str:
    """Return 'user:<id>' for a valid JWT cookie, 'ip:<addr>' otherwise."""
    token_cookie = request.cookies.get("access_token")
    if token_cookie:
        try:
            raw     = token_cookie.removeprefix("Bearer ")
            payload = jwt.decode(raw, _SECRET_KEY, algorithms=[_ALGORITHM])
            uid     = payload.get("sub")
            if uid:
                return f"user:{uid}"
        except (JWTError, ExpiredSignatureError, Exception):
            pass  # expired / tampered token → fall back to IP

    xff = request.headers.get("X-Forwarded-For")
    # Use the last entry — appended by nginx and not spoofable by the client.
    ip  = xff.split(",")[-1].strip() if xff else (
        request.client.host if request.client else "unknown"
    )
    return f"ip:{ip}"


limiter = Limiter(key_func=_user_or_ip, default_limits=["200/minute"])
