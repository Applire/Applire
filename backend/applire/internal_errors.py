# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Error detail hardening: one answer for an error no handler classified.

A route or tool that catches an exception it has no specific mapping for
answers with a static, catalog-friendly message plus a short error id. The
exception's type and stack frames go to the application log under the same
id, so an operator can match a user's report to the log line; its message
(scrubbed by :func:`applire.redaction.scrub_secrets`) is written on the
content logger at DEBUG.

Messages we wrote ourselves (a 404's "not found", a 422's validation text, the
``jd_url_invalid`` body …) are unaffected: this module is only for the
catch-all branch.
"""

from __future__ import annotations

import logging
import traceback
import uuid

from applire.content_log import content_logger
from applire.redaction import scrub_secrets

#: The REST catch-all body's ``error_code`` (the #256 convention).
INTERNAL_ERROR_CODE = "internal_error"
INTERNAL_ERROR_MESSAGE = "An unexpected error occurred. Please try again."

_log = logging.getLogger("applire.internal_errors")
_clog = content_logger("applire.internal_errors")


def new_error_id() -> str:
    """A short, unguessable id that ties a response to its log line."""
    return uuid.uuid4().hex[:12]


def log_unexpected(
    exc: BaseException,
    *,
    where: str,
    logger: logging.Logger | None = None,
    error_id: str | None = None,
) -> str:
    """Log ``exc`` and return the error id.

    The application log carries the id, the call site, the exception type and
    the stack frames; the exception's message (which can hold text) is written
    scrubbed on the content logger at DEBUG.
    """
    error_id = error_id or new_error_id()
    try:
        frames = "".join(traceback.format_tb(exc.__traceback__))
    except Exception:  # noqa: BLE001 — never let logging mask the original error
        frames = "<traceback unavailable>"
    (logger or _log).error(
        "unexpected error [error_id=%s] in %s: %s\n%s",
        error_id, where, type(exc).__name__, scrub_secrets(frames),
    )
    try:
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:  # noqa: BLE001
        trace = f"{type(exc).__name__}: <traceback unavailable>"
    _clog.debug("unexpected error [error_id=%s] in %s: %s", error_id, where, scrub_secrets(trace))
    return error_id


def internal_error_detail(error_id: str) -> dict[str, str]:
    """The REST catch-all body: ``{error_code, message, error_id}``."""
    return {
        "error_code": INTERNAL_ERROR_CODE,
        "message": INTERNAL_ERROR_MESSAGE,
        "error_id": error_id,
    }


def internal_server_error(
    exc: BaseException, *, where: str, logger: logging.Logger | None = None
):
    """Log ``exc`` and return the ``HTTPException(500)`` to raise in its place.

    Usage: ``except Exception as exc: raise internal_server_error(exc, where="…")``.
    An admission refusal is returned as it is, so it answers as the refusal.
    """
    from fastapi import HTTPException, status

    from applire.services.allowance import AdmissionRefused

    if isinstance(exc, AdmissionRefused):
        return exc  # a refusal keeps its own answer (ADR-096 cl. 11)
    error_id = log_unexpected(exc, where=where, logger=logger)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=internal_error_detail(error_id),
    )


def _retry_prefix(exc: BaseException) -> str:
    """788:134 (Hazelnut build 7, WP-O): a stable, typed head for the provider-side
    failures an agent may retry — FastMCP drops ``McpError.data``, so the code has
    to ride the message text. ``"<code> (safe to retry — …): "`` or ``""`` (RULING O-2)."""
    from applire.exceptions import (
        LLMProviderUnavailableError,
        LLMRateLimitError,
        LLMTimeoutError,
        LLMTruncatedError,
        is_provider_outage,
    )

    if isinstance(exc, LLMTimeoutError):
        code = "provider_timeout"
    elif isinstance(exc, LLMRateLimitError):
        code = "provider_rate_limited"
    elif isinstance(exc, LLMTruncatedError):
        code = "llm_truncated"
    elif isinstance(exc, LLMProviderUnavailableError) or is_provider_outage(exc):
        code = "provider_unavailable"
    else:
        return ""
    return f"{code} (safe to retry — send the same call again once): "


def agent_facing_message(exc: BaseException, *, where: str) -> str:
    """The text an agent sees for an exception the tool did not classify.

    Our own LLM error types carry messages we wrote (a timeout, a rate limit, a
    truncation, a provider failure by type and status) that tell the agent
    whether to retry, and a reverted vault write says that nothing was stored;
    those pass, scrubbed. Anything else becomes the static message plus the
    error id, and the exception goes to the log.
    """
    from applire.exceptions import LLMError
    from applire.services.profile.reconcile.apply import VaultWriteRevertedError

    if isinstance(exc, (LLMError, VaultWriteRevertedError)):
        log_unexpected(exc, where=where)
        return _retry_prefix(exc) + scrub_secrets(str(exc))
    error_id = log_unexpected(exc, where=where)
    return f"{INTERNAL_ERROR_MESSAGE} (error id {error_id})"
