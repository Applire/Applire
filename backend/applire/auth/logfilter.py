# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Redaction for the uvicorn access log (ADR-091 cl. 18; US328; ADR-087 cl. 9
amended 2026-11-18).

The access log is written at INFO and carries ids only. Its request line is
rewritten before it is formatted:

* **query parameters:** each name is read the way the application reads it
  (percent-decoded). A value is kept only when the name is on the allow-list
  :data:`STRUCTURED_QUERY_PARAMS` (ids, numbers, dates, enum codes; taken from
  the route table by ``tests/unit/test_access_log_query_census.py``) and the
  value itself has the shape of such a value; every other value becomes
  ``[redacted]``, on every path, a route that does not exist included. A name
  the application does not read is shown as ``[redacted]`` too (it can itself
  be typed text);
* any path segment after ``/api/auth/links/`` → ``[redacted]`` (invite/reset tokens
  travel in the URL fragment and POST bodies by design — cl. 22 — so this is the
  belt to that pair of braces: a client that puts one in the path is not logged);
* anything that looks like a personal token (``apl_<8>_<43>``) anywhere in the line.

nginx writes ``$request_method $uri`` without the query string. Install once per
process with ``install_access_log_redaction()`` (``main.py``); idempotent.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote_plus

REDACTED = "[redacted]"
def _re(pattern: str):
    rx = re.compile(pattern)
    return lambda value: rx.fullmatch(value) is not None


def _one_of(*values: str):
    allowed = frozenset(values)
    return lambda value: value in allowed


def _audit_cursor(value: str) -> bool:
    """``base64url("<ISO time>|<uuid>")`` — the audit page cursor."""
    import base64

    try:
        decoded = base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()).decode()
    except Exception:  # noqa: BLE001 — not a cursor
        return False
    return _DATETIME(decoded.split("|", 1)[0]) and _UUID(decoded.split("|", 1)[-1])


_UUID = _re(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_INT = _re(r"-?\d{1,12}")
_FLOAT = _re(r"-?\d{1,6}(\.\d{1,8})?")
_BOOL = _re(r"(?i:true|false|1|0|yes|no|on|off)")
_DATETIME = _re(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?)?(Z|[+-]\d{2}:?\d{2})?")
_DOCUMENT_STATUS = _one_of("pending", "generating", "ready", "failed", "expired")
_OAUTH_ERROR = _one_of(
    "invalid_request", "unauthorized_client", "access_denied", "unsupported_response_type",
    "invalid_scope", "server_error", "temporarily_unavailable", "interaction_required",
    "login_required", "account_selection_required", "consent_required", "invalid_request_uri",
    "invalid_request_object", "request_not_supported", "request_uri_not_supported",
    "registration_not_supported",
)

#: Query parameters whose values are logged, each with the shape its value must
#: have (after decoding) to be shown: an id, a number, a date, a flag, a code the
#: application defines. Anything else is redacted. The census test keeps the
#: names equal to the route table's structured parameters plus the signed link's.
STRUCTURED_QUERY_PARAMS = {
    "action": _re(r"[a-z_]{1,40}(\.[a-z_]{1,40}){1,3}"),  # audit action names
    "active": _BOOL,
    "actor_id": _UUID,
    "basis_updated_at": _DATETIME,
    "berufsbild_code": _re(r"\d{1,5}"),
    "consent": _BOOL,
    "cursor": _audit_cursor,
    "cx": _FLOAT,
    "cy": _FLOAT,
    "days": _INT,
    "error": _OAUTH_ERROR,
    "exp": _INT,
    "job_id": _UUID,
    "limit": _INT,
    "page": _INT,
    "page_size": _INT,
    "session_id": _UUID,
    "since": _DATETIME,
    "source": _one_of("linkedin"),
    "status": _DOCUMENT_STATUS,
    "target_user_id": _UUID,
    "top_n": _INT,
    "uid": _UUID,
    "until": _DATETIME,
    "user_status": _one_of("tracking", "applied", "interviewing", "offer", "rejected", "hired", "cancelled"),
    "workflow_status": _one_of("none", "analyzing", "interviewing", "cv_generating", "completed"),
    "zoom": _FLOAT,
}
#: Parameters that carry a credential, a page address or typed text. Never on
#: the allow-list (the census test checks it); listed for the reader.
SECRET_QUERY_PARAMS = ("sig", "token", "access_token", "setup_token", "code", "state")
TEXT_QUERY_PARAMS = ("q", "next")

#: Names shown as they are (their values follow the rule above).
_KNOWN_NAMES = frozenset(STRUCTURED_QUERY_PARAMS) | frozenset(SECRET_QUERY_PARAMS) | frozenset(TEXT_QUERY_PARAMS)
#: The query part of a request target inside a line: up to a space or a quote.
_TARGET_QUERY_RE = re.compile(r"\?(?P<query>[^\s\"]*)")
_LINK_PATH_RE = re.compile(r"(?P<lead>/api/auth/links/)[^?#\s\"]+")
_TOKEN_RE = re.compile(r"apl_[a-z0-9]{8}_[A-Za-z0-9_-]{43}")


def _redact_pair(pair: str) -> str:
    if not pair:
        return pair
    raw_name, sep, raw_value = pair.partition("=")
    name = unquote_plus(raw_name)
    if name not in _KNOWN_NAMES:
        # A name the application does not read can itself be typed text.
        return REDACTED + (sep and sep + REDACTED)
    if not sep:
        return name
    shape = STRUCTURED_QUERY_PARAMS.get(name)
    if shape is not None and shape(unquote_plus(raw_value)):
        return f"{name}={raw_value}"
    return f"{name}={REDACTED}"


def _redact_query(match: re.Match) -> str:
    return "?" + "&".join(_redact_pair(p) for p in match.group("query").split("&"))


ACCESS_LOGGER = "uvicorn.access"


def redact(text: str) -> str:
    """The credential-free form of a request target or log line."""
    if not text:
        return text
    text = _TARGET_QUERY_RE.sub(_redact_query, text)
    text = _LINK_PATH_RE.sub(lambda m: m.group("lead") + REDACTED, text)
    return _TOKEN_RE.sub(REDACTED, text)


class AccessLogRedactionFilter(logging.Filter):
    """Rewrites ``record.args`` (uvicorn: client, method, full_path, version, status)
    and ``record.msg``; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                k: (redact(v) if isinstance(v, str) else v) for k, v in record.args.items()
            }
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install_access_log_redaction(logger_name: str = ACCESS_LOGGER) -> AccessLogRedactionFilter:
    """Attach the filter to ``logger_name`` once; returns the installed instance."""
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, AccessLogRedactionFilter):
            return existing
    flt = AccessLogRedactionFilter()
    logger.addFilter(flt)
    return flt
