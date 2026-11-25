# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The edge of a process: what a request that fails, or an error nobody caught,
leaves in the default log (ADR-087 cl. 9 amended 2026-11-18).

* **Request validation** answers 422 with the field and the rule for every body
  a client can send; the value itself is never part of the answer's encoding
  path, so the answer cannot fail to build.
* **An unhandled error in a request** answers 500 with the error id; the
  application log gets the exception type, the route and the id.
* **Records written by code that is not ours** (the ASGI server, the MCP SDK,
  asyncio, a library) at WARNING and above, and any record with an attached
  exception, reach a stream handler as their static head, the exception type,
  the route when there is one and an error id. The full text and traceback go
  to the content logger at DEBUG, where the content-log rules apply.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from applire.content_log import content_logger
from applire.internal_errors import (
    internal_error_detail,
    log_unexpected,
    new_error_id,
)
from applire.redaction import scrub_secrets

_clog = content_logger("applire.log_boundary")

#: Attribute set on an original record once it has been reduced, so a record
#: that passes several handlers is reduced (and its detail written) once.
_REDUCED = "_applire_reduced"
_IS_REDUCED = "_applire_is_reduced"
#: Where the 500 handler leaves the id it logged, for the server's own line.
_STATE_KEY = "error_id"


def _own(name: str) -> bool:
    return name == "applire" or name.startswith("applire.")


def _safe_text(value: Any) -> str:
    """A string that always encodes as UTF-8 (a lone surrogate becomes its escape)."""
    return str(value).encode("utf-8", "backslashreplace").decode("utf-8")


def _scope_from(tb) -> dict | None:
    """The ASGI scope of the request the traceback ran in, if a frame holds it."""
    while tb is not None:
        scope = tb.tb_frame.f_locals.get("scope")
        if isinstance(scope, dict) and scope.get("type") in ("http", "websocket"):
            return scope
        tb = tb.tb_next
    return None


def route_label(scope: dict | None) -> str | None:
    """``METHOD /route/{template}``: the matched route's template, never the raw
    path or query (a path segment of an unmatched address is the client's text)."""
    if not scope:
        return None
    route = scope.get("route")
    template = getattr(route, "path", None) or getattr(route, "path_format", None)
    return f"{scope.get('method', '')} {template or '(no route)'}".strip()


#: MD-G32 (X-11): the argument types a head formats in — the EXACT types, never a
#: subclass (a foreign ``int`` subclass may carry its own ``__str__`` with text). ``bool``
#: is left out on purpose: a flag says nothing an operator needs in the head.
_HEAD_NUMBERS = (int, float)


def _head(record: logging.LogRecord) -> str:
    """The static start of a record: the message template's first line up to its
    first colon, never the formatted arguments (a foreign library may have
    formatted the text in before handing us the record)."""
    msg = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
    args = record.args
    if isinstance(record.msg, str) and isinstance(args, tuple) and args and all(
        type(a) in _HEAD_NUMBERS for a in args
    ):
        # DH item 6 (Sorbet invite-gate): numbers carry no content — uvicorn's
        # "Cancel %s running task(s), timeout graceful shutdown exceeded" printed
        # its count as a bare "%s".
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken format string keeps the template
            pass
    head = msg.split("\n", 1)[0].split(":", 1)[0].strip()
    return head[:160] or record.name


def _frames(tb) -> str:
    try:
        return "".join(traceback.format_tb(tb))
    except Exception:  # noqa: BLE001 — never let logging mask the original error
        return "<traceback unavailable>\n"


def reduce_record(record: logging.LogRecord) -> logging.LogRecord:
    """The record as a stream handler may show it (see the module docstring).

    Returns the record itself when nothing needs reducing, otherwise a copy;
    the original is left for every other handler (and for test capture)."""
    exc_info = record.exc_info if isinstance(record.exc_info, tuple) else None
    foreign = not _own(record.name)
    if not exc_info and not (foreign and record.levelno >= logging.WARNING):
        return record
    if record.__dict__.get(_IS_REDUCED):
        return record
    cached = record.__dict__.get(_REDUCED)
    if cached is not None:
        return cached

    exc = exc_info[1] if exc_info else None
    tb = exc_info[2] if exc_info else None
    scope = _scope_from(tb)
    state = (scope or {}).get("state") or {}
    logged_id = state.get(_STATE_KEY) if isinstance(state, dict) else None
    error_id = logged_id or new_error_id()

    parts = [_head(record) if foreign else record.getMessage()]
    if exc is not None:
        parts.append(f"[{type(exc).__name__}]")
    route = route_label(scope)
    if route:
        parts.append(f"[route={route}]")
    parts.append(f"[error_id={error_id}]")
    message = " ".join(parts)
    if exc is not None and not logged_id:
        message += "\n" + scrub_secrets(_frames(tb)).rstrip("\n")

    try:
        full = record.getMessage()
    except Exception:  # noqa: BLE001 — a broken format string is still a record
        full = str(record.msg)
    if exc_info:
        full += "\n" + "".join(traceback.format_exception(*exc_info))
    if not logged_id:  # the 500 handler already wrote this error's detail
        _clog.debug("[error_id=%s] %s: %s", error_id, record.name, scrub_secrets(full))

    reduced = logging.makeLogRecord(record.__dict__)
    reduced.msg, reduced.args = message, ()
    reduced.exc_info = reduced.exc_text = reduced.stack_info = None
    reduced.__dict__.pop(_REDUCED, None)
    reduced.__dict__[_IS_REDUCED] = True
    record.__dict__[_REDUCED] = reduced
    return reduced


class RecordReducer(logging.Filter):
    """Handler or logger filter applying :func:`reduce_record`."""

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:  # type: ignore[override]
        try:
            return reduce_record(record)
        except Exception:  # noqa: BLE001 — a filter must not drop the record silently
            out = logging.makeLogRecord(record.__dict__)
            out.msg, out.args = f"{record.name}: record withheld [{record.levelname}]", ()
            out.exc_info = out.exc_text = out.stack_info = None
            return out


RECORD_REDUCER = RecordReducer()

#: Loggers of the ASGI server whose records do not pass one of our handlers.
SERVER_LOGGERS = ("uvicorn.error",)


def install(*targets: logging.Handler | logging.Logger) -> None:
    """Attach the reducer to stream handlers / foreign loggers (idempotent)."""
    for target in targets:
        if RECORD_REDUCER not in target.filters:
            target.addFilter(RECORD_REDUCER)


def install_for_web() -> None:
    """The web process: the server's own logger and Python's last-resort handler
    (records of a logger without handlers, e.g. ``asyncio``)."""
    install(*(logging.getLogger(n) for n in SERVER_LOGGERS))
    if logging.lastResort is not None:
        install(logging.lastResort)


# --- the request side ------------------------------------------------------------

#: Context keys a validation answer may carry: a limit, never the value.
_CTX_SCALARS = (int, float, bool)


def safe_validation_errors(errors: list[dict]) -> list[dict]:
    """Validation errors without the value: ``type``, ``loc``, ``msg`` and the
    scalar limits from ``ctx`` (``max_length`` …)."""
    out: list[dict] = []
    for err in errors:
        item: dict[str, Any] = {
            "type": _safe_text(err.get("type", "")),
            "loc": [p if isinstance(p, int) else _safe_text(p) for p in err.get("loc", ())],
            "msg": _safe_text(err.get("msg", "")),
        }
        ctx = err.get("ctx")
        if isinstance(ctx, dict):
            limits = {
                _safe_text(k): v for k, v in ctx.items()
                if isinstance(v, _CTX_SCALARS) or (isinstance(v, str) and k in ("pattern",))
            }
            if limits:
                item["ctx"] = {k: (_safe_text(v) if isinstance(v, str) else v) for k, v in limits.items()}
        out.append(item)
    return out


async def request_validation_handler(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=422, content={"detail": safe_validation_errors(exc.errors())})


async def unhandled_error_handler(request, exc):
    """The 500 answer for an error no route caught: id in the body and the log."""
    from fastapi.responses import JSONResponse

    error_id = log_unexpected(exc, where=route_label(request.scope) or "request")
    request.scope.setdefault("state", {})[_STATE_KEY] = error_id
    return JSONResponse(status_code=500, content={"detail": internal_error_detail(error_id)})


def install_request_boundary(app) -> None:
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
