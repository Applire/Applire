# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-081 cl. 9 amended 2026-10-22 (#786, Hazelnut build 3, WP-T): a review step
of the writing loop that did not run is RECORDED, so the document can say so.

``review_and_refine`` (``services/reviewer.py``) fails open by contract (ADR-021,
ADR-076 cl. 2 *ship-and-report*): a reviewer call that times out, truncates or
returns malformed JSON ships the draft un-reviewed, and a corrector call that does
the same keeps the last known-good draft while the verdict's findings stay open.
Before this module the *report* half existed for the terminal chain only (the
``terminal-review`` check); the drafting, language and language-recheck chains
reported to the log alone — Emma's seed CV in the 2026-10-20 UX audit shipped
after three provider timeouts and the user was told nothing.

The record is a per-generation sink held in a :class:`~contextvars.ContextVar`,
the ``llm_usage_context`` pattern: the document's background render opens it
around the whole pipeline (:func:`review_degradation_record`), every fail-open exit
of the loop reports to it (:func:`record_review_degradation`), and the render
persists :meth:`DegradationRecord.as_json` in its single ready-commit. A sink that
is not open — job analysis, interview, extraction, a unit test — makes reporting a
no-op, so no other chain changes behaviour.

PII-free by construction: a chain id, a stage, a reason class and counts. No draft
text, no verdict text, no provider message.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Literal

Stage = Literal["review", "correction"]
Reason = Literal["timeout", "truncated", "malformed", "shape", "error"]

#: The exception class names ``review_and_refine`` hands over, mapped onto the
#: user-facing reason. Anything else is ``error`` — never dropped.
_REASON_BY_ERROR: dict[str, Reason] = {
    "LLMTimeoutError": "timeout",
    "TimeoutError": "timeout",
    "LLMTruncatedError": "truncated",
    "JSONDecodeError": "malformed",
    "WrongShape": "shape",
}


def reason_for(error_type: str) -> Reason:
    """The record's reason for a failed call's exception class name."""
    return _REASON_BY_ERROR.get(error_type, "error")


@dataclass
class DegradedStep:
    """One review step that did not do its job, folded by (check, stage, reason)."""

    check: str
    stage: Stage
    reason: Reason
    #: The loop attempt (1-based) of the FIRST occurrence.
    attempt: int
    #: How many times this (check, stage, reason) occurred in the generation.
    count: int = 1

    def as_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "stage": self.stage,
            "reason": self.reason,
            "attempt": self.attempt,
            "count": self.count,
        }


class DegradationRecord:
    """The degraded review steps of ONE document generation, in first-seen order."""

    def __init__(self) -> None:
        self._steps: list[DegradedStep] = []

    def add(self, check: str, stage: Stage, reason: Reason, attempt: int) -> None:
        for step in self._steps:
            if (step.check, step.stage, step.reason) == (check, stage, reason):
                step.count += 1
                step.attempt = min(step.attempt, attempt)
                return
        self._steps.append(DegradedStep(check, stage, reason, attempt))

    @property
    def steps(self) -> tuple[DegradedStep, ...]:
        return tuple(self._steps)

    def as_json(self) -> list[dict[str, Any]]:
        """The persisted value: ``[]`` = recorded, every review step answered."""
        return [s.as_json() for s in self._steps]


_SINK: ContextVar[DegradationRecord | None] = ContextVar("review_degradation_sink", default=None)


@contextmanager
def review_degradation_record() -> Iterator[DegradationRecord]:
    """Open the sink for one document generation (set-and-restore).

    The record is a mutable object held by the context variable, so a child task
    created inside the block (``asyncio.gather`` copies the context) reports into
    the SAME record.
    """
    record = DegradationRecord()
    token = _SINK.set(record)
    try:
        yield record
    finally:
        _SINK.reset(token)


def record_review_degradation(chain_id: str, stage: Stage, error_type: str, attempt: int) -> None:
    """Report one fail-open exit of ``review_and_refine``. No-op without an open sink.

    Never raises: this is a reporting layer and may not become a new way for
    generation to fail (ADR-039).
    """
    record = _SINK.get()
    if record is None:
        return
    try:
        record.add(str(chain_id), stage, reason_for(error_type), int(attempt))
    except Exception:  # pragma: no cover - fail-safe
        return


def normalize_stored(value: Any) -> list[dict[str, Any]] | None:
    """The stored column read back for a door: ``None`` stays ``None`` (not
    recorded), a list is passed through with only well-formed entries kept."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, list):
        return None
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("check"), str):
            out.append(
                {
                    "check": item["check"],
                    "stage": item.get("stage") if item.get("stage") in ("review", "correction") else "review",
                    "reason": item.get("reason") if item.get("reason") in _REASONS else "error",
                    "attempt": int(item.get("attempt") or 1),
                    "count": int(item.get("count") or 1),
                }
            )
    return out


_REASONS = frozenset({"timeout", "truncated", "malformed", "shape", "error"})
