# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Error detail hardening: scrub secrets from text bound for a log or a response.

One function, :func:`scrub_secrets`, used wherever exception text leaves the
code that raised it — the server log, a stored ``error_message``, an HTTP
``detail``, an MCP error message. It removes

* every value of a configured secret setting (``*_api_key``, ``*_secret``,
  ``*_password``, ``*_token``), in its plain form and in its ``repr``/bytes
  escaped forms (exception texts often quote values through ``repr``);
* credential-shaped tokens whatever their source: ``Bearer <token>``,
  ``sk-…`` keys, and a quoted header value in an HTTP client's error text.

It is a deny-list and therefore a second line, never the only one: code that
answers a caller with an unexpected error returns a static message plus an
error id (:mod:`applire.internal_errors`) and does not rely on this scrub.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

REDACTED = "[redacted]"

#: Settings whose values are secrets, by field-name suffix.
_SECRET_FIELD_SUFFIXES = ("_api_key", "_secret", "_password", "_token")
#: Shorter values are not scrubbed by value (a default like "local" would
#: otherwise blank out ordinary words); the shape patterns still apply.
_MIN_SECRET_LEN = 8

_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # An HTTP client error can quote a request header value verbatim.
    (re.compile(r"(?i)(illegal header value\s*)b?(['\"]).*?\2"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(bearer)\s+[^\s'\"<>,;]+"), r"\1 " + REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{10,}"), REDACTED),
)


def _escaped_forms(value: str) -> set[str]:
    forms = {value, repr(value)[1:-1]}
    try:
        forms.add(repr(value.encode("utf-8", "backslashreplace"))[2:-1])
    except Exception:  # noqa: BLE001 — a form we cannot build is a form nobody printed
        pass
    return {f for f in forms if len(f) >= _MIN_SECRET_LEN}


def _candidate_values(name: str) -> list[Any]:
    """Every value ``name`` may hold right now: the effective one in this
    context, the environment / ``.env`` one, and the admin override (ADR-093) in
    the process-wide latest snapshot. A call pinned to an older snapshot, or an
    env key shadowed by an override, is still a secret worth scrubbing."""
    from applire import config

    settings = config.settings
    found: list[Any] = [getattr(settings, name, None)]
    env_value = getattr(settings, "env_value", None)
    if callable(env_value):
        found.append(env_value(name))
    overlay_latest = getattr(config, "overlay_latest", None)
    if callable(overlay_latest):
        found.append(overlay_latest().get(name))
    return found


#: ADR-096 cl. 3: a user key being validated before it is stored (the key test)
#: is in no payer decision yet; it is scrubbed while the test runs.
_scoped_user_keys: ContextVar[tuple[str, ...]] = ContextVar("applire_scoped_user_keys", default=())


@contextmanager
def user_key_in_scope(key: str) -> Iterator[None]:
    token = _scoped_user_keys.set((*_scoped_user_keys.get(), key))
    try:
        yield
    finally:
        _scoped_user_keys.reset(token)


def _user_key_values() -> list[str]:
    """ADR-096 cl. 3: the current payer decision's user key, the keys this
    process decrypted recently, and a key under test — scrubbed like any
    configured secret, from HTTP details, MCP messages, logs and job errors."""
    values: list[str] = list(_scoped_user_keys.get())
    try:
        from applire.services.allowance import secret_values_in_context

        values.extend(secret_values_in_context())
    except Exception:  # noqa: BLE001 — a scrub may never fail on an import
        pass
    return values


def configured_secret_values() -> list[str]:
    """Every configured secret value (and its escaped forms), longest first —
    from the environment and from the instance-settings overlay alike, plus the
    user provider keys in play (ADR-096 cl. 3)."""
    from applire.config import settings

    values: set[str] = set()
    for name in type(settings).model_fields:
        if not name.endswith(_SECRET_FIELD_SUFFIXES):
            continue
        for value in _candidate_values(name):
            if isinstance(value, str) and len(value.strip()) >= _MIN_SECRET_LEN:
                values |= _escaped_forms(value)
                values |= _escaped_forms(value.strip())
    for value in _user_key_values():
        if isinstance(value, str) and len(value.strip()) >= _MIN_SECRET_LEN:
            values |= _escaped_forms(value)
            values |= _escaped_forms(value.strip())
    return sorted(values, key=len, reverse=True)


def scrub_secrets(text: Any) -> Any:
    """``text`` with every configured secret and credential-shaped token redacted.

    Non-strings are returned unchanged. Never raises.
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        for value in configured_secret_values():
            if value in text:
                text = text.replace(value, REDACTED)
        for pattern, replacement in _SHAPE_PATTERNS:
            text = pattern.sub(replacement, text)
    except Exception:  # noqa: BLE001 — a scrub that fails must not mask the caller's error
        return REDACTED
    return text


def scrub_detail(detail: Any) -> Any:
    """:func:`scrub_secrets` applied to every string inside a JSON-like value."""
    if isinstance(detail, str):
        return scrub_secrets(detail)
    if isinstance(detail, dict):
        return {k: scrub_detail(v) for k, v in detail.items()}
    if isinstance(detail, (list, tuple)):
        return type(detail)(scrub_detail(v) for v in detail)
    return detail


class SecretRedactionFilter(logging.Filter):
    """Scrubs a log record's message and traceback before any handler formats it.

    Never drops a record. Installed on the ``applire`` handler and on
    ``uvicorn.error`` by ``main.py``.
    """

    _formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            scrubbed = scrub_secrets(message)
            if scrubbed != message:
                record.msg = scrubbed
                record.args = None
            if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
                record.exc_text = self._formatter.formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = scrub_secrets(record.exc_text)
            if record.stack_info:
                record.stack_info = scrub_secrets(record.stack_info)
        except Exception:  # noqa: BLE001 — logging must never break a request
            pass
        return True


def install_secret_redaction(*targets: logging.Logger | logging.Handler) -> None:
    """Attach one :class:`SecretRedactionFilter` to each target; idempotent."""
    for target in targets:
        if not any(isinstance(f, SecretRedactionFilter) for f in target.filters):
            target.addFilter(SecretRedactionFilter())
