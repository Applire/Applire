# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed cover letter carries a stable code, never the provider's raw text.

Hazelnut build 7, WP-O (788:134, MD7-5). The CV path classifies its failures
since PQ F6 (``services.cv.classify_generation_error`` + one user-safe sentence
per ``error_code``, ADR-047 §4). The letter path stored ``str(exc)`` instead —
the agent read "Requesty call timed out after 180s" (a vendor name, no retry
advice) and the UI had nothing to localise from.

Letters have no ``error_code`` column and this build has no migration id for one,
so the CODE travels inside the stored sentence: the catch site writes
:func:`letter_failure_message` (one fixed English sentence per code), and the
status door reads it back with :func:`letter_error_code`. The mapping is exact
and both directions live here, so they cannot drift. A legacy row (raw provider
text from before this build) reads as ``generation_failed`` and is answered with
that code's sentence — the raw text never leaves the backend (it stays in the
server log, where the catch site's ``logger.exception`` already put it).
"""

from __future__ import annotations

#: The stable codes, shared with the CV path (``services.cv.classify_generation_error``).
RETRYABLE_CODES = frozenset({"llm_timeout", "provider_unavailable", "llm_truncated"})

_LETTER_MESSAGES: dict[str, str] = {
    "llm_timeout": "The AI provider did not answer in time. Generate the cover letter again.",
    "provider_unavailable": (
        "The AI provider could not be reached. Generate the cover letter again in a moment."
    ),
    "llm_truncated": (
        "The AI provider's answer was cut off before the cover letter was complete. "
        "Generate it again."
    ),
    "generation_failed": "The cover letter could not be generated. Generate it again.",
    # ADR-096 cl. 11.
    "provider_key_required": (
        "Add your own AI provider key in Settings, or ask your admin to let you use "
        "this instance's key."
    ),
    "provider_key_unreadable": (
        "Your stored AI provider key cannot be read on this server right now. Ask your admin."
    ),
}
_CODE_BY_MESSAGE = {message: code for code, message in _LETTER_MESSAGES.items()}


def letter_failure_message(exc: BaseException) -> str:
    """The sentence a failed letter row stores: classified, user-safe, English."""
    from applire.services.cv import classify_generation_error

    return _LETTER_MESSAGES.get(classify_generation_error(exc), _LETTER_MESSAGES["generation_failed"])


def letter_error_code(stored_message: str | None) -> str:
    """The code behind a stored failure sentence; anything else is ``generation_failed``."""
    return _CODE_BY_MESSAGE.get((stored_message or "").strip(), "generation_failed")


def letter_error_sentence(code: str) -> str:
    """The user-safe sentence for ``code`` (unknown codes: the generic one)."""
    return _LETTER_MESSAGES.get(code, _LETTER_MESSAGES["generation_failed"])


def is_retryable(code: str | None) -> bool:
    """A provider-side failure: the same request, sent again unchanged, may succeed."""
    return code in RETRYABLE_CODES
