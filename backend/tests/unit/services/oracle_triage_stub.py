# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared triage stub for the Oracle's letter tests (ADR-068 amended
2026-08-08, #309 + #373).

Every letter audit now runs a PRE-GRADING ``sentence_triage`` call, so any
test that hands ``audit_document`` a targeted provider has to answer that
chain too — otherwise the seam reads as unavailable and every sentence is
audited (fail-to-audit, the correct polarity, but not what those tests are
about).

One implementation, shared (ADR-066): a stub that recognises the triage
system prompt, answers the classes a test declares by marker substring, and
delegates every other chain to an inner provider (or a neutral entailment
shape). Markers are TEST-ONLY fixture wiring, never a classifier — a mock
cannot assert classification correctness (ADR-062 clause 7); only a charter
run can.
"""
from __future__ import annotations

from typing import Any

from applire.prompts.oracle_triage import ORACLE_TRIAGE_ITEM_RE


def is_triage_call(system: str | None) -> bool:
    return "sentence triage" in (system or "").lower()


def triage_answer(prompt: str, classify=None) -> dict[str, Any]:
    """One triage response for *prompt*, each sentence echoed verbatim.

    *classify* maps a sentence to its class; the default answers
    ``candidate-claim`` for everything — the permissive-inverted default,
    which exempts nothing and leaves every other seam reachable.
    """
    return {
        "items": [
            {
                "index": int(index),
                "classification": (
                    classify(text) if classify is not None else "candidate-claim"
                ),
                # Verbatim echo — the document-side citation must verify
                # honestly, exactly as a real answer's would.
                "sentence_quote": text,
            }
            for index, text in ORACLE_TRIAGE_ITEM_RE.findall(prompt)
        ]
    }


class TriageStubProvider:
    """Answers the triage chain; everything else goes to *inner*.

    *epistolary* / *employer_fact* are marker substrings (case-insensitive):
    a sentence containing one is answered with that class. Everything else is
    ``candidate-claim`` — the permissive-inverted default, so a stub can only
    ever exempt what a test explicitly asked it to.
    """

    def __init__(
        self,
        *,
        epistolary: tuple[str, ...] = (),
        employer_fact: tuple[str, ...] = (),
        inner: Any | None = None,
    ):
        self.epistolary = tuple(m.lower() for m in epistolary)
        self.employer_fact = tuple(m.lower() for m in employer_fact)
        self.inner = inner
        self.triage_calls: list[str] = []

    def _classify(self, sentence: str) -> str:
        low = sentence.lower()
        if any(m in low for m in self.employer_fact):
            return "employer-fact"
        if any(m in low for m in self.epistolary):
            return "epistolary-form"
        return "candidate-claim"

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        if is_triage_call(system):
            self.triage_calls.append(prompt)
            return triage_answer(prompt, self._classify)
        if self.inner is not None:
            return await self.inner.aparse_json(prompt, system=system, **kwargs)
        # Neutral narrow-entailment shape (never overrules a deterministic
        # verdict either way, ADR-052 §2).
        return {"verdict": "unverifiable"}
