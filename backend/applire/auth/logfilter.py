# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Credential redaction for the uvicorn access log (ADR-091 cl. 18; US328).

A signed document link is a bearer capability for up to 60 minutes; the access
log would otherwise keep every ``sig=`` the agent ever opened. This filter
rewrites the request line before it is formatted:

* query values of ``sig``, ``token``, ``access_token``, ``setup_token`` and the sign-in
  parameters ``code`` and ``state`` (the identity provider's callback) → ``[redacted]``
  (``exp`` and ``uid`` stay — they are not secrets and they make a log line useful);
* any path segment after ``/api/auth/links/`` → ``[redacted]`` (invite/reset tokens
  travel in the URL fragment and POST bodies by design — cl. 22 — so this is the
  belt to that pair of braces: a client that puts one in the path is not logged);
* anything that looks like a personal token (``apl_<8>_<43>``) anywhere in the line;
* query values that are text a person typed or a page address (``q``, the
  applications search; ``next``, where sign-in returns to) → ``[redacted]``, the whole
  value: the access log is written at INFO and carries ids only (ADR-087 cl. 9
  amended 2026-11-18).

Every string query parameter of the API is in one of the two lists or named as a
structured value in ``tests/unit/test_access_log_query_census.py``.

nginx gets the matching ``log_format`` (``$request_method $uri``, no args) in
package 5a. Install once per process with ``install_access_log_redaction()``
(``main.py`` lifespan/import — NEEDS-EDIT 1c → 1a); idempotent.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[redacted]"
SECRET_QUERY_PARAMS = ("sig", "token", "access_token", "setup_token", "code", "state")
#: Query parameters whose value is free text a person entered, or a page address.
TEXT_QUERY_PARAMS = ("q", "next")

_QUERY_RE = re.compile(
    r"(?P<lead>[?&](?:" + "|".join(SECRET_QUERY_PARAMS + TEXT_QUERY_PARAMS) + r")=)[^&#\s\"]*",
    re.IGNORECASE,
)
_LINK_PATH_RE = re.compile(r"(?P<lead>/api/auth/links/)[^?#\s\"]+")
_TOKEN_RE = re.compile(r"apl_[a-z0-9]{8}_[A-Za-z0-9_-]{43}")

ACCESS_LOGGER = "uvicorn.access"


def redact(text: str) -> str:
    """The credential-free form of a request target or log line."""
    if not text:
        return text
    text = _QUERY_RE.sub(lambda m: m.group("lead") + REDACTED, text)
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
