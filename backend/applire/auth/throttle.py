# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Login and setup throttling (ADR-091 cl. 13, 14; ruling RD-8; SF-IAM.6).

One tier, keyed on ``(casefolded email, client)`` for known **and** unknown
emails alike: after ``FREE_FAILURES`` (5) failures within ``WINDOW`` (15 min),
every further attempt on that key is **delayed** — 1 s, doubling, capped at
30 s. There is no hard lockout: a correct password is never refused, only
delayed while the key is hot, and a success clears the key. Because the key
contains the client, an attacker on another machine cannot slow the owner down.

The setup claim has its **own** bucket keyed on the client alone (cl. 14), so
exhausting the login bucket cannot block the operator's setup.

"Client" is ``X-Real-IP`` (nginx sets it from ``$remote_addr`` after its
``real_ip`` module has resolved an operator's TLS proxy in front), else the
socket peer. In-process store, LRU-evicted at 10 000 keys: correct for the
single backend process the compose file runs (a stated Negative of ADR-091).

The key never holds a raw email in the clear beyond process memory; nothing here
is persisted or logged.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import Request

FREE_FAILURES = 5
WINDOW_SECONDS = 15 * 60
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
MAX_KEYS = 10_000


def client_of(request: Request) -> str:
    """The client identity for throttling (``X-Real-IP`` from nginx, else the peer)."""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)


class Throttle:
    """A delay-only failure counter. ``clock`` and ``sleep`` are injectable for tests."""

    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep, max_keys: int = MAX_KEYS):
        self._entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
        self._clock = clock
        self._sleep = sleep
        self._max_keys = max_keys

    def _live_failures(self, key: tuple[str, ...]) -> list[float]:
        entry = self._entries.get(key)
        if entry is None:
            return []
        cutoff = self._clock() - WINDOW_SECONDS
        entry.failures = [t for t in entry.failures if t > cutoff]
        if not entry.failures:
            del self._entries[key]
            return []
        return entry.failures

    def delay_for(self, key: tuple[str, ...]) -> float:
        """Seconds the next attempt on ``key`` waits (0 while under the free budget)."""
        n = len(self._live_failures(key))
        if n < FREE_FAILURES:
            return 0.0
        return min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** (n - FREE_FAILURES))

    async def wait(self, key: tuple[str, ...]) -> float:
        """Sleep the current delay for ``key``; returns the seconds waited."""
        delay = self.delay_for(key)
        if delay > 0:
            await self._sleep(delay)
        return delay

    def record_failure(self, key: tuple[str, ...]) -> None:
        entry = self._entries.pop(key, None) or _Entry()
        entry.failures.append(self._clock())
        self._entries[key] = entry  # most recent last
        while len(self._entries) > self._max_keys:
            self._entries.popitem(last=False)

    def record_success(self, key: tuple[str, ...]) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def login_key(email: str, request: Request) -> tuple[str, str]:
    # Python casefold is fine HERE: this is an in-memory bucket key, never an
    # identity comparison (identity uses SQL lower(), ADR-091 cl. 6).
    return (email.strip().casefold(), client_of(request))


def setup_key(request: Request) -> tuple[str]:
    return (client_of(request),)


#: Process-wide buckets. Separate objects = separate budgets (cl. 14).
login_throttle = Throttle()
setup_throttle = Throttle()
