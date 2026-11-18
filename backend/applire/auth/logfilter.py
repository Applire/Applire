# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Redaction for the uvicorn access log (ADR-091 cl. 18; US328; ADR-087 cl. 9
amended 2026-11-18).

The access log is written at INFO and carries ids only. Its request line is
rewritten before it is formatted:

* **query parameters:** each name is read the way the application reads it
  (percent-decoded). A value is shown only when the name is on the allow-list
  :data:`STRUCTURED_QUERY_PARAMS` (ids, numbers, dates, enum codes; taken from
  the route table by ``tests/unit/test_access_log_query_census.py``) and the
  whole decoded value, at most :data:`MAX_SHOWN_VALUE` characters, fully
  matches that parameter's strict ASCII grammar or closed set. What is shown is
  that checked value, never the raw form, so nothing the check did not read
  reaches the line. Every other value becomes ``[redacted]``, on every path, a
  route that does not exist included. A name the application does not read is
  shown as ``[redacted]`` too (it can itself be typed text);
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
#: A longer value is never shown (the longest structured value, an audit
#: cursor, is about 95 characters).
MAX_SHOWN_VALUE = 128


def _re(pattern: str):
    # ASCII: ``\d`` must not match other scripts' digits.
    rx = re.compile(pattern, re.ASCII)
    return lambda value: rx.fullmatch(value) is not None


def _one_of(*values: str):
    allowed = frozenset(values)
    return lambda value: value in allowed


_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}", re.ASCII)


def _audit_cursor(value: str) -> bool:
    """``base64url("<ISO time>|<uuid>")`` — the audit page cursor
    (``services/admin/audit_view.encode_cursor``). Strict: only the base64url
    alphabet, a strict decode, and the decoded text encodes back to exactly the
    value, so no character can ride along that the decode skipped."""
    import base64
    import binascii

    if not _BASE64URL.fullmatch(value):
        return False
    bare = value.rstrip("=")
    try:
        raw = base64.b64decode(bare + "=" * (-len(bare) % 4), altchars=b"-_", validate=True)
        decoded = raw.decode("ascii")
    except (binascii.Error, ValueError):
        return False
    if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != bare:
        return False
    at, sep, row_id = decoded.partition("|")
    return bool(sep) and _DATETIME(at) and _UUID(row_id)


def _audit_action(value: str) -> bool:
    from applire.services.audit import ACTIONS

    return value in ACTIONS


_UUID = _re(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32}")
_INT = _re(r"-?\d{1,12}")
_FLOAT = _re(r"-?\d{1,6}(\.\d{1,8})?")
_BOOL = _re(r"(?i:true|false|1|0|yes|no|on|off)")
_DATETIME = _re(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:?\d{2})?)?")
_DOCUMENT_STATUS = _one_of("pending", "generating", "ready", "failed", "expired")
_OAUTH_ERROR = _one_of(
    "invalid_request", "unauthorized_client", "access_denied", "unsupported_response_type",
    "invalid_scope", "server_error", "temporarily_unavailable", "interaction_required",
    "login_required", "account_selection_required", "consent_required", "invalid_request_uri",
    "invalid_request_object", "request_not_supported", "request_uri_not_supported",
    "registration_not_supported",
)

#: Query parameters whose values are logged, each with the strict grammar or
#: closed set its whole decoded value must match to be shown: an id, a number, a
#: date, a flag, a code the application defines. Anything else is redacted. The census test keeps the
#: names equal to the route table's structured parameters plus the signed link's.
STRUCTURED_QUERY_PARAMS = {
    "action": _audit_action,  # the audit action names (services/audit.ACTIONS)
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
    if shape is None or len(raw_value) > 3 * MAX_SHOWN_VALUE:
        return f"{name}={REDACTED}"
    try:
        value = unquote_plus(raw_value, errors="strict")
    except UnicodeDecodeError:
        return f"{name}={REDACTED}"
    if len(value) <= MAX_SHOWN_VALUE and shape(value):
        return f"{name}={value}"  # the checked value, not the raw form
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
