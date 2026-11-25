# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The render settle window in the unit tiers (Sorbet invite-gate round 7).

A render that ends unfinished has its record marked ``failed`` by
``render_slots._mark_failed``, which retries a transient database error for
``SETTLE_SECONDS`` (60 s). CI's unit job sets no ``DATABASE_URL``, so
``tests/unit/conftest.py`` names a Postgres URL where nothing listens: a test
that runs a render but patches only its own module's session factory reaches
the default one, is refused, and waited the full 60 s.

* The window is one try (``SETTLE_SECONDS = 0``) unless the test is marked
  ``real_settle``: the tests that prove the retry, the shutdown wait and the
  compose bound keep the real constants. Production defaults are unchanged.
* The guard, for every unit test: one failure mark may take at most
  ``MARK_CEILING_SECONDS``. A longer one is cut off and fails the test, naming
  the trap — so a bypassed window can never again cost a minute per test in
  silence.
"""

from __future__ import annotations

import asyncio

import pytest

MARKER = "real_settle"
#: Real marks in the unit tiers take milliseconds; the trap took 60 s.
MARK_CEILING_SECONDS = 5.0


def register_settle_marker(config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}: run render_slots' real settle window (SETTLE_SECONDS) instead of one try",
    )


@pytest.fixture(autouse=True)
def bounded_settle_window(request, monkeypatch):
    from applire.services import render_slots

    if request.node.get_closest_marker(MARKER) is None:
        monkeypatch.setattr(render_slots, "SETTLE_SECONDS", 0.0)
    real_mark = render_slots._mark_failed
    overran: list[str] = []

    async def ceiled_mark(kind, record_id, *args, **kwargs):
        try:
            return await asyncio.wait_for(real_mark(kind, record_id, *args, **kwargs), MARK_CEILING_SECONDS)
        except asyncio.TimeoutError:
            overran.append(f"{kind} {record_id}")
            raise

    monkeypatch.setattr(render_slots, "_mark_failed", ceiled_mark)
    ceiled_mark.__settle_window_ceiling__ = MARK_CEILING_SECONDS  # type: ignore[attr-defined]
    yield overran  # the guard's own test provokes an overrun and clears it
    if overran:
        pytest.fail(
            f"render_slots._mark_failed ran longer than {MARK_CEILING_SECONDS:.0f} s ({', '.join(overran)}): "
            "a unit test retried a failure mark against an unreachable database. Patch "
            "applire.db.session.AsyncSessionLocal for the render, or mark the test "
            f"@pytest.mark.{MARKER} if it proves the retry (backend/tests/support/settle_window.py)",
            pytrace=False,
        )
