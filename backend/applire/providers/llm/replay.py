# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay provider — returns responses the model actually produced (ADR-073).

The problem this solves is not weak assertions. A 15-test sample of the CV /
letter / Oracle area found mostly-strong assertions and no tautological test at
all. Five of the six recorded test-failure modes are about the test never
*reaching* the behaviour, or reaching it with input that does not resemble
production. So the instrument that pays is realism of inputs.

``MockLLMProvider`` cannot supply that. It returns hand-written canned shapes,
and it identifies its caller by substring-matching the system prompt
("HR analyst", "outcome critic", …). #362 showed that is ambiguous, not merely
brittle: both real extraction prompts open with the identical sentence, so the
mock cannot distinguish the flat import door from the split one even in
principle, and returns one shape matching neither.

``ReplayLLMProvider`` fixes both halves:

* **Selection** is by pipeline seam — ``(stage, review_role, review_attempt)``,
  read from :func:`applire.providers.llm.debug_log.current_call_site`, the same
  triple the recorder writes. The caller sets it; nothing is inferred from prompt
  text, so there is no fingerprint to collide.
* **Content** is verbatim captured output. Nobody hand-wrote it, so it carries
  the things hand-written fixtures systematically lack — German compound
  morphology, real figure formatting, real phrasing variance.

**Fail-closed by design.** An unmatched call raises. A replay provider that
quietly fell back to a generic response would reproduce the exact defect this
class exists to remove: a test that looks green while never reaching the code.
That is also why there is no "record if missing" mode — a silent re-record turns
a failing test into a passing one by rewriting the evidence.

Scope: a **test** provider. It is not registered in the provider factory and must
never be selectable via ``LLM_PROVIDER``.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import current_call_site

# A recorded exchange, reduced to what replay needs.
Key = tuple[str, str | None]


class ReplayExhausted(RuntimeError):
    """More calls arrived at a seam than were recorded for it."""


class ReplayMiss(RuntimeError):
    """A call arrived at a seam with no recorded exchange at all."""


class ReplayLLMProvider(LLMProvider):
    """Serve recorded responses, keyed by pipeline seam.

    Args:
        records: recorded exchanges. Each needs ``stage``, ``response`` and
            optionally ``review_role`` / ``review_attempt``. Order within a seam
            is preserved and consumed as a queue, so a review loop replays its
            attempts in the order they happened.
        strict_role: when True (default) a call is matched on
            ``(stage, review_role)``. When False, ``review_role`` is ignored and
            all of a stage's records form one queue — useful when the code under
            test does not run the review loop.
    """

    def __init__(
        self,
        records: Iterable[dict[str, Any]],
        *,
        strict_role: bool = True,
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        self._strict_role = strict_role
        self._queues: dict[Key, deque[dict[str, Any]]] = defaultdict(deque)
        self._served: list[Key] = []
        for rec in records:
            self._queues[self._key_of(rec.get("stage") or "", rec.get("review_role"))].append(rec)

    # ---- key handling -------------------------------------------------

    def _key_of(self, stage: str, role: str | None) -> Key:
        return (stage, role if self._strict_role else None)

    def _take(self) -> dict[str, Any]:
        stage, role, _attempt = current_call_site()
        key = self._key_of(stage, role)
        queue = self._queues.get(key)
        if queue is None:
            raise ReplayMiss(
                f"no recorded exchange for seam {key!r}. Recorded seams: "
                f"{sorted(self._queues)}. Either the code under test moved to a "
                "different stage label, or the fixture needs that seam captured."
            )
        if not queue:
            raise ReplayExhausted(
                f"seam {key!r} ran out of recorded exchanges after "
                f"{sum(1 for k in self._served if k == key)} call(s). The code "
                "under test is making more calls than the capture did — that is a "
                "behaviour change, not a fixture problem."
            )
        self._served.append(key)
        return queue.popleft()

    # ---- observability for tests --------------------------------------

    @property
    def served(self) -> list[Key]:
        """Seams served, in call order. Assert on this to prove a test really
        reached the seam it claims to exercise — the reachability check that
        several of our failure modes come down to."""
        return list(self._served)

    def assert_fully_consumed(self) -> None:
        """Every recorded exchange was used.

        A leftover means the code under test skipped a call the capture made —
        which usually means a branch silently stopped running. Left as an explicit
        call rather than a destructor so a test opts into the stricter claim.
        """
        leftover = {k: len(q) for k, q in self._queues.items() if q}
        if leftover:
            raise AssertionError(
                f"recorded exchanges were never replayed: {leftover}. The code "
                "under test made fewer calls than the capture did."
            )

    # ---- LLMProvider ABC ----------------------------------------------

    async def acomplete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> str:
        response = self._take().get("response")
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)

    async def aparse_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> dict[str, Any]:
        response = self._take().get("response")
        if isinstance(response, str):
            response = json.loads(response)
        if not isinstance(response, dict):
            raise TypeError(
                f"recorded response is {type(response).__name__}, not a JSON object"
            )
        return response


def load_slice(path: str | Path) -> list[dict[str, Any]]:
    """Load a committed replay slice (JSONL, one recorded exchange per line).

    Deliberately not gated on the file existing: a missing replay fixture means
    the tier is broken, not that the test is inapplicable. Fixture-gated skips
    are how 9 tests in this repo stopped being tests without anyone noticing.
    """
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
