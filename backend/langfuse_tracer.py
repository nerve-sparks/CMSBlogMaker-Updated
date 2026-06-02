"""
Lightweight Langfuse tracer for generated pipeline projects with identity and usage support.

This wrapper is defensive by design:
- If the langfuse package is not installed, tracing is disabled.
- If LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are missing, tracing is disabled.
- Any Langfuse errors are logged but do NOT stop the agent from running.
"""

from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    # Import Langfuse client. Any exception here (including version/typing
    # issues inside the langfuse package) will disable tracing but MUST NOT
    # break the generated agent.
    from langfuse import Langfuse, get_client, propagate_attributes as _lf_propagate_attributes
    from langfuse.types import TraceContext as LangfuseTraceContext
except Exception:  # pragma: no cover - optional / best-effort dependency
    Langfuse = None  # type: ignore
    get_client = None  # type: ignore
    _lf_propagate_attributes = None  # type: ignore
    LangfuseTraceContext = None  # type: ignore

logger = logging.getLogger("langfuse_tracer")

_AGENT_METADATA: Dict[str, Any] = {
    "agent_id": 'cms_blog_maker',
    "workflow_id": 'wf_cms_blog',
    "department": 'content',
    "task_type": 'blog_generation',
}

_DEFAULT_TAGS: list[str] = ["cms_blog_maker"]
TOKEN_INPUT_KEYS = ("input_tokens", "prompt_tokens", "tokens_in")
TOKEN_OUTPUT_KEYS = ("output_tokens", "completion_tokens", "tokens_out")
TOKEN_TOTAL_KEYS = ("total_tokens", "tokens_total")
COST_KEYS = ("total_cost", "cost", "estimated_cost", "usd_cost")
_CURRENT_USAGE_METRICS: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "langfuse_current_usage_metrics",
    default=None,
)
_CURRENT_USAGE_MODEL: ContextVar[Optional[str]] = ContextVar(
    "langfuse_current_usage_model",
    default=None,
)
_CURRENT_TRACE_IDENTITY: ContextVar[Optional[Dict[str, Optional[str]]]] = ContextVar(
    "langfuse_current_trace_identity",
    default=None,
)
_CURRENT_INPUT_DATA: ContextVar[Optional[Any]] = ContextVar(
    "langfuse_current_input_data",
    default=None,
)
_CURRENT_SYSTEM_PROMPT: ContextVar[Optional[str]] = ContextVar(
    "langfuse_current_system_prompt",
    default=None,
)
_CURRENT_PREVIOUS_NODE_OUTPUT: ContextVar[Optional[Any]] = ContextVar(
    "langfuse_current_previous_node_output",
    default=None,
)
_CURRENT_PENDING_SESSION_ID: ContextVar[Optional[str]] = ContextVar(
    "langfuse_current_pending_session_id",
    default=None,
)


def set_current_input_data(data: Any) -> None:
    """Call this right after input_data is parsed so TraceContext can include it."""
    _CURRENT_INPUT_DATA.set(data)


def set_current_session_id(session_id: str) -> None:
    """Pre-set a session_id so the next @observe trace is tagged with it for grouping."""
    if session_id:
        _CURRENT_PENDING_SESSION_ID.set(str(session_id))


def consume_pending_session_id() -> Optional[str]:
    """Pop the pending session_id (used internally by observer)."""
    sid = _CURRENT_PENDING_SESSION_ID.get()
    if sid:
        _CURRENT_PENDING_SESSION_ID.set(None)
    return sid


def set_current_system_prompt(prompt: str) -> None:
    """Store the system prompt used by an LLM node so Langfuse can display it."""
    if prompt:
        _CURRENT_SYSTEM_PROMPT.set(str(prompt))


def set_current_previous_node_output(output: Any) -> None:
    """Store the output of the previous node so Langfuse can show the full context chain."""
    _CURRENT_PREVIOUS_NODE_OUTPUT.set(output)


_SENSITIVE_INPUT_KEYS = frozenset({
    "mcp_host_access_token", "password", "secret", "token", "api_key",
    "sender_password", "private_key",
})


def _sanitize_input_for_trace(value: Any, _depth: int = 0) -> Any:
    """Recursively make input_data safe for Langfuse — no bytes, no huge strings, no secrets."""
    if _depth > 6:
        return "<truncated>"
    if isinstance(value, bytes):
        return {"_type": "bytes", "size_bytes": len(value)}
    if hasattr(value, "read") or hasattr(value, "filename"):
        name = getattr(value, "filename", getattr(value, "name", None))
        return {"_type": "file", "filename": name}
    if isinstance(value, str):
        # Base64 or very large strings are not useful in traces
        if len(value) > 512:
            return value[:256] + f"...<truncated {len(value)} chars>"
        return value
    if isinstance(value, dict):
        return {
            k: "<redacted>" if k in _SENSITIVE_INPUT_KEYS else _sanitize_input_for_trace(v, _depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_input_for_trace(v, _depth + 1) for v in value]
    # Primitives and anything else that is JSON-safe
    try:
        import json as _json
        _json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_metric(source: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)
    return None


def _extract_usage_cost_metrics(output: Any) -> Dict[str, Any]:
    """Best-effort extraction of token usage and cost from function output."""
    if not isinstance(output, dict):
        return {}

    candidates = [output]
    for nested_key in ("usage", "token_usage", "llm_usage", "metrics", "metadata"):
        nested = output.get(nested_key)
        if isinstance(nested, dict):
            candidates.append(nested)

    input_tokens = None
    output_tokens = None
    total_tokens = None
    total_cost = None

    for candidate in candidates:
        if input_tokens is None:
            input_tokens = _coerce_int(_pick_metric(candidate, TOKEN_INPUT_KEYS))
        if output_tokens is None:
            output_tokens = _coerce_int(_pick_metric(candidate, TOKEN_OUTPUT_KEYS))
        if total_tokens is None:
            total_tokens = _coerce_int(_pick_metric(candidate, TOKEN_TOTAL_KEYS))
        if total_cost is None:
            total_cost = _coerce_float(_pick_metric(candidate, COST_KEYS))

    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    metrics: Dict[str, Any] = {}
    if input_tokens is not None:
        metrics["input_tokens"] = input_tokens
    if output_tokens is not None:
        metrics["output_tokens"] = output_tokens
    if total_tokens is not None:
        metrics["total_tokens"] = total_tokens
    if total_cost is not None:
        metrics["total_cost_usd"] = total_cost
    return metrics


def set_current_usage_metrics(metrics: Dict[str, Any], model: Optional[str] = None) -> None:
    """Attach per-call usage metrics to be merged at trace finalization."""
    clean = {k: v for k, v in (metrics or {}).items() if v is not None}
    if clean:
        _CURRENT_USAGE_METRICS.set(clean)
    if model:
        _CURRENT_USAGE_MODEL.set(str(model))


def _consume_current_usage_metrics() -> Tuple[Dict[str, Any], Optional[str]]:
    metrics = _CURRENT_USAGE_METRICS.get() or {}
    model = _CURRENT_USAGE_MODEL.get()
    _CURRENT_USAGE_METRICS.set(None)
    _CURRENT_USAGE_MODEL.set(None)
    return metrics, model


def get_current_trace_identity() -> Dict[str, Optional[str]]:
    identity = _CURRENT_TRACE_IDENTITY.get() or {}
    return {
        "tenant_id": identity.get("tenant_id"),
        "user_id": identity.get("user_id"),
        "session_id": identity.get("session_id"),
    }


def _normalize_model_for_pricing(model: Optional[str]) -> Optional[str]:
    """Normalize provider model ids so Langfuse pricing lookup can match."""
    if not model:
        return model
    value = str(model).strip()
    if not value:
        return value
    # OpenAI: gpt-4o-mini-2024-07-18 -> gpt-4o-mini
    value = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", value)
    # Anthropic: claude-3-5-sonnet-20241022 -> claude-3-5-sonnet
    value = re.sub(r"-\d{8}$", "", value)
    # Anthropic: claude-3-5-sonnet-latest -> claude-3-5-sonnet
    value = re.sub(r"-latest$", "", value)
    # Google: gemini-1.5-pro-001 -> gemini-1.5-pro
    value = re.sub(r"-\d{3}$", "", value)
    # Google: gemini-1.5-flash-latest -> gemini-1.5-flash
    value = re.sub(r"-latest$", "", value)
    # Google: gemini-2.0-flash-exp -> gemini-2.0-flash
    value = re.sub(r"-exp$", "", value)
    return value


class LangfuseTracer:
    """Minimal Langfuse client wrapper with graceful degradation."""

    def __init__(self) -> None:
        self._client: Optional[Langfuse] = None  # type: ignore[assignment]
        self._enabled: bool = False
        self._initialized: bool = False

    def _initialize(self) -> None:
        if self._initialized:
            return

        self._initialized = True

        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        if not Langfuse:
            logger.warning("Langfuse package not installed. Tracing is disabled.")
            return

        if not public_key or not secret_key:
            logger.info("Langfuse credentials not set. Tracing is disabled.")
            return

        try:
            self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
            self._enabled = True
            logger.info("Langfuse tracing enabled for generated pipeline.")
        except Exception as exc:  # pragma: no cover - best effort logging
            logger.warning("Failed to initialize Langfuse client: %s", exc)
            self._client = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        if not self._initialized:
            self._initialize()
        return self._enabled

    @property
    def client(self):
        if not self._initialized:
            self._initialize()
        return self._client

    def trace_client(self):
        """Return a client object that exposes trace(), if available."""
        client = self.client
        if client and hasattr(client, "trace"):
            return client
        if callable(get_client):
            try:
                alt = get_client()
                if alt and hasattr(alt, "trace"):
                    return alt
            except Exception:
                pass
        return None

    def trace(
        self,
        name: str,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        input_data: Optional[Any] = None,
        tags: Optional[list[str]] = None,
    ) -> "TraceContext":
        """Return a context manager for a single traced call with identity and usage support."""
        return TraceContext(
            tracer=self,
            name=name,
            metadata=metadata or {},
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            input_data=input_data,
            tags=tags if tags is not None else list(_DEFAULT_TAGS),
        )


class TraceContext:
    """Context manager for a single Langfuse generation/observation."""

    def __init__(
        self,
        tracer: LangfuseTracer,
        name: str,
        metadata: Dict[str, Any],
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        input_data: Optional[Any] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        self._tracer = tracer
        self._name = name
        self._metadata = metadata
        self._session_id = session_id
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._input_data = input_data
        self._tags = tags or []
        self._observation = None
        self._context_manager = None
        self._propagation_context = None
        self._identity_token = None
        self.output = None  # Captures wrapped function return value

    def __enter__(self) -> "TraceContext":
        if not self._tracer.enabled or not self._tracer.client:
            logger.info(
                "[trace:%s] disabled or missing client (enabled=%s)",
                self._name,
                self._tracer.enabled,
            )
            return self

        try:
            # Always inject static agent metadata into every trace
            for _k, _v in _AGENT_METADATA.items():
                if _v and _k not in self._metadata:
                    self._metadata[_k] = _v
            if self._tenant_id and "tenant_id" not in self._metadata:
                self._metadata["tenant_id"] = self._tenant_id
            # For explicitly passed input_data (non-FastAPI callers), inject immediately
            if self._input_data is not None:
                try:
                    self._metadata["input_data"] = _sanitize_input_for_trace(self._input_data)
                except Exception as _san_err:
                    self._metadata["input_data"] = f"<sanitize error: {_san_err}>"
            logger.info(
                "[trace:%s] enter tenant_id=%s user_id=%s session_id=%s",
                self._name,
                self._tenant_id,
                self._user_id,
                self._session_id,
            )
            self._identity_token = _CURRENT_TRACE_IDENTITY.set(
                {
                    "tenant_id": self._tenant_id,
                    "user_id": self._user_id,
                    "session_id": self._session_id,
                }
            )

            trace_client = self._tracer.trace_client()

            # Prefer explicit trace API whenever available.
            # This reliably attaches trace-level identity (user/session) in Langfuse.
            if trace_client:
                logger.info("[trace:%s] using client.trace() identity path", self._name)
                _trace_kwargs: Dict[str, Any] = {
                    "name": self._name,
                    "session_id": self._session_id,
                    "user_id": self._user_id,
                    "metadata": self._metadata,
                }
                if self._tags:
                    _trace_kwargs["tags"] = self._tags
                self._trace = trace_client.trace(**_trace_kwargs)
                self._observation = self._trace.generation(
                    name=self._name,
                    input=self._metadata,
                    metadata=self._metadata,
                )

            # --- Langfuse v3 SDK (Current) ---
            elif hasattr(self._tracer.client, "start_as_current_observation"):
                logger.info("[trace:%s] using start_as_current_observation() path", self._name)
                # v3 strictly expects "span", "generation", or "event"
                v3_as_type = self._metadata.get("as_type", "span")
                if v3_as_type not in ["span", "generation", "event"]:
                    v3_as_type = "span"

                # Langfuse v4 uses public module-level propagate_attributes().
                if self._session_id or self._user_id or self._tags:
                    if callable(_lf_propagate_attributes):
                        try:
                            _prop_kwargs: Dict[str, Any] = {
                                "user_id": self._user_id or None,
                                "session_id": self._session_id,
                                "trace_name": self._name,
                            }
                            if self._tags:
                                _prop_kwargs["tags"] = self._tags
                            self._propagation_context = _lf_propagate_attributes(**_prop_kwargs)
                            self._propagation_context.__enter__()
                            logger.info("[trace:%s] identity/tags propagation context entered", self._name)
                        except Exception as prop_err:
                            logger.warning("Failed to enter propagate_attributes context: %s", prop_err)
                            self._propagation_context = None
                    else:
                        self._propagation_context = None

                try:
                    # If session_id provided, use it as the fixed trace_id so all
                    # spans from different HTTP requests share ONE parent trace.
                    _obs_kwargs: Dict[str, Any] = dict(
                        name=self._name,
                        as_type=v3_as_type,
                        input=self._metadata,
                        metadata=self._metadata,
                    )
                    if self._session_id and LangfuseTraceContext:
                        # Langfuse trace_id must be 32 lowercase hex chars (no dashes)
                        _raw_sid = self._session_id.replace("-", "").lower()
                        if len(_raw_sid) == 32 and all(c in "0123456789abcdef" for c in _raw_sid):
                            _obs_kwargs["trace_context"] = LangfuseTraceContext(
                                trace_id=_raw_sid,
                            )
                            logger.info("[trace:%s] pinned to trace_id=%s", self._name, _raw_sid)
                        else:
                            # session_id is not UUID-shaped — fall back to identity params
                            _obs_kwargs["session_id"] = self._session_id
                            _obs_kwargs["user_id"] = self._user_id
                    else:
                        # Fall back to passing session_id/user_id directly
                        _obs_kwargs["session_id"] = self._session_id
                        _obs_kwargs["user_id"] = self._user_id
                    self._context_manager = self._tracer.client.start_as_current_observation(
                        **_obs_kwargs
                    )
                except TypeError:
                    self._context_manager = self._tracer.client.start_as_current_observation(
                        name=self._name,
                        as_type=v3_as_type,
                        input=self._metadata,
                        metadata=self._metadata,
                    )
                self._observation = self._context_manager.__enter__()
                logger.info("[trace:%s] observation context entered", self._name)

                # Always stamp trace-level identity + name after entering observation
                try:
                    _trace_update: Dict[str, Any] = {}
                    if self._user_id:
                        _trace_update["user_id"] = self._user_id
                    if self._session_id:
                        _trace_update["session_id"] = self._session_id
                    if self._tags:
                        _trace_update["tags"] = self._tags
                    if _trace_update:
                        self._tracer.client.update_current_trace(**_trace_update)
                        logger.info("[trace:%s] trace identity set via update_current_trace()", self._name)
                except Exception as attr_err:
                    logger.warning("Failed to update_current_trace identity: %s", attr_err)

            # --- Langfuse v2 SDK (Legacy) ---
            elif hasattr(self._tracer.client, "trace"):
                logger.info("[trace:%s] using legacy trace().generation() path", self._name)
                _legacy_kwargs: Dict[str, Any] = {
                    "name": self._name,
                    "session_id": self._session_id,
                    "user_id": self._user_id,
                }
                if self._tags:
                    _legacy_kwargs["tags"] = self._tags
                self._trace = self._tracer.client.trace(**_legacy_kwargs)
                self._observation = self._trace.generation(
                    name=self._name,
                    input=self._metadata,
                    metadata=self._metadata,
                )
            else:
                logger.warning("Unsupported Langfuse SDK version.")
                self._observation = None

        except Exception as exc:
            logger.warning("Langfuse observation call failed: %s", exc)
            self._observation = None

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if not self._tracer.enabled or not self._observation:
            logger.info(
                "[trace:%s] exit skipped (enabled=%s observation=%s)",
                self._name,
                self._tracer.enabled,
                bool(self._observation),
            )
            return False

        try:
            usage_metrics = _extract_usage_cost_metrics(self.output)
            context_usage, context_model = _consume_current_usage_metrics()
            if context_usage:
                usage_metrics = {**context_usage, **usage_metrics}
            output_payload = self.output
            if usage_metrics:
                if isinstance(self.output, dict):
                    output_payload = {**self.output, "langfuse_usage": usage_metrics}
                else:
                    output_payload = {"result": self.output, "langfuse_usage": usage_metrics}

            # Capture input_data set during function body (after @observe opened the trace)
            _deferred_input = _CURRENT_INPUT_DATA.get()
            if _deferred_input is not None and "input_data" not in self._metadata:
                try:
                    self._metadata["input_data"] = _sanitize_input_for_trace(_deferred_input)
                except Exception:
                    pass

            # Capture system_prompt and previous_node_output for evaluation context
            _deferred_system_prompt = _CURRENT_SYSTEM_PROMPT.get()
            if _deferred_system_prompt:
                self._metadata["system_prompt"] = _deferred_system_prompt

            _deferred_prev_output = _CURRENT_PREVIOUS_NODE_OUTPUT.get()
            if _deferred_prev_output is not None:
                try:
                    self._metadata["previous_node_output"] = _sanitize_input_for_trace(_deferred_prev_output)
                except Exception:
                    self._metadata["previous_node_output"] = "<sanitize error>"

            if exc_type is not None:
                message = str(exc_val) if exc_val else str(exc_type)
                if hasattr(self._observation, "update"):  # v3 API
                    self._observation.update(level="ERROR", status_message=message)
                else:  # v2 API
                    try:
                        self._observation.end(level="ERROR", status_message=message)
                    except TypeError:
                        self._observation.end(output=None)
            else:
                if hasattr(self._observation, "update"):  # v3 API
                    update_payload: Dict[str, Any] = {"output": output_payload}
                    # Build structured input with system_prompt and previous_node_output
                    _structured_input = {}
                    if "input_data" in self._metadata:
                        _structured_input["user_input"] = self._metadata["input_data"]
                    if _deferred_system_prompt:
                        _structured_input["system_prompt"] = _deferred_system_prompt
                    if _deferred_prev_output is not None:
                        try:
                            _structured_input["previous_node_output"] = _sanitize_input_for_trace(_deferred_prev_output)
                        except Exception:
                            pass
                    if _structured_input:
                        update_payload["input"] = _structured_input
                    elif "input_data" in self._metadata:
                        update_payload["input"] = self._metadata["input_data"]
                    metadata_payload = dict(self._metadata)
                    if usage_metrics:
                        metadata_payload["usage_metrics"] = usage_metrics
                    if metadata_payload:
                        update_payload["metadata"] = metadata_payload

                    if context_model:
                        normalized_model = _normalize_model_for_pricing(context_model)
                        update_payload["model"] = normalized_model
                        metadata_payload = update_payload.get("metadata", {})
                        if isinstance(metadata_payload, dict) and normalized_model != context_model:
                            metadata_payload["model_raw"] = context_model
                            update_payload["metadata"] = metadata_payload

                    if usage_metrics:
                        usage_details = {}
                        if usage_metrics.get("input_tokens") is not None:
                            usage_details["input"] = usage_metrics["input_tokens"]
                        if usage_metrics.get("output_tokens") is not None:
                            usage_details["output"] = usage_metrics["output_tokens"]
                        if usage_metrics.get("total_tokens") is not None:
                            usage_details["total"] = usage_metrics["total_tokens"]
                        if usage_details:
                            update_payload["usage_details"] = usage_details
                        if usage_metrics.get("total_cost_usd") is not None:
                            update_payload["cost_details"] = {
                                "total": usage_metrics["total_cost_usd"],
                                "currency": "USD",
                            }

                    try:
                        self._observation.update(**update_payload)
                        logger.info(
                            "[trace:%s] observation updated usage_keys=%s model=%s",
                            self._name,
                            list(usage_metrics.keys()),
                            context_model,
                        )
                    except TypeError:
                        self._observation.update(output=output_payload)
                else:  # v2 API
                    try:
                        self._observation.end(output=output_payload)
                    except TypeError:
                        self._observation.end(output=output_payload)

            if self._context_manager:
                self._context_manager.__exit__(exc_type, exc_val, exc_tb)
            if self._propagation_context:
                self._propagation_context.__exit__(exc_type, exc_val, exc_tb)

            # Force flush so traces appear instantly
            self._tracer.client.flush()
            logger.info("[trace:%s] flush completed", self._name)

        except Exception as exc:
            logger.warning("Error while finalizing Langfuse observation: %s", exc)
        finally:
            if self._identity_token is not None:
                _CURRENT_TRACE_IDENTITY.reset(self._identity_token)
                self._identity_token = None
            _CURRENT_INPUT_DATA.set(None)
            _CURRENT_SYSTEM_PROMPT.set(None)
            _CURRENT_PREVIOUS_NODE_OUTPUT.set(None)

        return False


_TRACER: Optional[LangfuseTracer] = None


def get_tracer() -> LangfuseTracer:
    """Get a global LangfuseTracer instance for this process."""
    global _TRACER
    if _TRACER is None:
        _TRACER = LangfuseTracer()
    return _TRACER
