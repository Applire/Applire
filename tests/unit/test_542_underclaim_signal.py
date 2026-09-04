# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#542 / ADR-076 clause 5 — under-claiming becomes a bound signal.

The class no existing instrument can see: a claimable, JD-required concept that
reaches the delivered document **only as a bare skills tag or a summary word**.
`verified_missing_claimable` scans the whole serialised draft, so the tag ends the
demand; `verified_missing_load_bearing` scopes to the narrative corpus but only for the
`direct` + figure-carrying subset; `build_gap_hints` treats covered as done.

Clause 5's own sentence — *"satisfied only by narrative or bullet content, never by
adding a bare skill tag"* — has therefore never had an instrument for the
non-load-bearing majority, which is what these tests pin.

No Docker, no DB, no real LLM.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_gap_hints import (  # noqa: E402
    UNDERCLAIM_ISSUE_LIMIT,
    UnderclaimedConcept,
    narrative_corpus_view,
    underclaim_signal_issues,
    underclaim_signal_issues_fn,
    verified_narrative_underclaim,
)
from applire.services.reviewer import review_and_refine  # noqa: E402


def _entry(concept, *, fit_weight=1.0, claimable=True, status="direct", evidence="vault says so", forms=None):
    return {
        "concept": concept,
        "claimable": claimable,
        "status": status,
        "fit_weight": fit_weight,
        "evidence": evidence,
        "surface_forms": forms or [concept],
    }


def _prose(bullets=(), skills=(), summary=""):
    """The writer's own PROSE shape — key `work`, not `work_history`."""
    return {
        "summary": summary,
        "work": [{"id": "w1", "bullets": list(bullets), "projects": []}],
        "skills": list(skills),
    }


def _composed(bullets=(), skills=(), summary=""):
    """The COMPOSED shape — key `work_history`."""
    return {
        "summary": summary,
        "work_history": [{"id": "w1", "bullets": list(bullets), "projects": []}],
        "skills": list(skills),
    }


# ---------------------------------------------------------------------------
# The shape adapter — the loop hands the PROSE draft, the audit the COMPOSED one
# ---------------------------------------------------------------------------


def test_the_narrative_corpus_view_reads_both_document_shapes():
    """The writer's response schema calls the list `work`; `TailoredCVData` calls it
    `work_history`, and `keyword_ledger._tailored_narrative_texts` only knows the
    latter. A signal that reads the raw draft would be blind on the drafting loop —
    which is a control that cannot fire, not a scoping decision."""
    assert narrative_corpus_view(_prose(bullets=["led the LTIF programme"]))["work_history"]
    assert narrative_corpus_view(_composed(bullets=["led the LTIF programme"]))["work_history"]


def test_the_narrative_corpus_view_is_empty_for_a_document_with_no_bullets():
    assert narrative_corpus_view({"summary": "x", "skills": ["A"]})["work_history"] == []


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_a_concept_carried_only_as_a_skills_tag_is_reported_as_tag_only():
    """THE class this signal exists for — invisible to every other instrument."""
    ledger = [_entry("ISO 45001", evidence="LTIF from 4.2 to 1.1 across three plants")]
    out = verified_narrative_underclaim(_prose(bullets=["ran the shift plan"], skills=["ISO 45001"]), ledger)
    assert [c.concept for c in out] == ["ISO 45001"]
    assert out[0].tag_only is True
    assert out[0].evidence == "LTIF from 4.2 to 1.1 across three plants"


def test_a_concept_absent_everywhere_is_reported_and_is_not_tag_only():
    ledger = [_entry("ISO 45001")]
    out = verified_narrative_underclaim(_prose(bullets=["ran the shift plan"]), ledger)
    assert [c.concept for c in out] == ["ISO 45001"]
    assert out[0].tag_only is False


def test_a_concept_present_in_a_bullet_is_never_reported():
    ledger = [_entry("ISO 45001")]
    assert verified_narrative_underclaim(_prose(bullets=["rolled out ISO 45001"]), ledger) == []


def test_a_concept_present_only_in_a_nested_project_bullet_counts_as_narrative():
    ledger = [_entry("ISO 45001")]
    draft = {"summary": "", "work": [{"id": "w1", "bullets": [],
                                      "projects": [{"name": "p", "bullets": ["ISO 45001 audit"]}]}], "skills": []}
    assert verified_narrative_underclaim(draft, ledger) == []


def test_a_summary_mention_does_not_count_as_narrative_evidence():
    """A one-line elevator pitch is not a story a hiring reviewer credits — the same
    scoping rule #315 already applies one class down."""
    ledger = [_entry("ISO 45001")]
    out = verified_narrative_underclaim(_prose(bullets=["ran the shift plan"], summary="ISO 45001 expert"), ledger)
    assert [c.concept for c in out] == ["ISO 45001"]
    assert out[0].tag_only is True


def test_a_below_rank_concept_is_never_reported():
    """Rank filter unconditional: a bullet list has a hard per-role ceiling whether or
    not the page budget currently binds, so narrative space is always scarce. A
    below-REQUIRED_WEIGHT concept living only as a tag is a legal outcome."""
    ledger = [_entry("Kanban", fit_weight=0.4)]
    assert verified_narrative_underclaim(_prose(skills=["Kanban"]), ledger) == []


def test_a_non_claimable_concept_is_never_reported():
    """An honest gap must stay absent — demanding it would be a demand to fabricate."""
    ledger = [_entry("SAP S/4HANA", claimable=False, status="gap")]
    assert verified_narrative_underclaim(_prose(), ledger) == []


def test_a_positioning_only_partial_is_never_reported():
    """ADR-048 amended 2026-07-27: the candidate does not hold the JD's own term at
    all, so demanding it appear literally is a demand to over-claim."""
    ledger = [dict(_entry("Payments platform", status="partial"), adjacent_evidence="settlement work")]
    assert verified_narrative_underclaim(_prose(), ledger) == []


def test_a_scope_entry_is_never_reported():
    """ADR-069: a scope entry's concept embeds the JD's OWN figure — demanding it
    verbatim would force that number into the document.

    The fixture is asserted against the REAL predicate first: the first version of
    this test invented `is_scope=True` and passed the filter untouched, which would
    have pinned nothing at all."""
    from applire.services.keyword_ledger import is_scope_entry

    entry = dict(_entry("Führungsspanne ~120 MA"), bar={"kind": "span", "value": 120})
    assert is_scope_entry(entry), "fixture does not actually carry the property it claims"
    assert verified_narrative_underclaim(_prose(), [entry]) == []


def test_results_are_ranked_by_fit_weight_descending():
    ledger = [_entry("B", fit_weight=1.0), _entry("A", fit_weight=2.0)]
    assert [c.concept for c in verified_narrative_underclaim(_prose(), ledger)] == ["A", "B"]


def test_no_ledger_reports_nothing_rather_than_guessing():
    assert verified_narrative_underclaim(_prose(), None) == []


# ---------------------------------------------------------------------------
# The carrier: ReviewIssues for ADR-083 clause 4's transport
# ---------------------------------------------------------------------------


def test_the_issue_demands_narrative_and_explicitly_refuses_a_skill_tag():
    """ADR-076 clause 5: 'satisfied only by narrative or bullet content, never by
    adding a bare skill tag' — otherwise this signal re-opens #250's keyword-stuffing
    door that the echo-drop guard closed."""
    ledger = [_entry("ISO 45001", evidence="LTIF from 4.2 to 1.1")]
    issues = underclaim_signal_issues(_prose(skills=["ISO 45001"]), ledger)
    assert len(issues) == 1
    text = issues[0].text
    assert "ISO 45001" in text
    assert "LTIF from 4.2 to 1.1" in text
    assert "skills" in text.lower()
    assert "bullet" in text.lower()


def test_the_issue_carries_the_house_grounding_rule_so_it_cannot_manufacture_an_overclaim():
    ledger = [_entry("ISO 45001")]
    text = underclaim_signal_issues(_prose(), ledger)[0].text
    assert "grounding outranks coverage" in text.lower()


def test_the_issue_is_blocking_so_the_shared_transport_renders_it():
    """`corrector_feedback.render_blocking_issues` filters to blocking by design; a
    minor signal issue would be computed and silently dropped."""
    ledger = [_entry("ISO 45001")]
    assert underclaim_signal_issues(_prose(), ledger)[0].is_blocking


def test_at_most_two_concepts_are_demanded_per_round():
    """The letter reviewer's own check-5 bound, for the same reason: #525's loop
    exhausted 5/5 demanding two new keywords per round while the corrector's
    insertions displaced earlier ones."""
    ledger = [_entry(f"Concept {i}", fit_weight=1.0 + i) for i in range(6)]
    issues = underclaim_signal_issues(_prose(), ledger)
    assert len(issues) == UNDERCLAIM_ISSUE_LIMIT == 2
    assert "Concept 5" in issues[0].text and "Concept 4" in issues[1].text


def test_a_document_with_all_its_evidence_on_the_page_raises_nothing():
    ledger = [_entry("ISO 45001")]
    assert underclaim_signal_issues(_prose(bullets=["ISO 45001 rollout"]), ledger) == []


# ---------------------------------------------------------------------------
# Loop integration — the signal may never create a round or force a verdict
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider():
    return AsyncMock()


@pytest.mark.asyncio
async def test_the_signal_never_fires_when_the_reviewer_approves(mock_provider):
    """The structural safety argument, pinned: no round happens, so no signal. This
    is why clause 5's floor is the report, not the carrier."""
    calls: list[dict] = []
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
    await review_and_refine(
        source="src",
        draft=_prose(),
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        signal_issues_fn=lambda d: (calls.append(d), [])[1],
    )
    assert calls == []


@pytest.mark.asyncio
async def test_the_signal_never_fires_on_a_minor_only_settle(mock_provider):
    calls: list[dict] = []
    mock_provider.aparse_json.return_value = {
        "approved": False, "issues": [{"severity": "minor", "issue": "tone"}], "feedback": "",
    }
    await review_and_refine(
        source="src",
        draft=_prose(),
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        signal_issues_fn=lambda d: (calls.append(d), [])[1],
    )
    assert calls == []


@pytest.mark.asyncio
async def test_the_signal_reaches_the_corrector_through_the_adr_083_transport(mock_provider):
    """One transport, five chains (ADR-066) — the signal is rendered by the same
    `fold_issues_into_feedback` as a reviewer finding, because to the corrector it IS
    a finding."""
    seen_feedback: list[str] = []
    ledger = [_entry("ISO 45001", evidence="LTIF from 4.2 to 1.1")]
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": [{"severity": "blocking", "issue": "reviewer finding"}],
         "feedback": "reviewer prose"},
        _prose(bullets=["something else"]),
    ]

    def gen(draft, feedback, source):
        seen_feedback.append(feedback)
        return "retry"

    await review_and_refine(
        source="src",
        draft=_prose(skills=["ISO 45001"]),
        generator_prompt_fn=gen,
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        signal_issues_fn=underclaim_signal_issues_fn(ledger),
    )
    assert len(seen_feedback) == 1
    assert "reviewer prose" in seen_feedback[0]
    assert "reviewer finding" in seen_feedback[0]
    assert "ISO 45001" in seen_feedback[0]


@pytest.mark.asyncio
async def test_a_raising_signal_fn_loses_the_signal_never_the_round(mock_provider):
    seen_feedback: list[str] = []
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": [{"severity": "blocking", "issue": "reviewer finding"}],
         "feedback": "reviewer prose"},
        _prose(bullets=["b"]),
    ]

    def boom(_draft):
        raise RuntimeError("signal blew up")

    def gen(draft, feedback, source):
        seen_feedback.append(feedback)
        return "retry"

    await review_and_refine(
        source="src",
        draft=_prose(),
        generator_prompt_fn=gen,
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        signal_issues_fn=boom,
    )
    assert "reviewer finding" in seen_feedback[0]


@pytest.mark.asyncio
async def test_the_signal_does_not_enter_the_exhaustion_log_issue_count(mock_provider, caplog):
    """`REVIEW_EXHAUSTED chain=… issues=N` counts REVIEWER issues. Folding a
    deterministic population into it would silently redefine an existing metric."""
    import logging

    ledger = [_entry("ISO 45001")]
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": [{"severity": "blocking", "issue": "one reviewer finding"}],
         "feedback": "f"},
        _prose(bullets=["b"]),
    ]
    with caplog.at_level(logging.INFO):
        await review_and_refine(
            source="src",
            draft=_prose(),
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "rev",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=1,
            signal_issues_fn=underclaim_signal_issues_fn(ledger),
        )
    exhausted = [r.getMessage() for r in caplog.records if "REVIEW_EXHAUSTED" in r.getMessage()]
    assert exhausted and "issues=1" in exhausted[0]
    assert any("REVIEW_SIGNAL_ISSUES" in r.getMessage() for r in caplog.records)


def test_the_dataclass_is_frozen_so_a_consumer_cannot_edit_the_fact():
    c = UnderclaimedConcept(concept="x", evidence="e", fit_weight=1.0, surface_forms=("x",), tag_only=False)
    with pytest.raises(Exception):
        c.concept = "y"  # type: ignore[misc]
