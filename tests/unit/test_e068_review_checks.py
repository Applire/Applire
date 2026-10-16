# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E068 / ADR-094 (draft) — deterministic findings into the review loop.

The generation-pipeline-v2 rule: after the last model call no code changes the
document's text; code may compute a FACT, feed it into the review loop as a
finding, or block delivery. ``review_and_refine(checks_fn=…)`` is the one
transport for the "feed it into the loop" half, shared by the CV and the letter
(ADR-066: one implementation).

What distinguishes a CHECK from the #542 ``signal_issues_fn``:

* a check finding is a FACT and is BLOCKING by construction — it opens a
  corrector round even when the reviewer approved or raised only minor issues
  (the signal can never create a round);
* the reviewer is SHOWN the open findings, so the reviewer and the code never
  contradict each other about one draft (ADR-062 cl. 4);
* what is still open when the loop settles is REPORTED (``on_checks_settle``),
  so the caller can ship-and-report or block — the loop never decides that and
  never edits the draft.

No Docker, no DB, no real LLM.
"""
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.reviewer import (  # noqa: E402
    CheckFinding,
    ChecksSettle,
    render_checks_block,
    review_and_refine,
)

APPROVE = {"approved": True, "issues": [], "feedback": ""}
MINOR = {"approved": False, "issues": [{"severity": "minor", "issue": "tone"}], "feedback": "polish"}


def _over_budget(draft):
    """A toy fact: more than two bullets is over budget."""
    n = len(draft.get("bullets") or [])
    if n > 2:
        return [CheckFinding(check="toy.budget", text=f"{n} bullets, budget 2 — swap, do not add")]
    return []


def _call(provider, draft, *, checks_fn=_over_budget, max_retries=2, gen=None, rev=None, **kw):
    return review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=gen or (lambda d, f, s: "retry"),
        generator_system="gen",
        reviewer_prompt_fn=rev or (lambda s, d: "REVIEW"),
        reviewer_system="rev",
        provider=provider,
        max_retries=max_retries,
        checks_fn=checks_fn,
        **kw,
    )


@pytest.fixture
def provider():
    return AsyncMock()


@pytest.mark.asyncio
async def test_a_check_finding_opens_a_round_the_reviewer_approved(provider):
    seen: list[str] = []
    provider.aparse_json.side_effect = [APPROVE, {"bullets": ["a", "b"]}, APPROVE]

    def gen(d, feedback, s):
        seen.append(feedback)
        return "retry"

    out = await _call(provider, {"bullets": ["a", "b", "c"]}, gen=gen)
    assert out == {"bullets": ["a", "b"]}
    assert len(seen) == 1 and "3 bullets, budget 2" in seen[0]
    assert provider.aparse_json.await_count == 3


@pytest.mark.asyncio
async def test_a_check_finding_opens_a_round_on_a_minor_only_verdict(provider):
    seen: list[str] = []
    provider.aparse_json.side_effect = [MINOR, {"bullets": ["a"]}, APPROVE]

    def gen(d, feedback, s):
        seen.append(feedback)
        return "retry"

    out = await _call(provider, {"bullets": ["a", "b", "c"]}, gen=gen)
    assert out == {"bullets": ["a"]}
    assert "budget 2" in seen[0]


@pytest.mark.asyncio
async def test_no_finding_and_approval_settles_without_a_round(provider):
    provider.aparse_json.side_effect = [APPROVE]
    out = await _call(provider, {"bullets": ["a"]})
    assert out == {"bullets": ["a"]}
    assert provider.aparse_json.await_count == 1


@pytest.mark.asyncio
async def test_the_reviewer_is_shown_the_open_findings(provider):
    prompts: list[str] = []
    provider.aparse_json.side_effect = [APPROVE, {"bullets": ["a"]}, APPROVE]

    async def capture(prompt, **kw):
        prompts.append(prompt)
        return provider.aparse_json.side_effect_values.pop(0)

    provider.aparse_json.side_effect_values = [APPROVE, {"bullets": ["a"]}, APPROVE]
    provider.aparse_json.side_effect = capture
    await _call(provider, {"bullets": ["a", "b", "c"]})
    assert "REVIEW" in prompts[0] and "3 bullets, budget 2" in prompts[0]
    assert "CODE CHECKS" in prompts[0]
    # the corrector's draft is clean, so the second reviewer prompt carries no block
    assert "CODE CHECKS" not in prompts[2]


@pytest.mark.asyncio
async def test_reviewer_blocking_issues_and_findings_both_reach_the_corrector(provider):
    seen: list[str] = []
    provider.aparse_json.side_effect = [
        {"approved": False, "issues": [{"severity": "blocking", "issue": "reviewer finding"}],
         "feedback": "reviewer prose"},
        {"bullets": ["a"]},
        APPROVE,
    ]

    def gen(d, feedback, s):
        seen.append(feedback)
        return "retry"

    await _call(provider, {"bullets": ["a", "b", "c"]}, gen=gen)
    assert "reviewer prose" in seen[0] and "reviewer finding" in seen[0]
    assert "toy.budget" in seen[0] or "budget 2" in seen[0]


@pytest.mark.asyncio
async def test_open_findings_are_reported_at_settle_and_the_draft_is_never_edited(provider):
    reports: list[ChecksSettle] = []
    # the corrector never fixes it; the loop exhausts
    provider.aparse_json.side_effect = [APPROVE, {"bullets": ["a", "b", "x"]}, APPROVE,
                                        {"bullets": ["a", "b", "y"]}]
    out = await _call(provider, {"bullets": ["a", "b", "c"]}, on_checks_settle=reports.append)
    assert out == {"bullets": ["a", "b", "y"]}  # shipped as the model wrote it
    assert len(reports) == 1
    r = reports[0]
    assert r.evaluated is True and r.path == "exhausted"
    assert [f.check for f in r.open_findings] == ["toy.budget"]
    assert r.rounds_with_findings == 2


@pytest.mark.asyncio
async def test_a_resolved_finding_reports_clean(provider):
    reports: list[ChecksSettle] = []
    provider.aparse_json.side_effect = [APPROVE, {"bullets": ["a"]}, APPROVE]
    await _call(provider, {"bullets": ["a", "b", "c"]}, on_checks_settle=reports.append)
    assert reports[0].open_findings == () and reports[0].path == "approved"
    assert reports[0].rounds_with_findings == 1


@pytest.mark.asyncio
async def test_review_disabled_reports_findings_as_evaluated_but_unacted(provider):
    reports: list[ChecksSettle] = []
    out = await _call(provider, {"bullets": ["a", "b", "c"]}, max_retries=0,
                      on_checks_settle=reports.append)
    assert out == {"bullets": ["a", "b", "c"]}
    provider.aparse_json.assert_not_awaited()
    assert reports[0].path is None and reports[0].open_findings[0].check == "toy.budget"


@pytest.mark.asyncio
async def test_a_raising_checks_fn_loses_the_finding_never_the_round(provider, caplog):
    provider.aparse_json.side_effect = [APPROVE]

    def boom(_d):
        raise RuntimeError("check blew up")

    with caplog.at_level(logging.ERROR):
        out = await _call(provider, {"bullets": ["a", "b", "c"]}, checks_fn=boom)
    assert out == {"bullets": ["a", "b", "c"]}
    assert any("checks_fn raised" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_findings_stay_out_of_the_reviewer_issue_count(provider, caplog):
    """`REVIEW_EXHAUSTED … issues=N` counts REVIEWER issues; the checks get their own
    line (no existing metric is silently redefined)."""
    provider.aparse_json.side_effect = [APPROVE, {"bullets": ["a", "b", "x"]}]
    with caplog.at_level(logging.INFO):
        await _call(provider, {"bullets": ["a", "b", "c"]}, max_retries=1)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("REVIEW_CHECKS chain=" in m and "toy.budget" in m for m in msgs)
    assert any("REVIEW_CHECKS_OPEN chain=" in m for m in msgs)


@pytest.mark.asyncio
async def test_without_checks_fn_behaviour_is_unchanged(provider):
    provider.aparse_json.side_effect = [APPROVE]
    out = await _call(provider, {"bullets": ["a", "b", "c"]}, checks_fn=None)
    assert out == {"bullets": ["a", "b", "c"]}
    assert provider.aparse_json.await_count == 1


def test_render_checks_block_is_empty_for_no_findings():
    assert render_checks_block([]) == ""
    block = render_checks_block([CheckFinding(check="c.x", text="fact one")])
    assert "CODE CHECKS" in block and "fact one" in block and "c.x" in block
