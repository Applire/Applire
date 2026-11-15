# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q5 (c), ADR-064 amended 2026-11-15: one denial has one scope, the
stored one, and the ledger and the Oracle read it alike.

Captured case (delivery 1b, synthetic persona Katrin Hoffmann): the interview
recorded "Weitere eigene Projektcontrolling-Projekte geleitet" / "eigene
Projektcontrolling-Projekte (geleitet)" — an ASPECT: no further own projects;
she introduced the POC valuation for six projects. The ledger kept
"Projektcontrolling" claimable (all three runs). The Oracle's cover read the same
record by compound containment as covering the bare chip: it closed the
equivalence seam and the ledger arm (graded ``unbacked``, run 2) and let the
delivery drop remove the chip. The cover is now the ledger's two arms: the
record's own words DECLARE the term, or the job's ledger holds the term's row as
``denied`` (bound by ``denied_by``)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from applire.services.oracle import audit_document
from applire.services.oracle.audit import _claimable_index, _denial_covers
from applire.services.oracle.backing import (
    ledger_backings,
    skill_backed_by_ledger,
    stored_denial_cover,
    with_ledger_denials,
)

_FILES = Path(__file__).resolve().parents[1] / "files"
RUN1 = json.loads((_FILES / "q3_md_q8_katrin_delivery_1b.json").read_text())
RERUN = json.loads((_FILES / "q3_md_q8_katrin_rerun_denied.json").read_text())


@pytest.mark.parametrize("fixture", [RUN1["profile"], RERUN["profile"]])
def test_an_aspect_denial_does_not_cover_the_bare_skill(fixture):
    idx = _claimable_index(fixture)
    assert not stored_denial_cover(["Projektcontrolling"], idx)
    # the same containment read also covered "Führung" under "Disziplinarische
    # Führung" (she leads functionally)
    assert not stored_denial_cover(["Führung"], idx)


@pytest.mark.parametrize("term", ["US-GAAP", "Make-or-buy-Entscheidungen", "disziplinarische Führung"])
def test_a_term_the_record_names_stays_covered(term):
    assert stored_denial_cover([term], _claimable_index(RERUN["profile"]))
    assert _denial_covers(term, _claimable_index(RERUN["profile"]))


def test_the_ledger_bound_row_is_the_stored_decision():
    idx = _claimable_index(RERUN["profile"])
    assert not _denial_covers("Investitionscontrolling", idx)
    ledger = [{"concept": "Investitionscontrolling", "surface_forms": ["Investitionscontrolling", "Investment Controlling"],
               "status": "denied", "claimable": False, "denied_by": "Make-or-buy-Entscheidungen"}]
    bound = with_ledger_denials(idx, ledger)
    assert stored_denial_cover(["Investment controlling"], bound) == ["Make-or-buy-Entscheidungen"]
    # a claimable row decides "not denied", a gap row decides nothing
    for status in ("partial", "gap"):
        other = with_ledger_denials(idx, [{**ledger[0], "status": status}])
        assert not _denial_covers("Investitionscontrolling", other)


class _Judge:
    def __init__(self, quote):
        self.quote = quote
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.prompts.append(prompt)
        return {"items": [{"index": 0, "corresponds": True, "vault_quote": self.quote}]}


def _chip(report, text):
    (r,) = [c for c in report.claims if c.claim.kind == "skill" and c.claim.text == text]
    return r.verdict


@pytest.mark.asyncio
async def test_the_oracle_opens_the_equivalence_seam_for_the_aspect_denied_chip():
    """Seam (audit.py, skill branch, ``_denial_covers`` before the equivalence
    judge): the chip is no longer closed by the aspect denial — the judge grades
    it against the vault (here: grants on the POC bullet)."""
    quote = "Monatliche Bewertung für sechs Großprojekte"
    prof = json.loads(json.dumps(RUN1["profile"]))
    prof["work_experience"][0].setdefault("responsibilities", []).append(
        quote + " und Berichterstattung von Abweichungen an das Management."
    )
    judge = _Judge(quote)
    rep = await audit_document("cv", prof, tailored_data={"skills": ["Projektcontrolling"]},
                               provider=judge, document_language="de",
                               keyword_ledger=RUN1["keyword_ledger"])
    v = _chip(rep, "Projektcontrolling")
    assert v.verdict == "grounded" and v.detail_key == "skillEquivalent", v
    assert len(judge.prompts) == 1


@pytest.mark.asyncio
async def test_a_declared_chip_never_reaches_the_judge():
    judge = _Judge("US-GAAP")
    rep = await audit_document("cv", RERUN["profile"], tailored_data={"skills": ["US-GAAP"]},
                               provider=judge, document_language="de")
    v = _chip(rep, "US-GAAP")
    assert v.verdict == "unbacked" and v.detail_key == "skillNoEvidence"
    assert judge.prompts == []


def test_ledger_backing_rows_and_the_review_regrounding_read_the_stored_scope():
    """Seams (backing.ledger_backings, backing.skill_backed_by_ledger): a backed row
    whose concept the record only CONTAINS keeps its quote; a row the record names
    is refused."""
    prof = RERUN["profile"]
    quote = prof["work_experience"][0]["responsibilities"][0]
    rows = [
        {"concept": "Projektcontrolling", "surface_forms": ["Projektcontrolling"], "status": "direct",
         "claimable": True, "sources": ["required"], "backing": {"quote": quote}},
        {"concept": "US-GAAP", "surface_forms": ["US-GAAP"], "status": "direct",
         "claimable": True, "sources": ["required"], "backing": {"quote": quote}},
    ]
    idx = _claimable_index(prof)
    assert [b.concept for b in ledger_backings(rows, prof, idx)] == ["Projektcontrolling"]
    assert skill_backed_by_ledger("Projektcontrolling", rows, prof, idx) is not None
    assert skill_backed_by_ledger("US-GAAP", rows, prof, idx) is None


# ── MD-Q11 F6: a shorter name of the denied thing, and a denial sentence ─────
#
# The reviewer's probe (adv-2a probe_c.py): the stored-scope reader lost real
# denials of the denied NAME itself. Covered again: a shorter/alias name of the
# product, and the one term a first-person denial sentence names. Still not
# covered: an aspect ("weitere", "Projekte geleitet"), a specialisation, level or
# role ("SAP S/4HANA" is not "SAP"; "Scrum Master" is not "Scrum", ADR-064/052).


def _denied_index(*concepts: str):
    return _claimable_index({
        "personal_info": {"name": "X"}, "skills": [], "work_experience": [],
        "metadata": {"denied_concepts": [
            {"concept": c, "statement": c, "source": "interview", "date": "2026-11-15"} for c in concepts]},
    })


@pytest.mark.parametrize("denial,chip", [
    ("Microsoft Power BI", "Power BI"),
    ("SAP S/4HANA", "S/4HANA"),
    ("Google Cloud Platform", "Google Cloud"),
    ("Google Cloud Platform", "GCP"),
    ("Adobe Premiere Pro", "Premiere"),
    ("Mit US-GAAP habe ich keine Erfahrung", "US-GAAP"),
    ("Mit US-GAAP habe ich leider noch keine Erfahrung.", "US-GAAP"),
    ("I have no experience with Kubernetes", "Kubernetes"),
    ("keine Erfahrung mit US-GAAP", "US-GAAP"),
])
def test_a_shorter_name_or_the_term_a_denial_sentence_names_is_covered(denial, chip):
    idx = _denied_index(denial)
    assert stored_denial_cover([chip], idx) == [denial]
    assert _denial_covers(chip, idx)


@pytest.mark.parametrize("denial,chip", [
    ("Scrum Master", "Scrum"),                        # the role is not the method (ADR-064)
    ("Lean Six Sigma Black Belt", "Six Sigma"),       # the level is not the method
    ("Lean Six Sigma Black Belt", "Lean"),
    ("SAP S/4HANA", "SAP"),                           # the product line is not the vendor
    ("Excel VBA", "Excel"),                           # a specialisation is not the area
    ("Advanced Excel", "Excel"),                      # a narrowing qualifier
    ("keine weitere Erfahrung mit SAP", "SAP"),       # an aspect
    ("Weitere eigene Projektcontrolling-Projekte geleitet", "Projektcontrolling"),
    ("Projektleitung im Anlagenbau", "Projektleitung"),
    ("Kubernetes-Cluster-Administration", "Kubernetes"),  # a hyphen-part is never a token
    ("Microsoft Power BI", "Microsoft"),
])
def test_an_aspect_level_or_specialisation_does_not_cover(denial, chip):
    assert stored_denial_cover([chip], _denied_index(denial)) == []


@pytest.mark.parametrize("denial,chip", [
    ("Disziplinarische Führung", "Führung"),
    ("Fachliche Führung", "Führung"),
    ("Strategische Planung", "Planung"),
])
def test_a_leading_german_adjective_narrows(denial, chip):
    assert stored_denial_cover([chip], _denied_index(denial)) == []


@pytest.mark.parametrize("denial,chip", [
    ("Der Microsoft Power BI Service", "Power BI"),           # a function word: prose, not a name
    ("Microsoft Dynamics Power BI Embedded Analytics Suite", "Power BI"),  # longer than a name
])
def test_only_a_name_shaped_record_drops_words(denial, chip):
    assert stored_denial_cover([chip], _denied_index(denial)) == []
