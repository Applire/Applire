# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-C (Sorbet invite gate) — GARH: the review loop never ends on an unreviewed rewrite.

Founder rulings Q9-1…Q9-7 rule out "an unreviewed last rewrite". ``review_and_refine``'s
``final_review`` option ends the loop on a reviewer round: the last corrector output is
read by one more verdict-only round, and the draft that settles is the draft that verdict
read. Default ``False`` keeps ADR-021's shape for every caller that does not opt in (the
letter loops).

No Docker, no DB, no real LLM.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.review_issues import ReviewSettle  # noqa: E402
from applire.services.reviewer import review_and_refine  # noqa: E402
from applire.services.terminal_review_outcome import settle_to_outcome  # noqa: E402

_REJECT = {"approved": False, "feedback": "fix it",
           "issues": [{"severity": "blocking", "check": "1", "issue": "still wrong"}]}
_APPROVE = {"approved": True, "feedback": "", "issues": []}


class _Scripted:
    """A provider that answers reviewer calls from a verdict script and corrector calls
    with numbered drafts; it records which draft each reviewer call read."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.reviewed: list[str] = []
        self.corrector_calls = 0

    async def aparse_json(self, prompt, system=None, **_kw):
        if system == "rev":
            self.reviewed.append(prompt)
            return self.verdicts.pop(0)
        self.corrector_calls += 1
        return {"summary": f"d{self.corrector_calls}"}


async def _run(provider, *, max_retries, final_review, seen):
    return await review_and_refine(
        source="src",
        draft={"summary": "d0"},
        generator_prompt_fn=lambda d, f, s: f"correct {d['summary']}",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: d["summary"],
        reviewer_system="rev",
        provider=provider,
        max_retries=max_retries,
        on_settle=seen.append,
        final_review=final_review,
    )


@pytest.mark.asyncio
async def test_final_review_reads_the_last_rewrite_and_settles_it_with_its_open_findings():
    seen: list[ReviewSettle] = []
    p = _Scripted([_REJECT, _REJECT, _REJECT])
    out = await _run(p, max_retries=2, final_review=True, seen=seen)
    assert out == {"summary": "d2"}
    assert p.reviewed == ["d0", "d1", "d2"], "the last rewrite must be read by a verdict"
    assert p.corrector_calls == 2, "no corrector runs after the final verdict"
    (settle,) = seen
    assert settle.path == "exhausted"
    assert settle.settled == {"summary": "d2"}
    assert settle.blocking_issues == ("still wrong",)
    outcome = settle_to_outcome(settle, chain_id="cv_terminal_review", reviewed_draft={"summary": "d2"})
    assert outcome.findings_stand_against_delivered
    assert outcome.status == "fail"


@pytest.mark.asyncio
async def test_final_review_that_approves_the_last_rewrite_settles_approved():
    seen: list[ReviewSettle] = []
    p = _Scripted([_REJECT, _REJECT, _APPROVE])
    out = await _run(p, max_retries=2, final_review=True, seen=seen)
    assert out == {"summary": "d2"}
    assert seen[0].path == "approved"


@pytest.mark.asyncio
async def test_default_loop_is_unchanged_and_still_ends_on_the_unreviewed_rewrite():
    """Letters do not opt in: two verdicts, two rewrites, the last one unread."""
    seen: list[ReviewSettle] = []
    p = _Scripted([_REJECT, _REJECT])
    out = await _run(p, max_retries=2, final_review=False, seen=seen)
    assert out == {"summary": "d2"}
    assert p.reviewed == ["d0", "d1"]
    assert seen[0].path == "exhausted"


@pytest.mark.asyncio
async def test_review_only_reads_the_draft_once_and_never_rewrites_it():
    seen: list[ReviewSettle] = []
    p = _Scripted([_REJECT])
    out = await _run(p, max_retries=0, final_review=True, seen=seen)
    assert out == {"summary": "d0"}
    assert p.reviewed == ["d0"] and p.corrector_calls == 0
    assert seen[0].path == "review_only"
    assert settle_to_outcome(seen[0], chain_id="x", reviewed_draft={"summary": "d0"}).status == "fail"


@pytest.mark.asyncio
async def test_review_only_clean_verdict_settles_approved():
    seen: list[ReviewSettle] = []
    p = _Scripted([_APPROVE])
    await _run(p, max_retries=0, final_review=True, seen=seen)
    assert seen[0].path == "approved"


@pytest.mark.asyncio
async def test_disabled_layer_without_final_review_is_still_not_run():
    seen: list[ReviewSettle] = []
    p = _Scripted([])
    await _run(p, max_retries=0, final_review=False, seen=seen)
    assert p.reviewed == [] and seen[0].path is None
