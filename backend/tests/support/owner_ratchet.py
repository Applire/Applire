# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The 0-production-fallback ratchet (ADR-092 cl. 8; MD-21, MD-24 (2); SF-OWN.1/.3).

Public service functions keep ``user_id=None`` (MD-21): ``None`` resolves to the
*user* owner context and raises without one, so the fallback is never wrong —
only implicit. MD-24 (2) keeps it and instead holds the production doors at
**zero** fallback hits and **zero** context-derived owner fills: a door must name
the owner explicitly (``user_id=`` / the row's ``user_id``).

How a hit counts as a *door* (the w2-int instrumented run, made permanent): the
call stack at the moment of the fallback — for a fill, at the moment the row was
CONSTRUCTED (the flush runs in SQLAlchemy's greenlet, whose stack ends there) — passes FastAPI/Starlette (a real HTTP request
through the ASGI app, its ``BackgroundTasks`` included) or the MCP tool wrapper
(``applire/mcp/server.py:wrapper`` — every agent tool and resource). A test that
calls a service, a route function or a background runner *directly* is
"test-direct" and allowed (the ~1600 test call sites MD-24 (2) leaves unedited).

Residual (documented, not covered here): a task started with
``asyncio.create_task`` from a door runs on a fresh stack without the door's
frames, so its fallbacks read as test-direct. Those tasks are pinned by
``test_ownership_background_tasks.py`` (they receive ``user_id``) and by the
real boot.

The autouse fixture :func:`owner_fallback_ratchet` fails the test at teardown
that drove a door into a fallback or a context fill, naming the site and door.
Opt out (a test that pins the fallback behaviour itself) with
``@pytest.mark.owner_fallback_allowed``.
"""

from __future__ import annotations

import collections
import os
import sys
import threading

import pytest

from applire import ownership
from applire.services import owner_resolution

ALLOW_MARKER = "owner_fallback_allowed"

#: (kind, site, door) — kind ∈ {"fallback", "fill"}; collected for the running test.
DOOR_HITS: list[tuple[str, str, str]] = []
#: Every door hit of the session (never cleared) — the ratchet's own tests read it.
SESSION_DOOR_HITS: "collections.Counter[tuple[str, str, str]]" = collections.Counter()

_APPLIRE = os.sep + "applire" + os.sep


def door_of(frame) -> str | None:  # noqa: ANN001
    """The door a call stack passes, or ``None`` for a test-direct call."""
    http = False
    mcp_wrapper = False
    doors: list[str] = []
    f = frame
    while f is not None:
        fn = f.f_code.co_filename
        if "/starlette/" in fn or "/fastapi/" in fn:
            http = True
        if fn.endswith(os.path.join("applire", "mcp", "server.py")) and f.f_code.co_name == "wrapper":
            mcp_wrapper = True
        if _APPLIRE in fn and (os.sep + "routers" + os.sep in fn or os.sep + "mcp" + os.sep in fn):
            rel = fn[fn.rindex(_APPLIRE) + len(_APPLIRE):]
            doors.append(f"{rel}:{f.f_code.co_name}")
        f = f.f_back
    if not (http or mcp_wrapper):
        return None
    return doors[-1] if doors else ("http" if http else "mcp")


class _RatchetCounter(collections.Counter):
    """A ``Counter`` that also records door hits (swapped in by ``__class__``)."""

    _kind = "fallback"

    def __setitem__(self, key, value):  # noqa: ANN001
        _TOTALS[self._kind] += 1
        door = door_of(sys._getframe(1))
        if door is not None:
            hit = (self._kind, str(key), door)
            DOOR_HITS.append(hit)
            SESSION_DOOR_HITS[hit] += 1
        super().__setitem__(key, value)


_insert_target = threading.local()


class _FillRatchetCounter(collections.Counter):
    """``FILL_STATS``: a context fill counts as a door fill when the ROW was built
    on a door's stack. The fill itself runs inside the flush, i.e. inside
    SQLAlchemy's greenlet, whose stack does not reach the request's frames — so
    the door is read at construction time (``init`` event) instead."""

    def __setitem__(self, key, value):  # noqa: ANN001
        _TOTALS["fill"] += 1
        target = getattr(_insert_target, "row", None)
        door = getattr(target, "_ratchet_door", None) if target is not None else None
        if door is not None:
            hit = ("fill", str(key), door)
            DOOR_HITS.append(hit)
            SESSION_DOOR_HITS[hit] += 1
        super().__setitem__(key, value)


def _on_init(target, args, kwargs):  # noqa: ANN001
    if kwargs.get("user_id") is None:
        door = door_of(sys._getframe(1))
        if door is not None:
            try:
                object.__setattr__(target, "_ratchet_door", door)
            except Exception:  # noqa: BLE001 — never break a constructor
                pass


def _before_insert_mark(mapper, connection, target):  # noqa: ANN001
    _insert_target.row = target


_TOTALS: "collections.Counter[str]" = collections.Counter()


def _write_session_report() -> None:  # pragma: no cover — diagnostic only
    import json

    path = os.environ.get("APPLIRE_RATCHET_REPORT")
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "door_hits": {" | ".join(k): v for k, v in SESSION_DOOR_HITS.items()},
                "totals": dict(_TOTALS),
            },
            fh,
            indent=1,
        )


def install_owner_ratchet() -> None:
    """Swap the two production counters' class (identity kept) and hook the owned
    models' constructors (idempotent)."""
    from sqlalchemy import event

    from applire.db.session import Base

    owner_resolution.OWNER_FALLBACK_STATS.__class__ = _RatchetCounter
    ownership.FILL_STATS.__class__ = _FillRatchetCounter
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls.__dict__.get("__owned__") is True and not event.contains(cls, "init", _on_init):
            event.listen(cls, "init", _on_init)
    if os.environ.get("APPLIRE_RATCHET_REPORT") and not getattr(install_owner_ratchet, "_atexit", False):
        import atexit

        atexit.register(_write_session_report)
        install_owner_ratchet._atexit = True  # type: ignore[attr-defined]
    if not event.contains(Base, "before_insert", _before_insert_mark):
        # insert=True: runs before ownership's owner fill on the same row.
        event.listen(Base, "before_insert", _before_insert_mark, propagate=True, insert=True)


def register_ratchet_marker(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{ALLOW_MARKER}: the test may drive a production door into the user_id=None "
        "owner fallback or a context owner fill (it pins that behaviour — MD-24 (2))",
    )


@pytest.fixture(autouse=True)
def owner_fallback_ratchet(request: pytest.FixtureRequest):
    """Fail the test whose door reached the owner fallback or a context fill."""
    DOOR_HITS.clear()
    yield
    hits = list(DOOR_HITS)
    DOOR_HITS.clear()
    if hits and request.node.get_closest_marker(ALLOW_MARKER) is None:
        lines = "\n".join(f"  {k}: {site} <- {door}" for k, site, door in hits[:10])
        pytest.fail(
            "a production door relied on the implicit owner (MD-24 (2) ratchet: 0 "
            "door fallbacks, 0 door context fills) — pass user_id explicitly:\n" + lines,
            pytrace=False,
        )
