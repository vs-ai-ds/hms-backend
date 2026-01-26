# app/core/redis.py
"""
Redis connection and caching utilities.
Redis is used for:
- Dashboard metrics caching (per-tenant)
- Rate limiting / login throttling
- Reference data caching (departments, etc.)

The app should boot even if Redis is unavailable (degraded mode).
"""

import logging
from functools import lru_cache
from typing import Optional

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None
_redis_available: bool = False


@lru_cache
def get_redis_client() -> Optional[redis.Redis]:
    """
    Get Redis client instance.
    Returns None if Redis is not configured or unavailable.
    """
    global _redis_client, _redis_available

    if _redis_client is not None:
        return _redis_client if _redis_available else None

    settings = get_settings()

    if not settings.redis_url:
        logger.warning("REDIS_URL not set. Redis features will be disabled.")
        return None

    try:
        _redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        # Test connection
        _redis_client.ping()
        _redis_available = True
        logger.info("Redis connection established successfully.")
        return _redis_client
    except Exception as e:
        logger.warning(
            f"Failed to connect to Redis: {e}. Running in degraded mode (no caching)."
        )
        _redis_available = False
        return None


def is_redis_available() -> bool:
    """Check if Redis is available."""
    client = get_redis_client()
    if not client:
        return False
    try:
        client.ping()
        return True
    except Exception:
        return False


def cache_get(key: str) -> Optional[str]:
    """Get value from cache. Returns None if Redis unavailable or key not found."""
    client = get_redis_client()
    if not client:
        return None
    try:
        return client.get(key)
    except Exception as e:
        logger.warning(f"Redis GET error for key '{key}': {e}")
        return None


def cache_set(key: str, value: str, ttl: int = 60) -> bool:
    """Set value in cache with TTL (seconds). Returns False if Redis unavailable."""
    client = get_redis_client()
    if not client:
        return False
    try:
        client.setex(key, ttl, value)
        return True
    except Exception as e:
        logger.warning(f"Redis SET error for key '{key}': {e}")
        return False


def cache_delete(key: str) -> bool:
    """Delete key from cache. Returns False if Redis unavailable."""
    client = get_redis_client()
    if not client:
        return False
    try:
        client.delete(key)
        return True
    except Exception as e:
        logger.warning(f"Redis DELETE error for key '{key}': {e}")
        return False
