# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry build 2 adversarial review — #703 repeated-demand signal.

EXPECTED TO FAIL on the reviewed tree (`a96007bb`); pins finding 12 of
`Documents/Runs/Strawberry/build-2/adv-review/findings.md`.

`DemandRecord.record_round` is the `on_demand` callback of
`coverage_reviewer_prompt_fn`, which fires while the reviewer PROMPT is built —
before the provider call. A reviewer call that then fails (timeout, truncation,
malformed JSON) ships the draft un-reviewed, but the round it never answered is
already in the record. A term shown in one answered round + one failed round
becomes "2× nachgefordert — Applire hat in 2 Runden versucht …".
"""
from __future__ import annotations

import asyncio

from applire.exceptions import LLMTimeoutError
from applire.services.reviewer import review_and_refine
from applire.services.terminal_review_outcome import DemandRecord


class _Provider:
    """Reviewer round 1 rejects, the corrector answers, reviewer round 2 times out."""

    def __init__(self):
        self.reviewer_calls = 0

    async def aparse_json(self, prompt, system=None, **kw):
        if prompt.startswith("REVIEW"):
            self.reviewer_calls += 1
            if self.reviewer_calls == 1:
                return {"approved": False, "issues": [
                    {"text": "SAP MM fehlt im Anschreiben.", "severity": "blocking"}
                ]}
            raise LLMTimeoutError("reviewer timed out")
        return {"body": {"paragraphs": ["Zweiter Entwurf."]}}


def test_adv_review_12_a_round_the_reviewer_never_answered_is_not_counted():
    record = DemandRecord()

    def reviewer_prompt_fn(source, draft):
        # What `coverage_reviewer_prompt_fn(on_demand=record.record_round)` does:
        # report the round's demanded entries while building the prompt.
        record.record_round([{"concept": "SAP MM", "surface_forms": ["SAP MM"]}])
        return "REVIEW " + source

    asyncio.run(review_and_refine(
        source="Quelle",
        draft={"body": {"paragraphs": ["Erster Entwurf."]}},
        generator_prompt_fn=lambda draft, feedback, source: "CORRECT " + feedback,
        generator_system="gen",
        reviewer_prompt_fn=reviewer_prompt_fn,
        reviewer_system="rev",
        provider=_Provider(),
        max_retries=3,
        chain_id="adv_review_703",
    ))
    signals = record.repeated({"body": {"paragraphs": ["Zweiter Entwurf."]}})
    # One answered demand of SAP MM: below the >= 2 rounds threshold (ruling R-3).
    assert [s.term for s in signals] == [], [(s.term, s.rounds, s.total_rounds) for s in signals]
