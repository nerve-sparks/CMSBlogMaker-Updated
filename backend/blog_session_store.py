"""
blog_session_store.py
---------------------
Shared store mapping user_id → a stable Langfuse session_id (trace_id)
reused for all AI steps within the same blog creation workflow.

Uses Redis (via REDIS_URL env var) so all gunicorn/uvicorn workers share
the same session map. Falls back to an in-memory dict if Redis is unavailable.

TTL: 2 hours.
"""

import os
import uuid
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_SESSION_TTL_HOURS = 2
_SESSION_TTL_SECONDS = _SESSION_TTL_HOURS * 3600
_REDIS_KEY_PREFIX = "cms_blog_session:"

# ---------------------------------------------------------------------------
# Redis client (optional)
# ---------------------------------------------------------------------------
_redis_client = None
_redis_lock = threading.Lock()


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        url = os.getenv("REDIS_URL", "")
        if not url:
            return None
        try:
            import redis
            client = redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
            logger.info("blog_session_store: Redis connected at %s", url)
            _redis_client = client
        except Exception as e:
            logger.warning("blog_session_store: Redis unavailable (%s). Using in-memory fallback.", e)
            _redis_client = None
    return _redis_client


# ---------------------------------------------------------------------------
# In-memory fallback (single-process only)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_store: Dict[str, Tuple[str, datetime]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_to_hex32(u: str) -> str:
    return u.replace("-", "").lower()


def _new_sid() -> str:
    return _uuid_to_hex32(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_create_session_id(user_id: str) -> str:
    if not user_id:
        return _new_sid()

    r = _get_redis()

    if r is not None:
        try:
            key = _REDIS_KEY_PREFIX + user_id
            sid = r.get(key)
            if sid:
                return sid
            sid = _new_sid()
            r.set(key, sid, ex=_SESSION_TTL_SECONDS)
            return sid
        except Exception as e:
            logger.warning("blog_session_store: Redis get_or_create failed (%s). Falling back.", e)

    # In-memory fallback
    with _lock:
        entry = _store.get(user_id)
        if entry:
            sid, expires_at = entry
            if _now() < expires_at:
                return sid
        sid = _new_sid()
        _store[user_id] = (sid, _now() + timedelta(hours=_SESSION_TTL_HOURS))
        return sid


def reset_session_id(user_id: str) -> str:
    sid = _new_sid()

    r = _get_redis()
    if r is not None:
        try:
            key = _REDIS_KEY_PREFIX + user_id
            r.set(key, sid, ex=_SESSION_TTL_SECONDS)
            return sid
        except Exception as e:
            logger.warning("blog_session_store: Redis reset failed (%s). Falling back.", e)

    with _lock:
        _store[user_id] = (sid, _now() + timedelta(hours=_SESSION_TTL_HOURS))
    return sid


def get_session_id(user_id: str) -> Optional[str]:
    r = _get_redis()
    if r is not None:
        try:
            return r.get(_REDIS_KEY_PREFIX + user_id)
        except Exception as e:
            logger.warning("blog_session_store: Redis get failed (%s). Falling back.", e)

    with _lock:
        entry = _store.get(user_id)
        if entry:
            sid, expires_at = entry
            if _now() < expires_at:
                return sid
    return None
