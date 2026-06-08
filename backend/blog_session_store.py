"""
blog_session_store.py
---------------------
In-memory store mapping user_id → a stable Langfuse session_id (trace_id)
that is reused for all AI steps within the same blog creation workflow.

TTL: 2 hours. After TTL expires a new session_id is generated on the next call.

Usage:
    from blog_session_store import get_or_create_session_id
    sid = get_or_create_session_id(user_id)
    # pass sid to set_current_session_id() before each @observe trace
"""

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

_SESSION_TTL_HOURS = 2
_lock = threading.Lock()

# {user_id: (session_id_hex32, expires_at)}
_store: Dict[str, Tuple[str, datetime]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_to_hex32(u: str) -> str:
    """Convert a UUID string to 32 lowercase hex chars (Langfuse trace_id format)."""
    return u.replace("-", "").lower()


def get_or_create_session_id(user_id: str) -> str:
    """
    Return the current session_id for the given user.
    Creates a new one if none exists or if the previous one has expired.
    Returns a 32-char lowercase hex string suitable as a Langfuse trace_id.
    """
    if not user_id:
        # No user context — generate a one-off (won't group but won't break)
        return _uuid_to_hex32(str(uuid.uuid4()))

    with _lock:
        entry = _store.get(user_id)
        if entry:
            sid, expires_at = entry
            if _now() < expires_at:
                return sid
        # Create or refresh
        new_sid = _uuid_to_hex32(str(uuid.uuid4()))
        _store[user_id] = (new_sid, _now() + timedelta(hours=_SESSION_TTL_HOURS))
        return new_sid


def reset_session_id(user_id: str) -> str:
    """
    Force a new session_id for the given user (call this when a blog is published/saved).
    Returns the new session_id.
    """
    with _lock:
        new_sid = _uuid_to_hex32(str(uuid.uuid4()))
        _store[user_id] = (new_sid, _now() + timedelta(hours=_SESSION_TTL_HOURS))
        return new_sid


def get_session_id(user_id: str) -> Optional[str]:
    """Return the current session_id if one exists and hasn't expired, else None."""
    with _lock:
        entry = _store.get(user_id)
        if entry:
            sid, expires_at = entry
            if _now() < expires_at:
                return sid
    return None
