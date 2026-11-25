# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G36 fix round 3, X-5b/X-5c (adv re-probe 1 on e7e7cfa3). One rule wherever a class
is carried across class calls or drafts: a delivered finding is advice only when every
class call that classed its text said the same advice class AND the call for the
delivered draft succeeded. A failure, a disagreement or a missing answer is truth.

Driven through the functions the adversary named: ``classify_open_points`` then
``worse_of`` (the letter re-entry, ``cover_letter.py`` ~2821) and ``after`` (the CV
re-entry, ``cv.py`` ~5640), then the producer → ``ATSReport`` → stored JSON → hold."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.ats import ATSReport  # noqa: E402
from applire.services import review_signals  # noqa: E402
from applire.services.point_class import classify_open_points  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    CorrectionFacts,
    TerminalReviewOutcome,
    build_terminal_review_check,
)

GAP = "The letter does not name the posting's SAP PM requirement."


class _Provider:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc = answer, exc

    async def aparse_json(self, prompt, *, system=None, **kw):
        if self.exc:
            raise self.exc
        return self.answer


def _cls(c):
    return _Provider({"classes": [{"index": 0, "class": c}]})


FAILS = _Provider(exc=RuntimeError("scripted class-call failure"))
NO_ANSWER = _Provider({"classes": []})


def _outcome(*, superseding=False):
    return TerminalReviewOutcome(
        chain_id="letter_terminal_review", path="cycle_detected", approved=False,
        blocking_issues=(GAP,), minor_issues=(), rounds=1,
        correction=CorrectionFacts(delivered_is_reviewed=True) if superseding else None,
    )


def _held(outcome, document="cover_letter"):
    check = build_terminal_review_check(outcome, document=document).model_dump(mode="json", exclude_none=True)
    ats = {"version": 1, "document": document, "passed": 0, "failed": 1, "not_applicable": 0,
           "checks": [check], "keywords": {"present": [], "missing": [], "present_unsupported": []}}
    ats = json.loads(json.dumps(ATSReport.model_validate(ats).model_dump(mode="json")))
    rec = SimpleNamespace(ats_report=ats, review_state=None, truthfulness_report=None, origin="pipeline",
                          audit_state=None)
    if document == "cover_letter":
        rec.letter_data = {"body": {"paragraphs": ["x"]}}
    served = review_signals.open_points_check(rec)
    return 0 if served is None else served["details_params"]["count"]


async def _two_calls(earlier_provider, delivered_provider, *, fold, superseding=False):
    earlier = await classify_open_points(_outcome(), document={"body": "a"}, provider=earlier_provider)
    delivered = await classify_open_points(_outcome(superseding=superseding), document={"body": "b"},
                                           provider=delivered_provider)
    return delivered.worse_of(earlier) if fold == "worse_of" else delivered.after(earlier)


@pytest.mark.asyncio
@pytest.mark.parametrize("fold,superseding", [("worse_of", False), ("after", True), ("after", False)])
@pytest.mark.parametrize("earlier,delivered,held", [
    ("coverage", FAILS, 1),         # X-5b: the delivered draft's call failed
    ("coverage", NO_ANSWER, 1),     # ... or gave no answer for the finding
    ("coverage", "truth", 1),
    ("truth", "coverage", 1),       # X-5c: the later class never releases an earlier truth
    ("coverage", "form", 1),        # two different advice classes
    ("coverage", "coverage", 0),    # every call said the same advice class
    ("form", "form", 0),
    (FAILS, "coverage", 1),         # an earlier failed call is a call that did not agree
])
async def test_advice_only_when_every_call_agreed_and_the_delivered_call_succeeded(fold, superseding, earlier,
                                                                                  delivered, held):
    p_early = earlier if isinstance(earlier, _Provider) else _cls(earlier)
    p_late = delivered if isinstance(delivered, _Provider) else _cls(delivered)
    folded = await _two_calls(p_early, p_late, fold=fold, superseding=superseding)
    assert _held(folded) == held
    assert _held(folded, document="cv") == held


@pytest.mark.asyncio
async def test_a_single_failed_call_records_truth_not_nothing():
    got = await classify_open_points(_outcome(), document=None, provider=FAILS)
    assert dict(got.point_tags) == {GAP: "truth"}
