"""
Redis-backed cache for slowly-changing read endpoints.

Key design decisions:
- Per-user cache keys: keyed on tenant + user + URL path + query string.
  Every cached endpoint (types, reviewers, schema, review count) answers
  only with what the caller may see - grants, clearance and role differ per
  user - so a tenant-wide key would hand one user's counts to another. Each
  user still gets their own cached answer (enough for the /api/review/count
  polling loop), and different tenants never share an entry. Every cached
  endpoint takes `current_user: CurrentUser = Depends(...)`, which FastAPI
  resolves and fastapi-cache2 passes through in `kwargs` before this key
  builder runs - see fastapi_cache.decorator.cache's `inner()`.
- Silent fallback: if Redis is unreachable at startup the app continues with an
  in-memory backend so offline deployments (no Redis) still function.
- invalidate_cache() never raises - a failed invalidation causes a stale cache
  read at most, not a broken write path.
"""

import os
from collections.abc import Callable

from fastapi import Request, Response
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.backends.redis import RedisBackend
from redis.asyncio import Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


def _path_key_builder(
    func: Callable,
    namespace: str = "",
    *,
    request: Request | None = None,
    response: Response | None = None,
    args: tuple,
    kwargs: dict,
) -> str:
    """Build cache key from tenant + user + URL path + query string."""
    current_user = kwargs.get("current_user")
    tenant = getattr(current_user, "tenant_slug", None) or "-"
    user = getattr(current_user, "id", None) or "-"
    if request is not None:
        path = request.url.path
        query = f"?{request.url.query}" if request.url.query else ""
        return f"{namespace}:{tenant}:{user}:{path}{query}"
    return f"{namespace}:{tenant}:{user}:{func.__module__}.{func.__name__}"


async def init_cache() -> None:
    """Initialise FastAPICache.  Falls back to in-memory if Redis is unreachable."""
    try:
        redis_client = Redis.from_url(REDIS_URL, encoding="utf8", decode_responses=False)
        await redis_client.ping()
        FastAPICache.init(RedisBackend(redis_client), prefix="sdai")
    except Exception:
        FastAPICache.init(InMemoryBackend(), prefix="sdai")


async def invalidate_cache(*namespaces: str) -> None:
    """Clear all cached entries for the given namespace(s).  Never raises."""
    try:
        for ns in namespaces:
            await FastAPICache.clear(namespace=ns)
    except Exception:
        pass
