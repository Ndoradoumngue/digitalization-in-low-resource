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
import time
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


# Fallback for first_in_window when Redis isn't there (offline deployments
# run on the in-memory cache backend) - per process, which is what that
# deployment has anyway.
_local_windows: dict[str, float] = {}


async def first_in_window(key: str, seconds: int) -> bool:
    """True the first time `key` is seen within a `seconds`-long window,
    False for every repeat inside it - e.g. one document_viewed audit entry
    per user and document every 5 minutes, however often the page reloads.
    Uses Redis (SET NX EX, shared by every worker) when the cache runs on
    it, else a per-process dict. Never raises - when in doubt it answers
    True, since a duplicate audit entry beats a missing one."""
    try:
        backend = FastAPICache.get_backend()
        redis = getattr(backend, "redis", None)
        if redis is not None:
            return bool(await redis.set(f"sdai:window:{key}", b"1", ex=seconds, nx=True))
    except Exception:
        pass
    now = time.monotonic()
    expires = _local_windows.get(key)
    if expires is not None and expires > now:
        return False
    if len(_local_windows) > 10_000:
        for k in [k for k, exp in _local_windows.items() if exp <= now]:
            del _local_windows[k]
    _local_windows[key] = now + seconds
    return True
