# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Content logs: one switch, their own logger tree, their own files.

**One switch.** ``LOG_LEVEL=DEBUG`` is the only setting that writes *content*
(profile, posting, answer or document text, and anything an LLM sent or
returned). At ``DEBUG`` two logs exist, each line carrying the user id:

* the application's content lines — every logger under :data:`CONTENT_ROOT`
  (``applire.content.*``), written as JSON lines to
  ``<CONTENT_LOG_DIR>/<YYYY-MM-DD>.jsonl``;
* the LLM call log (``providers/llm/debug_log.py``) in ``LLM_DEBUG_LOG_DIR``.

At ``INFO`` and above neither exists: the content tree is disabled and the
provider is not wrapped. A normal ``applire.*`` line carries ids, counts and
check names only, at every level.

**Never on stdout.** The content tree does not propagate to the ``applire``
stream handler, so a content line never reaches the container's output (which
Docker keeps in files the retention worker cannot reach). It reaches exactly one
handler: :class:`ContentFileHandler`.

**Only for a person who agreed.** A record whose owner (ADR-092 owner context)
has not consented to content logging is dropped by the writer itself, whatever
code emitted it (:func:`owner_may_be_logged`). Ownerless work is written with
``user_id: null``.

How to write a content line::

    from applire.content_log import content_logger
    clog = content_logger(__name__)
    clog.debug("skill spelling guard: %r has no unambiguous vault original", name)

Keep the check name in the format string and the text in the arguments: when a
person withdraws, the retention worker keeps the format string (``template``)
and drops everything else.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

try:  # POSIX only; on other platforms the in-process lock is all there is
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

CONTENT_ROOT = "applire.content"
_DISABLED = logging.CRITICAL + 10

_write_lock = threading.Lock()


#: The shape of a label the default log may carry: a snake_case identifier.
_LABEL_SHAPE = re.compile(r"[a-z][a-z0-9_]{0,39}")
#: What :func:`label` prints in place of anything else.
OTHER = "<other>"
_QUOTED = re.compile(r"""(["'])([a-z][a-z0-9_]{0,39})\1""")
_vocabulary: frozenset[str] | None = None
_vocabulary_lock = threading.Lock()


def code_labels() -> frozenset[str]:
    """Every quoted snake_case word in the ``applire`` package's source: the
    words the code itself uses as statuses, kinds, ops, fields and check
    names. Built once per process (a text scan, well under a second)."""
    global _vocabulary
    if _vocabulary is None:
        with _vocabulary_lock:
            if _vocabulary is None:
                from pathlib import Path

                words: set[str] = set()
                for path in Path(__file__).resolve().parent.rglob("*.py"):
                    try:
                        source = path.read_text("utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    words.update(m.group(2) for m in _QUOTED.finditer(source))
                _vocabulary = frozenset(words)
    return _vocabulary


def label(value: Any, known: Any = None) -> Any:
    """``value`` when the default log may carry it, else ``"<other>"``.

    For a value that comes from a model reply, a request body or a parsed
    document and is EXPECTED to be a label (status, kind, op, field): with
    ``known`` it must be a member; without, it must be a snake_case word the
    code itself spells as a literal (:func:`code_labels`). Numbers, booleans
    and ``None`` pass. The raw value belongs on the content logger.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if known is not None:
        try:
            return value if value in known else OTHER
        except TypeError:
            return OTHER
    if isinstance(value, str) and _LABEL_SHAPE.fullmatch(value) and value in code_labels():
        return value
    return OTHER


def content_logger(name: str) -> logging.Logger:
    """The content logger for module ``name`` (``applire.content.<module>``)."""
    short = name[len("applire.") :] if name.startswith("applire.") else name
    return logging.getLogger(f"{CONTENT_ROOT}.{short}")


def content_logging_on() -> bool:
    """True when this instance writes content logs: ``LOG_LEVEL`` is ``DEBUG``."""
    from applire.config import settings

    return str(settings.log_level or "").strip().upper() == "DEBUG"


def owner_may_be_logged() -> tuple[bool, str | None]:
    """May a content record be written in the current context, and for whom?

    * ownerless (no owner context, or a declared ``unscoped`` reason): yes,
      ``user_id`` ``None``;
    * an owner whose payer facts (loaded at the auth door, ADR-096) say the
      person has consented: yes, with their id;
    * any other owner — no facts for them, or no consent: **no**.
    """
    from applire.ownership import current_owner

    owner = current_owner()
    if owner is None or owner.user_id is None:
        return True, None
    from applire.services.allowance import current_facts

    facts = current_facts()
    if facts is None or facts.owner_id != owner.user_id or not facts.log_consent_ok:
        return False, str(owner.user_id)
    return True, str(owner.user_id)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def append_jsonl(directory: str, record: dict[str, Any]) -> None:
    """Append one JSON line to ``<directory>/<today>.jsonl``.

    Holds an exclusive ``flock`` while appending and re-opens when the retention
    worker replaced the file meanwhile (the worker takes the same lock on the
    file it rewrites), so a line is never appended to a file that was just
    unlinked. Never raises: logging must not break a request.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, _today() + ".jsonl")
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _write_lock:
            for _ in range(3):
                with open(path, "a", encoding="utf-8") as fh:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    try:
                        same = os.fstat(fh.fileno()).st_ino == os.stat(path).st_ino
                    except FileNotFoundError:
                        same = False
                    if not same:
                        continue  # replaced while we waited: open the new file
                    fh.write(line)
                    fh.flush()
                    return
    except Exception:  # noqa: BLE001 — a log line must never break a request
        pass


class ContentFileHandler(logging.Handler):
    """Writes ``applire.content.*`` records to ``<CONTENT_LOG_DIR>/<date>.jsonl``."""

    def __init__(self, directory: str | None = None) -> None:
        super().__init__(level=logging.DEBUG)
        self._directory = directory

    @property
    def directory(self) -> str:
        if self._directory:
            return self._directory
        from applire.config import settings

        return settings.content_log_dir or "logs/content"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            allowed, user_id = owner_may_be_logged()
            if not allowed:
                return
            from applire.redaction import scrub_secrets

            try:
                message = scrub_secrets(record.getMessage())
            except Exception:  # noqa: BLE001
                message = "[unformattable record withheld]"
            append_jsonl(
                self.directory,
                {
                    "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "user_id": user_id,
                    "template": str(record.msg),
                    "message": message,
                },
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


def _content_root() -> logging.Logger:
    root = logging.getLogger(CONTENT_ROOT)
    root.propagate = False
    return root


class _InfoAndAbove(logging.Filter):
    """A stream handler carries INFO and above only: any DEBUG record, ours or
    a library's (an SDK's request dump), may hold what a person wrote."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.INFO


STREAM_FILTER = _InfoAndAbove()


class _DebugToContent(logging.Handler):
    """At DEBUG, an ``applire.*`` DEBUG record outside the content tree is
    written to the content file like a content line: with the user id, only
    for a person who consented."""

    def __init__(self, target: ContentFileHandler) -> None:
        super().__init__(level=logging.DEBUG)
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.INFO and not record.name.startswith(CONTENT_ROOT):
            self.target.handle(record)


def configure_content_logging(*stream_handlers: logging.Handler) -> bool:
    """Apply the switch; returns whether content logs are on.

    Called once per process (web: ``main.py``; MCP stdio: ``mcp/__main__.py``)
    with the process's stream handlers, which then carry INFO and above at
    every level. At DEBUG the ``applire.content`` tree and every other
    ``applire.*`` DEBUG record go to the content file. Idempotent.
    """
    code_labels()  # built here, not on the first request that logs a label
    for handler in stream_handlers:
        if STREAM_FILTER not in handler.filters:
            handler.addFilter(STREAM_FILTER)
    root = _content_root()
    app = logging.getLogger("applire")
    for handler in [h for h in root.handlers if isinstance(h, ContentFileHandler)]:
        root.removeHandler(handler)
    for handler in [h for h in app.handlers if isinstance(h, _DebugToContent)]:
        app.removeHandler(handler)
    if content_logging_on():
        file_handler = ContentFileHandler()
        root.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
        app.addHandler(_DebugToContent(file_handler))
        return True
    root.setLevel(_DISABLED)
    return False


@contextlib.contextmanager
def capturing() -> Iterator[list[logging.LogRecord]]:
    """Test seam: enable the content tree and collect its records (no file)."""
    root = _content_root()
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.DEBUG)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


# Safe default at import: the tree never propagates, and is off until a process
# configures it from LOG_LEVEL.
_content_root().setLevel(_DISABLED)
