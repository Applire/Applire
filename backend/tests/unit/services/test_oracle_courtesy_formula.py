# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Courtesy/meta formulas — the contract RENEGOTIATED with the sentence-triage
seam (ADR-068 amended 2026-08-08, #309 + #373; ADR-062 note of the same date).

**What this file used to pin (2026-07-23 – 2026-08-08).** A phrase list
(``_FORMULA_SEED_PATTERNS``) plus ``_is_pure_formula_clause`` decided "does
this sentence assert anything about the candidate?", and a match SILENTLY
DROPPED the claim — ``extract_claims_from_letter`` returned nothing at all for
a pure courtesy paragraph. Seven tests pinned that behaviour, including
``test_pure_courtesy_paragraph_yields_no_claim``.

**Why it was retired, not tuned.** The question is an ADR-062 clause-1
judgement, and a phrase list cannot answer one: word-order variance defeated
it on #309's own real-world phrasing, and the silent drop was invisible
suppression — the user saw no verdict either way. Deletion over repair
(clause 3), replaced by the ``sentence_triage`` seam.

**The new contract, pinned below.**
1. Nothing is silently dropped any more. A courtesy sentence IS extracted as
   a claim.
2. With the seam available and classifying it ``epistolary-form``, it becomes
   a VISIBLE ``not_applicable`` verdict (checker ``sentence_triage``) that
   quotes the sentence and leaves the ``unverifiable_dominated`` denominator.
3. With the seam DOWN, it flows to audit like any other sentence
   (permissive-inverted polarity: fail-to-audit, never fail-to-exempt). That
   is #309's denominator pollution returning for the outage's duration —
   named as a degradation cost in the amendment, not a regression to fix
   here.
4. ``_strip_formula_prefix`` survives the retirement: it trims a recognized
   courtesy PREFIX from a claim's stored TEXT, dropping nothing, and the
   RETAINED deterministic employer-fact pre-filter depends on it (#282).

Classification correctness under a real provider is charter-run evidence
(ADR-062 clause 7); everything here is wiring.
"""
from __future__ import annotations

import pytest

from applire.prompts.oracle_triage import ORACLE_TRIAGE_ITEM_RE
from applire.services.oracle import audit_document
from applire.services.oracle.extract import (
    _strip_formula_prefix,
    extract_claims_from_letter,
)

PROFILE = {
    "personal_info": {"name": "Max Muster"},
    "work_experience": [
        {
            "id": "w-contoso",
            "company": "Contoso GmbH",
            "role": "Automation Engineer",
            "start_date": "2020-03",
            "end_date": "2026-02",
            "responsibilities": [
                "Automated release workflows with Git and GitHub Actions.",
                "Led the migration of production databases.",
            ],
        }
    ],
    "skills": [{"name": "GitHub Actions", "category": "technical"}],
}

COURTESY_SENTENCES = [
    "I am writing to express my interest in the Senior Automation Engineer "
    "role at your company.",
    "Thank you for your time and consideration.",
    # #309's own phrasing, the word-order twin the retired phrase list missed.
    "Gerne stehe ich für ein persönliches Gespräch zur Verfügung.",
    # #564: the deterministic Anrede floor's OWN generic output
    # (_inject_salutation, services/cover_letter.py) — this exact string
    # reaches body.paragraphs[0] of every composed letter that had no
    # author-written salutation. It is a courtesy/form sentence like any
    # other above, never a candidate claim.
    "Sehr geehrte Damen und Herren,",
    "Dear Sir or Madam,",
]


class _EpistolaryStub:
    """Triage stub: answers ``epistolary-form`` for this file's courtesy
    sentences and ``candidate-claim`` for everything else, always echoing the
    sentence verbatim so the document-side citation verifies."""

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        if "sentence triage" not in (system or "").lower():
            return {"verdict": "unverifiable"}
        items = []
        for raw_index, text in ORACLE_TRIAGE_ITEM_RE.findall(prompt):
            norm = " ".join(text.lower().split())
            epistolary = any(
                norm in " ".join(s.lower().split())
                or " ".join(s.lower().split()) in norm
                for s in COURTESY_SENTENCES
            )
            items.append(
                {
                    "index": int(raw_index),
                    "classification": (
                        "epistolary-form" if epistolary else "candidate-claim"
                    ),
                    "sentence_quote": text,
                }
            )
        return {"items": items}


# ── 1. nothing is silently dropped any more ─────────────────────────────────


@pytest.mark.parametrize("sentence", COURTESY_SENTENCES)
def test_courtesy_sentence_is_extracted_not_dropped(sentence):
    """The retired ``_is_pure_formula_clause`` returned zero claims here.
    Extraction now hands every sentence to the seam — a claim the audit can
    place a visible verdict on."""
    claims = extract_claims_from_letter({"body": {"paragraphs": [sentence]}}, PROFILE)
    assert claims, sentence


def test_the_retired_predicate_is_gone():
    """Deletion, not tuning (ADR-062 clause 3) — re-introducing the phrase
    list as a deterministic pre-drop would restore invisible suppression."""
    import applire.services.oracle.extract as extract_module

    assert not hasattr(extract_module, "_is_pure_formula_clause")


# ── 2. with the seam available: a VISIBLE, quoted not_applicable verdict ────


@pytest.mark.asyncio
async def test_courtesy_paragraph_becomes_a_visible_not_applicable_verdict():
    letter = {
        "body": {
            "paragraphs": [
                COURTESY_SENTENCES[0],
                "At Contoso GmbH, I automated workflows using Git, GitHub "
                "Actions.",
                "I led the migration of production databases.",
                COURTESY_SENTENCES[1],
            ]
        }
    }
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=letter, provider=_EpistolaryStub()
    )
    exempted = [r for r in report.claims if r.verdict.checker == "sentence_triage"]
    assert exempted, [(r.claim.text, r.verdict.checker) for r in report.claims]
    for r in exempted:
        assert r.verdict.verdict == "not_applicable", r.verdict
        assert "epistolary-form" in (r.verdict.detail or "")
        assert r.claim.text in (r.verdict.detail or ""), r.verdict.detail


@pytest.mark.asyncio
async def test_exempted_courtesy_sentences_leave_the_denominator():
    letter = {
        "body": {
            "paragraphs": [
                COURTESY_SENTENCES[0],
                "At Contoso GmbH, I automated workflows using Git, GitHub "
                "Actions.",
                "I led the migration of production databases.",
                COURTESY_SENTENCES[1],
            ]
        }
    }
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=letter, provider=_EpistolaryStub()
    )
    checkable = len(report.claims) - report.counts["not_applicable"]
    assert report.counts["not_applicable"] >= 2, report.counts
    assert checkable >= 1
    assert report.unverifiable_dominated is False, report.counts


@pytest.mark.asyncio
async def test_a_substantive_sentence_is_never_exempted_by_the_seam():
    """The over-drop guard: a real claim keeps being graded, and the stub is
    only ever asked about sentences — never allowed to exempt this one."""
    letter = {
        "body": {
            "paragraphs": [
                "At Contoso GmbH, I led the migration of production databases."
            ]
        }
    }
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=letter, provider=_EpistolaryStub()
    )
    assert [r.verdict.checker for r in report.claims] != ["sentence_triage"]
    assert all(r.verdict.verdict != "not_applicable" for r in report.claims)


# ── 3. seam down: courtesy flows to audit (fail-to-audit) ───────────────────


@pytest.mark.asyncio
async def test_seam_down_audits_courtesy_sentences_instead_of_exempting_them():
    """The amendment's named degradation cost (a): with no provider, #309's
    denominator pollution returns — self-identified via
    ``judgement_unavailable``. What must NEVER happen is the opposite:
    exemptions granted while the seam is down."""
    letter = {"body": {"paragraphs": [COURTESY_SENTENCES[1]]}}
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    assert report.claims
    assert report.counts["not_applicable"] == 0
    assert all(r.verdict.checker != "sentence_triage" for r in report.claims)
    assert report.judgement_unavailable >= 1


# ── 4. the prefix trim survives (the pre-filter depends on it) ──────────────


def test_courtesy_prefix_is_still_trimmed_from_the_stored_claim_text():
    """``_strip_formula_prefix`` is not the retired judgement: it drops
    nothing and only scopes a claim's TEXT. #282's fused-opener employer-fact
    re-classification keys off it, so its removal would make seam-down worse
    than today rather than equal to it."""
    text = (
        "I would welcome the opportunity to discuss how my background in "
        "backend engineering, production LLM applications, and mentoring "
        "aligns with your needs."
    )
    assert _strip_formula_prefix(text) != text
    assert "backend engineering" in _strip_formula_prefix(text)


def test_prefix_trim_leaves_a_sentence_with_no_recognized_prefix_alone():
    text = "At Contoso GmbH, I led the migration of production databases."
    assert _strip_formula_prefix(text) == text


# ── 5. #564 — the deterministic Anrede floor's generic salutation ───────────
#
# The #564 W1 brief pointed at ``_is_pure_formula_clause`` as the "fix site"
# for keeping the floor's generic salutation out of the Oracle's claim set.
# That function is gone (retired 2026-08-08, module docstring above) — deletion,
# not tuning, and nothing on this path drops a claim any more (contract point
# 1 above). So the brief's premise does not hold against the CURRENT code, and
# the tests below verify what the code actually does instead: the generic
# salutation is extracted like any other courtesy sentence (point 1), and the
# ONE existing safety net is this file's point-2 mechanism — the sentence-
# triage seam already classifies it as ``epistolary-form`` BY NAME (the
# ORACLE_TRIAGE_SYSTEM_PROMPT text below lists "a salutation" as its own
# example), exempting it from grounding verification with a visible, quoted
# ``not_applicable`` verdict rather than a silent, deterministic drop.


def test_564_generic_salutation_is_a_single_courtesy_claim_not_an_employer_fact():
    """Real predicates, not the retired phrase list: extract_claims_from_letter
    keeps the floor's exact output string as ONE unsplit claim (no comma-clause
    boundary fires — the trailing comma has nothing after it), and it is
    neither an employer fact nor a denial — an ordinary courtesy claim,
    dispatched to sentence_triage like COURTESY_SENTENCES[0:3] above."""
    for salutation in ("Sehr geehrte Damen und Herren,", "Dear Sir or Madam,"):
        claims = extract_claims_from_letter(
            {"body": {"paragraphs": [salutation]}}, PROFILE
        )
        assert len(claims) == 1, (salutation, claims)
        claim = claims[0]
        assert claim.text == salutation
        assert claim.kind == "sentence"
        assert claim.is_employer_fact is False
        assert claim.is_denial is False
        assert claim.source_experience_id is None


def test_564_triage_prompt_names_a_salutation_as_its_own_epistolary_form_example():
    """The mechanism that actually protects #564's injected Anrede from
    becoming a false "ungrounded claim" finding: not a deterministic drop (the
    brief's named fix site, retired), but this classifier prompt explicitly
    listing a salutation as epistolary-form. Deterministic prompt pin —
    classification correctness under a real provider stays charter-run
    evidence (ADR-062 clause 7), per this module's own docstring."""
    from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT

    low = ORACLE_TRIAGE_SYSTEM_PROMPT.lower()
    assert "epistolary-form" in low
    epistolary_bullet = low[low.index("- epistolary-form"):]
    epistolary_bullet = epistolary_bullet[: epistolary_bullet.index("\n\n")]
    assert "salutation" in epistolary_bullet


@pytest.mark.asyncio
async def test_564_generic_de_salutation_becomes_a_visible_not_applicable_verdict():
    """End-to-end wiring pin (mirrors
    test_courtesy_paragraph_becomes_a_visible_not_applicable_verdict above)
    for the EXACT string _inject_salutation supplies: with the triage seam
    available, the floor's Anrede is exempted with a visible, quoted verdict
    rather than silently graded (or silently dropped, the retired behaviour)."""
    salutation = "Sehr geehrte Damen und Herren,"
    letter = {
        "body": {
            "paragraphs": [
                salutation,
                "At Contoso GmbH, I automated workflows using Git, GitHub "
                "Actions.",
            ]
        }
    }
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=letter, provider=_EpistolaryStub()
    )
    exempted = [
        r for r in report.claims
        if r.claim.text == salutation and r.verdict.checker == "sentence_triage"
    ]
    assert exempted, [(r.claim.text, r.verdict.checker) for r in report.claims]
    verdict = exempted[0].verdict
    assert verdict.verdict == "not_applicable", verdict
    assert "epistolary-form" in (verdict.detail or "")
    assert salutation in (verdict.detail or ""), verdict.detail
    # the substantive sentence alongside it is never swept in by the exemption
    substantive = [r for r in report.claims if r.claim.text != salutation]
    assert substantive and all(r.verdict.verdict != "not_applicable" for r in substantive)


# ── 6. the ADR-060 outcome critic — no sentence-filter mechanism to pin ─────
#
# The brief's check ("grep services/critic*") finds nothing: the module is
# named services/outcome_critic.py, not services/critic*.py. Read directly
# (not grepped) instead: it is a keyword-LEDGER CONCEPT presence/coherence
# diff between the CV and the letter (ADR-060 module docstring — "for every
# claimable Keyword-Ledger concept, whether it is present in the CV's own
# text, whether it is present in the letter's own text..."), never a
# per-SENTENCE classifier over the whole document the way extract.py's
# sentence-triage seam is. A generic salutation carries no keyword-ledger
# concept and no tenure/depth figure, so it is structurally never a candidate
# for this critic's judgement at all — there is no sentence-filter mechanism
# here to add the salutation to, and no separate fix site.
