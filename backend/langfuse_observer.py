from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple
from contextvars import ContextVar
import inspect
import uuid

from langfuse_tracer import (
    get_tracer, get_current_trace_identity, _AGENT_METADATA, _DEFAULT_TAGS,
    consume_pending_session_id,
)

_CURRENT_IDENTITY: ContextVar[Dict[str, Optional[str]]] = ContextVar(
    "langfuse_current_identity",
    default={"tenant_id": None, "user_id": None, "session_id": None},
)
logger = logging.getLogger("langfuse_observer")

# Import for type checking only
try:
    from fastapi import Request
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def observe(
    name: Optional[str] = None,
    as_type: str = "function",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    extract_tenant_from_request: bool = False,  # Backward-compatible option name
) -> Callable:
    """Decorator to trace function calls with Langfuse identity and usage support.

    Args:
        extract_tenant_from_request: If True, extract tenant_id/user_id from FastAPI Request.
    """

    def decorator(func: Callable) -> Callable:
        _base_name = name or func.__name__
        func_name = _base_name

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                if not tracer.enabled:
                    logger.info("[observe:%s] tracer disabled", func_name)
                    return await func(*args, **kwargs)

                dynamic_session_id = session_id
                dynamic_user_id = user_id
                dynamic_tenant_id = tenant_id
                inherited = _CURRENT_IDENTITY.get() or {}

                if extract_tenant_from_request and HAS_FASTAPI:
                    request = kwargs.get("request")
                    if not request and args:
                        for arg in args:
                            if isinstance(arg, Request):
                                request = arg
                                break

                    if request:
                        req_tenant, req_user, req_session = await _extract_identity_from_request(request)
                        dynamic_tenant_id = dynamic_tenant_id or req_tenant
                        dynamic_user_id = dynamic_user_id or req_user
                        dynamic_session_id = dynamic_session_id or req_session

                kw_tenant, kw_user, kw_session = _extract_identity_from_kwargs(kwargs)
                dynamic_tenant_id = dynamic_tenant_id or kw_tenant
                dynamic_user_id = dynamic_user_id or kw_user
                dynamic_session_id = dynamic_session_id or kw_session

                # Extract session_id from Pydantic payload (first positional arg)
                if not dynamic_session_id:
                    for arg in args:
                        arg_session = getattr(arg, "session_id", None)
                        if arg_session:
                            dynamic_session_id = str(arg_session)
                            break

                tracer_inherited = get_current_trace_identity()
                dynamic_tenant_id = dynamic_tenant_id or inherited.get("tenant_id")
                dynamic_user_id = dynamic_user_id or inherited.get("user_id")
                dynamic_session_id = dynamic_session_id or inherited.get("session_id")
                dynamic_tenant_id = dynamic_tenant_id or tracer_inherited.get("tenant_id")
                dynamic_user_id = dynamic_user_id or tracer_inherited.get("user_id")
                dynamic_session_id = dynamic_session_id or tracer_inherited.get("session_id")
                # Check if a session_id was pre-set via set_current_session_id() in the handler
                dynamic_session_id = dynamic_session_id or consume_pending_session_id()
                if not dynamic_session_id:
                    dynamic_session_id = str(uuid.uuid4())
                    logger.info("[observe:%s] generated session_id=%s", func_name, dynamic_session_id)
                logger.info(
                    "[observe:%s] identity tenant_id=%s user_id=%s session_id=%s",
                    func_name,
                    dynamic_tenant_id,
                    dynamic_user_id,
                    dynamic_session_id,
                )

                metadata = {
                    "as_type": as_type,
                    "function": func.__name__,
                }
                if dynamic_tenant_id:
                    metadata["tenant_id"] = dynamic_tenant_id
                if dynamic_user_id:
                    metadata["user_id"] = dynamic_user_id

                identity_token = _CURRENT_IDENTITY.set(
                    {
                        "tenant_id": dynamic_tenant_id,
                        "user_id": dynamic_user_id,
                        "session_id": dynamic_session_id,
                    }
                )
                _trace_input = args[0] if args else (kwargs.get("input_data") or kwargs.get("data"))
                try:
                    with tracer.trace(
                        func_name,
                        metadata=metadata,
                        session_id=dynamic_session_id,
                        user_id=dynamic_user_id,
                        tenant_id=dynamic_tenant_id,
                        input_data=_trace_input,
                        tags=list(_DEFAULT_TAGS),
                    ) as ctx:
                        logger.info("[observe:%s] trace opened", func_name)
                        result = await func(*args, **kwargs)
                        ctx.output = result
                        logger.info("[observe:%s] trace output attached", func_name)
                        return result
                finally:
                    _CURRENT_IDENTITY.reset(identity_token)
                    logger.info("[observe:%s] identity context reset", func_name)

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            if not tracer.enabled:
                logger.info("[observe:%s] tracer disabled", func_name)
                return func(*args, **kwargs)

            dynamic_session_id = session_id
            dynamic_user_id = user_id
            dynamic_tenant_id = tenant_id
            inherited = _CURRENT_IDENTITY.get() or {}

            kw_tenant, kw_user, kw_session = _extract_identity_from_kwargs(kwargs)
            dynamic_tenant_id = dynamic_tenant_id or kw_tenant
            dynamic_user_id = dynamic_user_id or kw_user
            dynamic_session_id = dynamic_session_id or kw_session
            tracer_inherited = get_current_trace_identity()
            dynamic_tenant_id = dynamic_tenant_id or inherited.get("tenant_id")
            dynamic_user_id = dynamic_user_id or inherited.get("user_id")
            dynamic_session_id = dynamic_session_id or inherited.get("session_id")
            dynamic_tenant_id = dynamic_tenant_id or tracer_inherited.get("tenant_id")
            dynamic_user_id = dynamic_user_id or tracer_inherited.get("user_id")
            dynamic_session_id = dynamic_session_id or tracer_inherited.get("session_id")
            if not dynamic_session_id:
                dynamic_session_id = str(uuid.uuid4())
                logger.info("[observe:%s] generated session_id=%s", func_name, dynamic_session_id)
            logger.info(
                "[observe:%s] identity tenant_id=%s user_id=%s session_id=%s",
                func_name,
                dynamic_tenant_id,
                dynamic_user_id,
                dynamic_session_id,
            )

            metadata = {
                "as_type": as_type,
                "function": func.__name__,
                **_AGENT_METADATA,
            }
            if dynamic_tenant_id:
                metadata["tenant_id"] = dynamic_tenant_id
            if dynamic_user_id:
                metadata["user_id"] = dynamic_user_id

            identity_token = _CURRENT_IDENTITY.set(
                {
                    "tenant_id": dynamic_tenant_id,
                    "user_id": dynamic_user_id,
                    "session_id": dynamic_session_id,
                }
            )
            _trace_input = args[0] if args else (kwargs.get("input_data") or kwargs.get("data"))
            try:
                with tracer.trace(
                    func_name,
                    metadata=metadata,
                    session_id=dynamic_session_id,
                    user_id=dynamic_user_id,
                    tenant_id=dynamic_tenant_id,
                    input_data=_trace_input,
                    tags=list(_DEFAULT_TAGS),
                ) as ctx:
                    logger.info("[observe:%s] trace opened", func_name)
                    result = func(*args, **kwargs)
                    ctx.output = result
                    logger.info("[observe:%s] trace output attached", func_name)
                    return result
            finally:
                _CURRENT_IDENTITY.reset(identity_token)
                logger.info("[observe:%s] identity context reset", func_name)

        return sync_wrapper

    return decorator


def _extract_identity_from_payload(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract tenant_id, user_id, and session_id from flat payload keys."""
    tenant_value = payload.get("tenant_id")
    user_value = payload.get("user_id")
    session_value = payload.get("session_id")

    tenant_id = str(tenant_value) if tenant_value is not None else None
    user_id = str(user_value) if user_value is not None else None
    session_id = str(session_value) if session_value is not None else None
    return tenant_id, user_id, session_id


def _extract_identity_from_kwargs(kwargs: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract identity values from direct kwargs."""
    tenant_id = kwargs.get("tenant_id")
    user_id = kwargs.get("user_id")
    session_id = kwargs.get("session_id")

    tenant_str = str(tenant_id) if tenant_id is not None else None
    user_str = str(user_id) if user_id is not None else None
    session_str = str(session_id) if session_id is not None else None
    return tenant_str, user_str, session_str


async def _extract_identity_from_request(request: "Request") -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract tenant_id, user_id, and session_id from FastAPI request state or body."""
    try:
        # Primary source: state claims populated by get_current_user dependency.
        state_claims = getattr(getattr(request, "state", None), "auth_claims", None)
        if isinstance(state_claims, dict):
            tenant_id = state_claims.get("tenant_id")
            user_id = state_claims.get("sub") or state_claims.get("user_id")
            if tenant_id or user_id:
                return (
                    str(tenant_id) if tenant_id is not None else None,
                    str(user_id) if user_id is not None else None,
                    None,
                )

        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            body = await request.json()
            if isinstance(body, dict):
                return _extract_identity_from_payload(body)
            return None, None, None

        form = await request.form()
        payload = {
            "tenant_id": form.get("tenant_id"),
            "user_id": form.get("user_id"),
            "session_id": form.get("session_id"),
        }
        return _extract_identity_from_payload(payload)

    except Exception:
        return None, None, None
