# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Rolling in-process error counter (ADR-086 clause 6).

The cheapest honest answer to *"is something going wrong right now"* that does
not need a log aggregator: a deque of timestamps, trimmed to
``OPS_ERROR_WINDOW_MINUTES``.

Deliberately **in-process and not persisted**. A restart clears it, and a
multi-worker deployment counts per worker — both are true and both are stated on
the endpoint (`window_minutes`, and the counter resets with the process). A
persisted error table would be a second growth surface for a number whose only
use is "recent", and would need its own retention rule.

Fed from two places: the LLM usage recorder (every failed provider call) and —
once the ``main.py`` patch lands — the application's 5xx exception handler.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from applire.services.ops.config import OPS_ERROR_WINDOW_MINUTES

# (monotonic_seconds, kind)
_events: deque[tuple[float, str]] = deque(maxlen=1000)
_lock = threading.Lock()


def record_error(kind: str = "unknown") -> None:
    """Note that something failed. Never raises."""
    try:
        with _lock:
            _events.append((time.monotonic(), kind[:32]))
    except Exception:  # pragma: no cover - defensive
        pass


def error_counts(window_minutes: int | None = None) -> dict[str, int]:
    """Errors in the rolling window, by kind, plus a ``total``."""
    window = (window_minutes or OPS_ERROR_WINDOW_MINUTES) * 60
    cutoff = time.monotonic() - window
    counts: dict[str, int] = {}
    with _lock:
        # Trim from the left; the deque is time-ordered by construction.
        while _events and _events[0][0] < cutoff:
            _events.popleft()
        for _, kind in _events:
            counts[kind] = counts.get(kind, 0) + 1
    counts["total"] = sum(counts.values())
    return counts


def reset() -> None:
    """Test hook."""
    with _lock:
        _events.clear()
