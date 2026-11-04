# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WP3 (ADR-100 tiers, Letter #784 / Writer #786, Hazelnut collectors 2026-11-04):
three literal scans rendered at their true strength. Synthetic data only.

1. A ledger row whose VERIFIED backing is a LANGUAGES row the CV renders is delivered
   by that section — the CV terminal review demanded "German language skills" beside
   "German: Native speaker" until it cycled (Fact tier: quote + code-joined section).
2. A row whose verified backing sits in a work entry the CV renders is a HINT for the
   ``narrative-evidence`` check: never a "claimed but not evidenced" fail, and the
   corrector's demand becomes confirm-or-surface.
3. A letter's denied term named only inside its own negation ("While I have no
   Kubernetes experience, …") is the candidate's stated limit, not ``present_denied``.
"""

from __future__ import annotations

import pytest
import types

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import (
    _audit_letter_text,
    _narrative_evidence_check,
    grounding_vault_index,
    non_claim_names_for_job,
)
from applire.services.cv_gap_hints import (
    _issue_text,
    backing_evidence_entry,
    structured_backing_delivered,
    underclaim_signal_issues_fn,
    verified_narrative_underclaim,
)
from applire.services.keyword_ledger import verified_missing_claimable

_WID = "a1b2c3d4-0000-4000-8000-000000000001"
_PROFILE = {
    "personal_info": {"name": "Mira Feldkamp"},
    "work_experience": [
        {
            "id": _WID,
            "company": "Brennwerk Analytik GmbH",
            "role": "Leiterin Labor-IT",
            "start_date": "2018-03",
            "responsibilities": [
                "Leitung der Labor-IT mit einem Team von fünf Mitarbeitenden an zwei Standorten.",
            ],
        }
    ],
    "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
}
_LANG_ROW = {
    "concept": "German language skills", "surface_forms": ["German language skills"],
    "status": "direct", "claimable": True, "fit_weight": 1.0, "sources": ["required"],
    "backing": {"quote": "Deutsch", "unit_path": "languages[0]", "instrument": "quote"},
}
_LEAD_ROW = {
    "concept": "Team leadership", "surface_forms": ["Team leadership"],
    "status": "direct", "claimable": True, "fit_weight": 1.0, "sources": ["required"],
    "backing": {
        "quote": "Leitung der Labor-IT mit einem Team von fünf Mitarbeitenden an zwei Standorten.",
        "unit_path": "work_experience[0].responsibilities[0]", "instrument": "quote",
    },
}


def _cv(*, languages=True, bullets=True, skills=("Team leadership",)):
    return {
        "contact": {"name": "Mira Feldkamp"},
        "summary": "Laboratory IT lead.",
        "work_history": [{
            "id": _WID, "company": "Brennwerk Analytik GmbH", "role": "Head of Laboratory IT",
            "start_date": "2018-03", "end_date": None,
            "bullets": ["Led laboratory IT with a team of five across two sites."] if bullets else [],
        }],
        "skills": list(skills),
        "languages": [{"language": "German", "level": "Native speaker"}] if languages else [],
    }


# ── 1. structured backing delivers ────────────────────────────────────────────

def test_a_languages_backing_rendered_by_the_cv_is_delivered():
    assert structured_backing_delivered(_LANG_ROW, _cv())
    assert [e["concept"] for e in verified_missing_claimable(_cv(), [_LANG_ROW])] == []


def test_the_same_row_stays_missing_when_the_cv_drops_the_language():
    assert not structured_backing_delivered(_LANG_ROW, _cv(languages=False))
    assert [e["concept"] for e in verified_missing_claimable(_cv(languages=False), [_LANG_ROW])] == [
        "German language skills"]


def test_a_backing_outside_the_structured_sections_never_delivers_by_section():
    assert not structured_backing_delivered(_LEAD_ROW, _cv())
    unbacked = {k: v for k, v in _LANG_ROW.items() if k != "backing"}
    assert not structured_backing_delivered(unbacked, _cv())


def test_a_different_language_does_not_deliver():
    cv = _cv()
    cv["languages"] = [{"language": "English", "level": "C1"}]
    assert not structured_backing_delivered(_LANG_ROW, cv)


# ── 2. narrative-evidence: a backed row in a rendered entry is a Hint ─────────

def _check(cv):
    return _narrative_evidence_check(
        TailoredCVData.model_validate(cv), [_LEAD_ROW], grounding_vault_index(_PROFILE))


def test_a_backed_concept_in_a_rendered_entry_does_not_fail_the_check():
    c = _check(_cv())
    assert c.status == "pass"
    assert "Team leadership (Brennwerk Analytik GmbH)" in (c.details or "")
    assert "not evidenced" not in (c.details or "")
    assert c.driver == {"concepts": 0, "unconfirmed": 1}


def test_the_entry_must_be_rendered_with_bullets_or_the_check_still_fails():
    c = _check(_cv(bullets=False))
    assert c.status == "fail"
    assert "claimed but not evidenced" in (c.details or "")


def test_without_a_vault_index_the_literal_tier_is_unchanged():
    c = _narrative_evidence_check(TailoredCVData.model_validate(_cv()), [_LEAD_ROW])
    assert c.status == "fail"


def test_the_corrector_demand_for_a_backed_concept_is_confirm_or_surface():
    fn = underclaim_signal_issues_fn(
        [_LEAD_ROW], structured_document_fn=lambda d: d, vault_index=grounding_vault_index(_PROFILE))
    (issue,) = fn(_cv())
    assert "recorded under Brennwerk Analytik GmbH" in issue.text
    assert "leave it unchanged and never insert the posting's word" in issue.text
    assert "reads this requirement as unmet" not in issue.text


def test_an_unbacked_concept_keeps_the_literal_demand():
    row = {k: v for k, v in _LEAD_ROW.items() if k != "backing"}
    (c,) = verified_narrative_underclaim(_cv(), [row], structured_document=_cv(),
                                         vault_index=grounding_vault_index(_PROFILE))
    assert c.evidence_entry is None
    assert "reads this requirement as unmet" in _issue_text(c)


def test_backing_evidence_entry_reads_the_vault_index_owner():
    vi = grounding_vault_index(_PROFILE)
    assert backing_evidence_entry(_LEAD_ROW, _cv(), vi) == "Brennwerk Analytik GmbH"
    other = _cv()
    other["work_history"][0]["id"] = "not-the-owner"
    assert backing_evidence_entry(_LEAD_ROW, other, vi) is None


# ── 3. a letter's own stated limit is not present_denied ─────────────────────

_K8S = {"status": "denied", "concept": "Kubernetes", "surface_forms": ["Kubernetes"],
        "claimable": False, "fit_weight": 1.0, "denial_level": "direct", "sources": ["required"]}
_JOB = types.SimpleNamespace(role_title="Integration Lead", company_name="Norvane Biosciences GmbH")


def _letter_buckets(sentence: str):
    paras = ["Dear Hiring Manager,", "At Brennwerk I lead laboratory IT. " + sentence]
    text = "\n\n".join(paras)
    rep = _audit_letter_text(
        text, {"header": {}, "recipient": {}, "body": {"paragraphs": paras}}, ["Kubernetes"],
        [_K8S], non_claim=non_claim_names_for_job(_JOB, None), document_language="en")
    return rep.keywords.present_denied, rep.keywords.present_unsupported_in_denial


def test_a_stated_limit_moves_out_of_present_denied():
    denied, in_denial = _letter_buckets(
        "While I have no Kubernetes experience, my container work with Docker transfers directly.")
    assert denied == [] and in_denial == ["Kubernetes"]


def test_a_claim_of_the_denied_term_stays_present_denied():
    denied, in_denial = _letter_buckets("I ran Kubernetes clusters for every laboratory system.")
    assert denied == ["Kubernetes"] and in_denial == []


@pytest.mark.parametrize("sentence", [
    "I never stopped using Kubernetes daily.",
    "No problem: Kubernetes is my daily tool.",
    "There is no better platform than Kubernetes, which I operate daily.",
    "Without any hesitation I deployed Kubernetes at scale.",
    "I have no doubt about Kubernetes; I run it in production.",
    "I have no doubt about my Kubernetes experience.",
    "I never stopped gaining practical Kubernetes knowledge.",
])
def test_an_unrelated_negation_does_not_turn_a_claim_into_a_stated_limit(sentence):
    # Adversarial pass 2026-11-04: a negation cue earlier in the clause is not a
    # disclosure unless the sentence says what is lacking.
    denied, in_denial = _letter_buckets(sentence)
    assert denied == ["Kubernetes"] and in_denial == []


@pytest.mark.parametrize("sentence", [
    "I have not worked with Kubernetes so far.",
    "I have no hands-on exposure to Kubernetes yet.",
])
def test_a_stated_limit_naming_what_is_lacking_still_moves(sentence):
    denied, in_denial = _letter_buckets(sentence)
    assert denied == [] and in_denial == ["Kubernetes"]


def test_one_claim_sentence_beside_a_disclosure_keeps_it_flagged():
    denied, _ = _letter_buckets(
        "I have no Kubernetes certification. I operated Kubernetes clusters at two sites.")
    assert denied == ["Kubernetes"]


# ── seams: each call site hands the vault index over ─────────────────────────

class _Captured(Exception):
    pass


def test_SEAM_cv_terminal_review_hands_the_vault_index_to_the_underclaim_signal(monkeypatch):
    """cv._terminal_review → underclaim_signal_issues_fn(vault_index=…): stop the
    terminal review at the signal's construction and read what it was given."""
    import asyncio
    from unittest.mock import MagicMock

    import applire.services.cv_gap_hints as hints
    from applire.services import cv as cv_mod

    seen: dict = {}

    def _spy(ledger, **kw):
        seen.update(kw)
        raise _Captured

    monkeypatch.setattr(hints, "underclaim_signal_issues_fn", _spy)
    try:
        record = MagicMock()
        record.tailored_data = _cv()
        asyncio.run(cv_mod._terminal_review(
            record, MagicMock(), prose_draft={}, source_material="", provider=MagicMock(),
            profile_json=_PROFILE, raw_profile_json=_PROFILE, keyword_ledger=[_LEAD_ROW],
            budget=MagicMock(), job_dict={}, language="en", condense_ctx=MagicMock(pins=[]),
            coverage_budget=None, measured=None))
    except _Captured:
        pass
    vi = seen.get("vault_index")
    assert vi is not None
    assert any(u.path == "work_experience[0].responsibilities[0]" for u in vi.units)


def test_SEAM_cv_ats_audit_hands_the_vault_index_to_the_narrative_check():
    from applire.services.ats_audit import _audit_cv_text

    rep = _audit_cv_text("Mira Feldkamp Led laboratory IT", TailoredCVData.model_validate(_cv()),
                         keywords=[], ledger=[_LEAD_ROW], vault_index=grounding_vault_index(_PROFILE))
    (check,) = [c for c in rep.checks if c.id == "narrative-evidence"]
    assert check.status == "pass" and check.driver == {"concepts": 0, "unconfirmed": 1}


def test_a_claim_in_the_same_sentence_as_the_disclosure_keeps_it_flagged():
    denied, _ = _letter_buckets(
        "Kubernetes clusters I operated at two sites, though I have no Kubernetes certification.")
    assert denied == ["Kubernetes"]


# ── Ruling R-2, letter side: the CV's translated title is the same title ─────

def test_r2_the_letter_titles_block_carries_the_cv_form_of_a_translated_title():
    from applire.services.cover_letter_positioning import (
        render_role_titles_block, vault_role_titles, with_cv_role_forms)

    block = render_role_titles_block(with_cv_role_forms(vault_role_titles(_PROFILE), _PROFILE, _cv()))
    assert '"Leiterin Labor-IT"' in block
    assert 'the CV states this same title in its language as "Head of Laboratory IT"' in block
    assert "the CV states this same title" not in render_role_titles_block(
        with_cv_role_forms(vault_role_titles(_PROFILE), _PROFILE, None))
