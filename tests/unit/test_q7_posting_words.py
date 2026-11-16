# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q7 — the posting's words are not the person's claim (ruling Q7-1,
MD-Q15; ADR-048 / ADR-052 amended 2026-11-16).

Captured in delivery 2a (synthetic personas; names here are fixtures):

* run 3, CV summary[0]: "Leiter Operations mit 14 Jahren Erfahrung in der
  diskreten Fertigung …" for a Produktionsleiter — Oracle ``grounded`` (token
  coverage against the vault's own summary sentence);
* run 3, bullet: "… und übernahm Führung und Entwicklung der Mitarbeitenden" — a
  below-the-bar ``partial`` row rendered as plain CLAIMABLE, demanded literally by
  the coverage scan, graded ``grounded`` by the figure arm.

Every deterministic guard here has a mutation the named test kills
(``q7/report.md`` lists them with their runs).
"""
from __future__ import annotations

import asyncio
import copy

from applire.services import keyword_ledger as kl
from applire.services.oracle.selfaudit import build_self_audit_report

WID = "22222222-2222-2222-2222-222222222222"
VAULT = {
    "professional_summary": {
        "de": "Produktions- und Betriebsleiter mit 14 Jahren Erfahrung in der diskreten "
              "Fertigung. Schwerpunkte: Lean Management, Führung gewerblicher Teams.",
        "en": "",
    },
    "work_experience": [{
        "id": WID, "company": "Muster Kunststoff GmbH", "role": "Produktionsleiter",
        "start_date": "2010-01", "end_date": None, "location": "Kassel",
        "responsibilities": [
            "Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden im "
            "Dreischichtbetrieb.",
            "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher.",
        ],
    }],
    "education": [], "skills": [{"name": "Lean Management", "level": None, "category": "domain"}],
    "languages": [], "certifications": [],
    "contact": {"first_name": "Jan", "last_name": "Muster", "email": "jan@example.com"},
}

HEADLINE = ("Leiter Operations mit 14 Jahren Erfahrung in der diskreten Fertigung sowie "
            "disziplinarischer Führung im Mehrschichtbetrieb.")
HELD_HEADLINE = HEADLINE.replace("Leiter Operations", "Produktionsleiter")
LABEL_BULLET = ("Verantwortete zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb "
                "und übernahm Führung und Entwicklung der Mitarbeitenden.")
OWN_BULLET = "Verantwortete zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb."

QUOTE = "Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb."
PARTIAL = {
    "concept": "Führung und Entwicklung", "surface_forms": ["Führung und Entwicklung"],
    "status": "partial", "claimable": True,
    "evidence": "Führt 38 Mitarbeitende; Entwicklung nicht ausdrücklich beschrieben.",
    "backing": {"quote": QUOTE},
}
ADJACENT = {
    "concept": "SAP S/4HANA", "surface_forms": ["S/4HANA"], "status": "partial",
    "claimable": True, "adjacent_evidence": "SAP ECC", "evidence": "ECC only",
}
DIRECT = {
    "concept": "Lean Management", "surface_forms": ["Lean Management"], "status": "direct",
    "claimable": True, "evidence": "Schwerpunkt Lean Management",
    "backing": {"quote": "Lean Management"},
}
DIRECT_ABSENT = {  # a direct row whose word the vault never uses: not a partial, never stamped
    "concept": "Kunststoffverarbeitung", "surface_forms": [], "status": "direct",
    "claimable": True, "evidence": "Spritzguss",
}
HELD_PARTIAL = {  # a below-the-bar partial whose word the person DOES use
    "concept": "Lean Management", "surface_forms": [], "status": "partial",
    "claimable": True, "evidence": "Schwerpunkt, aber ohne Zertifikat",
}


def _ledger():
    return [copy.deepcopy(r) for r in (PARTIAL, ADJACENT, DIRECT, HELD_PARTIAL, DIRECT_ABSENT)]


def _tailored(summary, bullets, skills=("Lean Management",)):
    return {
        "contact": {"name": "Jan Muster", "email": "jan@example.com"},
        "summary": summary,
        "work_history": [{"id": WID, "company": "Muster Kunststoff GmbH", "role": "Produktionsleiter",
                          "start_date": "2010-01", "end_date": None, "bullets": list(bullets)}],
        "skills": list(skills),
    }


def _audit(summary, bullets, *, skills=("Lean Management",), language="de", ledger=None):
    return asyncio.run(build_self_audit_report(
        VAULT, tailored_data=_tailored(summary, bullets, skills), document_language=language,
        keyword_ledger=_ledger() if ledger is None else ledger,
    ))


def _row(report, text):
    from applire.schemas.oracle import ClaimResult

    return next(ClaimResult(**c) for c in report["claims"] if c["claim"]["text"] == text)


def _keys(report):
    return [c["verdict"].get("detail_key") for c in (report or {}).get("claims", [])]


# ── the ledger stamp (a fact: literal presence, ADR-062 cl. 1) ─────────────────

def test_only_a_below_the_bar_partial_without_the_persons_word_is_stamped():
    stamped = {e["concept"]: e.get("posting_word_only") for e in
               kl.stamp_posting_word_only(_ledger(), VAULT)}
    assert stamped == {"Führung und Entwicklung": True, "SAP S/4HANA": None,
                       "Lean Management": None, "Kunststoffverarbeitung": None}


def test_a_stale_stamp_is_dropped_and_rows_are_not_mutated():
    rows = _ledger()
    rows[2]["posting_word_only"] = True          # a direct row carrying a stale stamp
    before = copy.deepcopy(rows)
    out = kl.stamp_posting_word_only(rows, VAULT)
    assert "posting_word_only" not in out[2]
    assert rows == before


def test_the_persons_own_denial_statement_counts_as_their_word():
    """Delivery 2a (Daniel): "That is the infrastructure as code I did there; I did
    not write the cluster provisioning itself" — filed under a denial. The coverage
    corpus strips it; this predicate must not."""
    vault = copy.deepcopy(VAULT)
    vault["metadata"] = {"denied_concepts": [{
        "concept": "cluster provisioning",
        "statement": "Helm charts in Git, rolled out by ArgoCD. That is the infrastructure "
                     "as code I did there; I did not write the cluster provisioning itself.",
    }]}
    row = {"concept": "Infrastructure as code", "surface_forms": [], "status": "partial",
           "claimable": True, "evidence": "Helm/ArgoCD"}
    assert kl.posting_word_partial_rows([row], vault) == []
    assert kl.posting_word_partial_rows([row], VAULT) == [row]


def test_the_build_and_the_read_seam_both_stamp():
    ledger, changed = kl.refresh_ledger_against_vault(
        [copy.deepcopy(PARTIAL), copy.deepcopy(DIRECT)], VAULT, seam="test")
    assert ledger[0].get("posting_word_only") is True and changed
    assert "posting_word_only" not in ledger[1]
    built = kl.build_keyword_ledger(
        [{"concept": "Führung und Entwicklung", "status": "partial",
          "evidence": "Entwicklung nicht beschrieben", "evidence_quote": QUOTE,
          "surface_forms": ["Führung und Entwicklung"]}],
        ["Führung und Entwicklung"], [], [], profile_json=VAULT,
    )
    row = next(e for e in built if e["concept"] == "Führung und Entwicklung")
    assert row.get("posting_word_only") is True


# ── no literal demand (the coverage scan and the underclaim demand) ─────────────

def test_the_coverage_scan_never_demands_the_posting_word():
    ledger = kl.stamp_posting_word_only(_ledger(), VAULT)
    draft = {"summary": HELD_HEADLINE, "work": [{"id": WID, "bullets": [OWN_BULLET]}], "skills": []}
    missing = {e["concept"] for e in kl.verified_missing_claimable(draft, ledger)}
    assert "Führung und Entwicklung" not in missing
    assert "Lean Management" in missing                 # a held row is still demanded


def test_the_underclaim_demand_never_names_the_posting_word():
    ledger = kl.stamp_posting_word_only(_ledger(), VAULT)
    names = {e["concept"] for e in kl.underclaim_candidate_entries(ledger)}
    assert "Führung und Entwicklung" not in names and "Lean Management" in names


def test_the_ats_panel_does_not_call_the_posting_word_a_surfacing_miss():
    ledger = kl.stamp_posting_word_only(_ledger(), VAULT)
    forms = {f.casefold() for f in kl.claimable_surface_forms(ledger)}
    assert "führung und entwicklung" not in forms and "lean management" in forms


# ── the writers' block ──────────────────────────────────────────────────────────

def test_the_writer_block_marks_the_row_and_shows_the_profiles_own_words():
    block = kl.render_ledger_prompt_block(kl.stamp_posting_word_only(_ledger(), VAULT))
    line = next(ln for ln in block.splitlines() if ln.startswith("  - Führung und Entwicklung"))
    assert "PARTLY MET" in line and "never write" in line and QUOTE in line
    assert "[forms:" not in line.split("—")[0]          # not rendered like a direct row
    direct = next(ln for ln in block.splitlines() if ln.startswith("  - Lean Management"))
    assert "PARTLY MET" not in direct


def test_the_reviewer_is_not_told_the_posting_word_is_truthfully_supported():
    """Re-run rec 289: listed under "the candidate truthfully supports these" with
    the label as its form, the row told the reviewer the label was grounded."""
    block = kl.render_ledger_reviewer_block(kl.stamp_posting_word_only(_ledger(), VAULT))
    line = next(ln for ln in block.splitlines() if ln.startswith("  - Führung und Entwicklung"))
    assert "PARTLY MET" in line and "overstates" in line and "[forms:" not in line
    direct = next(ln for ln in block.splitlines() if ln.startswith("  - Lean Management"))
    assert "PARTLY MET" not in direct


# ── the writer's and reviewer's title rule (ruling Q7-1, v2 only) ───────────────

def test_the_v2_writer_and_reviewer_name_the_candidates_own_title():
    from applire.prompts import cv_tailoring as w
    from applire.prompts import review_cv_tailoring as cv

    assert w.V2_TITLE_PHRASE_RULE in w.V2_SYSTEM_PROMPT
    assert "never the posting's role_title" in w.V2_SYSTEM_PROMPT
    assert "role_title" not in w.SYSTEM_PROMPT.split("open with the title phrase")[1][:40]
    for p in (cv.V2_REVIEW_SYSTEM_PROMPT, cv.V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
        assert "the posting's role title is the employer's" in p


# ── the Oracle: the two facts, on the captured sentences ────────────────────────

def test_the_captured_headline_is_unbacked_and_the_held_title_stays_grounded():
    """Two rules on one sentence (the lexical coverage arm and the title fact),
    on the captured sentence: the arm grades it grounded, the fact overrides."""
    report = _audit(HEADLINE, [OWN_BULLET])
    row = _row(report, HEADLINE)
    assert (row.verdict.verdict, row.verdict.detail_key) == ("unbacked", "titleNotHeld")
    assert row.verdict.detail_params == {"title": "leiter operations"}
    held = _row(_audit(HELD_HEADLINE, [OWN_BULLET]), HELD_HEADLINE)
    assert held.verdict.verdict == "grounded" and held.verdict.detail_key != "titleNotHeld"


def test_a_cross_language_cv_gets_no_title_finding():
    report = _audit("Head of Operations with 14 years of experience in discrete manufacturing.",
                    [OWN_BULLET], language="en")
    assert "titleNotHeld" not in _keys(report)


def test_a_letter_and_a_cross_language_pair_get_no_title_finding():
    """A letter speaks of the job applied for ("Als Leiter Operations würde ich …");
    a translated title is ruled behaviour (R-2) the vocabulary cannot match."""
    from applire.schemas.oracle import Claim, ClaimResult, ClaimVerdict
    from applire.services.oracle.posting_words import apply_posting_word_checks

    res = [ClaimResult(claim=Claim(text=HEADLINE, location="summary[0]"),
                       verdict=ClaimVerdict(verdict="grounded", checker="grounding"))]
    for is_cv, same_language in ((False, True), (True, False)):
        out = apply_posting_word_checks(res, profile=VAULT, keyword_ledger=[],
                                        is_cv=is_cv, same_language=same_language)
        assert out[0] is res[0]
    out = apply_posting_word_checks(res, profile=VAULT, keyword_ledger=[], is_cv=True, same_language=True)
    assert out[0].verdict.detail_key == "titleNotHeld"


def test_the_captured_label_bullet_is_left_open_and_the_own_bullet_stays_grounded():
    report = _audit(HELD_HEADLINE, [LABEL_BULLET, OWN_BULLET])
    row = _row(report, LABEL_BULLET)
    assert (row.verdict.verdict, row.verdict.detail_key) == ("unverifiable", "postingTermNotHeld")
    assert row.verdict.detail_params == {"term": "Führung und Entwicklung"}
    assert _row(report, OWN_BULLET).verdict.verdict == "grounded"


def test_a_posting_word_chip_is_not_grounded_by_the_partial_rows_quote():
    report = _audit(HELD_HEADLINE, [OWN_BULLET], skills=("Führung und Entwicklung", "Lean Management"))
    chip = _row(report, "Führung und Entwicklung")
    assert (chip.verdict.verdict, chip.verdict.detail_key) == ("unverifiable", "postingTermNotHeld")
    assert _row(report, "Lean Management").verdict.verdict == "grounded"


def test_across_languages_the_posting_word_is_left_open_with_its_own_key():
    """The faithful translation misses the literal check ("led three shift managers"
    for "über drei Schichtleiter"), so the detail must not say "a word your profile
    never uses" (ADR-068); the verdict is the same."""
    from applire.schemas.oracle import Claim, ClaimResult, ClaimVerdict
    from applire.services.oracle.posting_words import apply_posting_word_checks

    res = [ClaimResult(claim=Claim(text=LABEL_BULLET, location="work_history[0].bullets[0]",
                                   kind="bullet"),
                       verdict=ClaimVerdict(verdict="grounded", checker="numbers"))]
    keys = {}
    for same in (True, False):
        out = apply_posting_word_checks(res, profile=VAULT, keyword_ledger=_ledger(),
                                        is_cv=True, same_language=same)
        keys[same] = (out[0].verdict.verdict, out[0].verdict.detail_key)
    assert keys == {True: ("unverifiable", "postingTermNotHeld"),
                    False: ("unverifiable", "postingTermOtherLanguage")}


def test_without_a_ledger_nothing_changes():
    report = _audit(HELD_HEADLINE, [LABEL_BULLET], ledger=[])
    assert _row(report, LABEL_BULLET).verdict.detail_key != "postingTermNotHeld"


def test_both_new_keys_are_registered_with_their_class():
    from applire.services import finding_registry as fr

    assert fr.REGISTRY["oracle:titleNotHeld"].cls == "F"
    assert fr.REGISTRY["oracle:postingTermNotHeld"].cls == "S"
    assert fr.REGISTRY["oracle:postingTermOtherLanguage"].cls == "S"
