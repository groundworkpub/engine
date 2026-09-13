"""Shared resilience utilities for all Groundwork agents (D4).

Provides:
- `resilient_task`: replace bare `except Exception` with structured, redacted
  logging while re-raising so callers still see failures.
- `safe_api_call`: lightweight wrapper for one-shot best-effort API calls that
  should degrade gracefully instead of crashing a pipeline.

Uses structlog when available, falling back to stdlib logging otherwise.
"""

from __future__ import annotations

import functools
import logging
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover - optional dependency
    structlog = None  # type: ignore[assignment]
    _HAS_STRUCTLOG = False

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Secrets/credential patterns we must never persist in logs.
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|authorization|password)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(token|secret|apikey|api_key)\s+\S+"),
    re.compile(r"Bearer\s+\S+"),
]


def redact_message(text: str) -> str:
    """Redact likely credential substrings before logging (Axiom VI)."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(r"\1=[REDACTED]", out)
    return out


def _log_error(**event_kw: Any) -> None:
    event = event_kw.pop("event", "task_failed")
    if _HAS_STRUCTLOG:
        structlog.get_logger().error(event, **event_kw)
    else:
        logger.error("%s %s", event, event_kw, exc_info=event_kw.pop("exc_info", False))


def resilient_task(func: F) -> F:
    """Replace bare `except Exception` with structured logging.

    Usage::

        @resilient_task
        def process_article(article_id: str) -> dict:
            ...
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            _log_error(
                "task_failed",
                task=func.__name__,
                error=redact_message(str(exc)),
                error_type=type(exc).__name__,
                duration_seconds=round(time.monotonic() - start, 3),
                exc_info=True,
            )
            raise

    return wrapper  # type: ignore[return-value]


def safe_api_call(func: F) -> F:
    """Best-effort wrapper: logs and swallows errors, returning a default.

    Intended for optional integrations (bing ping, webmention, satellite) that
    must never take down the main pipeline on a transient failure.

    NOTE: the wrapped function should itself return a sensible default (e.g.
    ``None`` / ``[]`` / ``{}``) when it fails; this wrapper only guarantees the
    error is structured-logged and not raised.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            _log_error(
                "api_call_degraded",
                task=func.__name__,
                error=redact_message(str(exc)),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return None

    return wrapper  # type: ignore[return-value]
