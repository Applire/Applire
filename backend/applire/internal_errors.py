# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Error detail hardening: one answer for an error no handler classified.

A route or tool that catches an exception it has no specific mapping for
answers with a static, catalog-friendly message plus a short error id. The
exception itself — type, message, traceback — goes to the server log only,
scrubbed by :func:`applire.redaction.scrub_secrets`, under the same id, so an
operator can match a user's report to the log line.

Messages we wrote ourselves (a 404's "not found", a 422's validation text, the
``jd_url_invalid`` body …) are unaffected: this module is only for the
catch-all branch.
"""

from __future__ import annotations

import logging
import traceback
import uuid

from applire.redaction import scrub_secrets

#: The REST catch-all body's ``error_code`` (the #256 convention).
INTERNAL_ERROR_CODE = "internal_error"
INTERNAL_ERROR_MESSAGE = "An unexpected error occurred. Please try again."

_log = logging.getLogger("applire.internal_errors")


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
    """Log ``exc`` (scrubbed, with its traceback) and return the error id."""
    error_id = error_id or new_error_id()
    try:
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:  # noqa: BLE001 — never let logging mask the original error
        trace = f"{type(exc).__name__}: <traceback unavailable>"
    (logger or _log).error(
        "unexpected error [error_id=%s] in %s: %s",
        error_id, where, scrub_secrets(trace),
    )
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
    """
    from fastapi import HTTPException, status

    error_id = log_unexpected(exc, where=where, logger=logger)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=internal_error_detail(error_id),
    )


def agent_facing_message(exc: BaseException, *, where: str) -> str:
    """The text an agent sees for an exception the tool did not classify.

    Our own LLM error types carry messages we wrote (a timeout, a rate limit, a
    truncation, a provider failure by type and status) that tell the agent
    whether to retry; those pass, scrubbed. Anything else becomes the static
    message plus the error id, and the exception goes to the log.
    """
    from applire.exceptions import LLMError

    if isinstance(exc, LLMError):
        log_unexpected(exc, where=where)
        return scrub_secrets(str(exc))
    error_id = log_unexpected(exc, where=where)
    return f"{INTERNAL_ERROR_MESSAGE} (error id {error_id})"
