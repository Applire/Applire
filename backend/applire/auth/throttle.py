# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Login and setup throttling (ADR-091 cl. 13, 14; ruling RD-8; SF-IAM.6).

One tier, keyed on ``(casefolded email, client)`` for known **and** unknown
emails alike: after ``FREE_FAILURES`` (5) failures within ``WINDOW`` (15 min),
every further attempt on that key is **delayed** — 1 s, doubling, capped at
30 s. There is no hard lockout: a correct password is never refused, only
delayed while the key is hot, and a success clears the key — except while an
attack holds a SHARED client key (loopback / IPv6 behind docker-proxy, an
un-configured outer proxy): an attempt that cannot start within
``MAX_QUEUE_SECONDS`` is refused unchecked (ruling fix-id-1; the fix for the
operator is ``APPLIRE_TRUSTED_PROXY``). Because the key
contains the client, an attacker on another machine cannot slow the owner down.

The setup claim has its **own** bucket keyed on the client alone (cl. 14), so
exhausting the login bucket cannot block the operator's setup.

"Client" is ``X-Real-IP`` (nginx sets it from ``$remote_addr`` after its
``real_ip`` module has resolved an operator's TLS proxy in front), else the
socket peer. In-process store, LRU-evicted at 10 000 keys: correct for the
single backend process the compose file runs (a stated Negative of ADR-091).

**Per-key serialisation (MD-35).** A delay that every concurrent request sleeps
once bounds latency, not the guess rate (adv-id-3: C parallel connections ≈ C/30
guesses/s). Two mechanisms close it:

* :meth:`Throttle.attempt` is a per-key ``asyncio.Lock`` the caller holds across
  wait → verify → record, so attempts on one key run one at a time and attempt
  k+1 sees attempt k's failure (no free burst while the key is still cold).
* :meth:`Throttle.wait` reserves a **slot**: a request released at ``t`` moves the
  key's next slot to ``t + delay``, so even callers that skip the lock are spaced
  one delay apart instead of being released together.

A per-process lock is enough: the compose file runs one backend process (stated
Negative of ADR-091, cl. 13 as amended by MD-35).

**One normalisation (w4-fix-id).** The login route keys a KNOWN account on its
``users.id`` (the result of the SQL ``lower()`` lookup, ADR-091 cl. 6), so every
spelling the lookup maps to one account shares one bucket; only unknown emails are
keyed on the normalised text, where no account is at stake.

The key never holds a raw email in the clear beyond process memory; nothing here
is persisted or logged.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from fastapi import Request

FREE_FAILURES = 5
WINDOW_SECONDS = 15 * 60
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
MAX_KEYS = 10_000
#: MD-35 queue bound (founder ruling fix-id-1 = B, bounded). An attempt whose slot
#: lies further out than this is held this long and then refused WITHOUT a password
#: check (:class:`ThrottleSaturated` → the existing throttled 401 / setup 403), so the
#: guess rate stays bounded with no pile-up. ``None`` would be unbounded (option A).
MAX_QUEUE_SECONDS: float | None = 30.0


class ThrottleSaturated(Exception):
    """The key's queue is longer than ``MAX_QUEUE_SECONDS``; the attempt is refused."""


def client_of(request: Request) -> str:
    """The client identity for throttling (``X-Real-IP`` from nginx, else the peer)."""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


_UNSET = object()


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)


class Throttle:
    """A delay-only failure counter. ``clock`` and ``sleep`` are injectable for tests."""

    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep, max_keys: int = MAX_KEYS,
                 max_queue: object = None):
        self._entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
        #: key -> monotonic time before which the next attempt may not be released.
        self._next_slot: dict[tuple[str, ...], float] = {}
        #: key -> attempts released by ``wait`` and not yet recorded. When it drops
        #: to 0 the slot chain ends: a sequential attempt waits its own delay only.
        self._outstanding: dict[tuple[str, ...], int] = {}
        #: key -> [lock, holders+waiters]; dropped when nobody uses it (no growth
        #: from key spraying).
        self._locks: dict[tuple[str, ...], list] = {}
        self._clock = clock
        self._sleep = sleep
        #: per-instance override of ``MAX_QUEUE_SECONDS`` (tests); _UNSET = module value.
        self._max_queue = _UNSET if max_queue is None else max_queue
        self._max_keys = max_keys

    def _bound(self) -> float | None:
        return MAX_QUEUE_SECONDS if self._max_queue is _UNSET else self._max_queue  # type: ignore[return-value]

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
        """Sleep until this attempt's slot on ``key``; returns the seconds waited.

        While the key is hot each attempt takes the next free slot, one delay after
        the previous one (MD-35) — concurrent attempts are spaced, not batched.
        """
        delay = self.delay_for(key)
        if delay <= 0:
            return 0.0
        now = self._clock()
        release = max(now, self._next_slot.get(key, now)) + delay
        bound = self._bound()
        if bound is not None and release - now > bound:
            await self._sleep(bound)
            raise ThrottleSaturated()
        self._next_slot[key] = release
        self._outstanding[key] = self._outstanding.get(key, 0) + 1
        waited = release - now
        await self._sleep(waited)
        return waited

    @asynccontextmanager
    async def attempt(self, key: tuple[str, ...]) -> AsyncIterator[None]:
        """Hold ``key``'s lock across wait → verify → record (MD-35)."""
        slot = self._locks.get(key)
        if slot is None:
            slot = self._locks[key] = [asyncio.Lock(), 0]
        bound = self._bound()
        if bound is not None and slot[1] * self.delay_for(key) > bound:
            # Everyone ahead of us will wait at least one delay each: refuse rather
            # than queue past the bound (founder question w4-fix-id-1, option B).
            await self._sleep(bound)
            raise ThrottleSaturated()
        slot[1] += 1
        try:
            async with slot[0]:
                yield
        finally:
            slot[1] -= 1
            if slot[1] == 0 and self._locks.get(key) is slot:
                del self._locks[key]
                self._end_slot_chain(key)

    def _settle(self, key: tuple[str, ...]) -> None:
        """One released attempt reached its verdict."""
        n = self._outstanding.get(key, 0) - 1
        if n <= 0:
            self._end_slot_chain(key)
        else:
            self._outstanding[key] = n

    def _end_slot_chain(self, key: tuple[str, ...]) -> None:
        self._outstanding.pop(key, None)
        self._next_slot.pop(key, None)

    def record_failure(self, key: tuple[str, ...]) -> None:
        self._settle(key)
        entry = self._entries.pop(key, None) or _Entry()
        entry.failures.append(self._clock())
        self._entries[key] = entry  # most recent last
        while len(self._entries) > self._max_keys:
            evicted, _ = self._entries.popitem(last=False)
            self._end_slot_chain(evicted)

    def record_success(self, key: tuple[str, ...]) -> None:
        self._entries.pop(key, None)
        self._end_slot_chain(key)

    def clear(self) -> None:
        self._entries.clear()
        self._next_slot.clear()
        self._outstanding.clear()
        self._locks.clear()

    def __len__(self) -> int:
        return len(self._entries)


def login_key(email: str, request: Request, account_id: object = None) -> tuple[str, str]:
    """``(identity, client)``. ``account_id`` = the id the SQL ``lower()`` lookup
    resolved (one normalisation for key and lookup); else the typed email."""
    if account_id is not None:
        return (f"id:{account_id}", client_of(request))
    return (email.strip().casefold(), client_of(request))


def setup_key(request: Request) -> tuple[str]:
    return (client_of(request),)


#: Process-wide buckets. Separate objects = separate budgets (cl. 14).
login_throttle = Throttle()
setup_throttle = Throttle()
