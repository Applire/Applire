# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blind Kaile probe, 2026-09-19 — a skill chip is grounded by a WHOLE TOKEN,
and a job's own ledger may say which word names the competence.

Ground truth (dev DB ``generated_cvs`` 1b581b82…, job ``43d7332b…``,
synthetic persona Katrin Hoffmann):

* **False positive.** The delivered skills section carried "Gruppe",
  "Vertrieb" and "Produktion" — three bare nouns from the job ad's prose
  ("Self-Service-Dashboards für Vertrieb, Produktion und Geschäftsführung";
  "Gruppenebene"), none of them among the vault's 20 skill names. The Oracle
  graded all three ``grounded``, by bare SUBSTRING against a vault that says
  "Vertriebsreporting" and "Produktionsstandorte". The blind hiring manager
  read the section as "ATS-Keyword-Stuffing ohne Beleg".
* **False negative, same document.** "Produktionscontrolling" graded
  ``unbacked`` — "Skill … has no vault evidence." — although the job's own
  keyword ledger carries it as ``status: direct, claimable: true`` with
  ``surface_forms: [Produktionscontrolling, Werkscontrolling, Production
  Controlling]`` and evidence "Acht Jahre Werkscontrolling …". The vault
  names the competence in the candidate's vocabulary; the document names it
  in the job's.

Both halves are one defect: the matcher was asking a COVERAGE question
("does this string occur") where a GROUNDING question was due ("does the
vault name this competence"). ``surface_present_whole_token`` answers the
second; the claimable ledger row answers the vocabulary half, as an
already-recorded fact rather than a fresh equivalence judgement.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document
from applire.services.oracle.matchers.grounding import ground_skill_claim
from applire.services.oracle.matchers.vault import build_vault_index

PROFILE = {
    "personal_info": {"name": "Katrin Hoffmann"},
    "skills": [{"name": "Werkscontrolling"}, {"name": "Konsolidierung"}],
    "work_experience": [
        {
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Senior Controllerin",
            "is_current": True,
            "responsibilities": [
                "Acht Jahre Werkscontrolling an zwei Produktionsstandorten "
                "mit Herstellkosten und Bestandsbewertung.",
                "Monatliches Vertriebsreporting in Excel aufgebaut.",
            ],
        }
    ],
}

# The job's own ledger: the "Produktionscontrolling" row is a stated
# REQUIREMENT of the posting; "Vertrieb" and "Produktion" are bare nouns
# scraped from its prose (sources == ["keyword"]).
LEDGER = [
    {
        "concept": "Produktionscontrolling",
        "status": "direct",
        "claimable": True,
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Acht Jahre Werkscontrolling an zwei Produktionsstandorten.",
        "surface_forms": [
            "Produktionscontrolling",
            "Werkscontrolling",
            "Production Controlling",
        ],
    },
    {
        "concept": "Vertrieb",
        "status": "direct",
        "claimable": True,
        "sources": ["keyword"],
        "fit_weight": 0.0,
        "evidence": "Vertriebsreporting in Excel aufgebaut.",
        "surface_forms": ["Vertrieb", "Vertriebsreporting", "Sales"],
    },
]


def _index():
    return build_vault_index(PROFILE)


def test_a_substring_of_a_vault_word_no_longer_grounds_a_skill_chip():
    """The false positive. "Vertrieb" occurs only INSIDE
    "Vertriebsreporting"; "Produktion" only inside "Produktionsstandorten"."""
    idx = _index()
    assert ground_skill_claim("Vertrieb", idx) is None
    assert ground_skill_claim("Produktion", idx) is None


def test_a_whole_token_in_the_vault_still_grounds():
    """Over-drop guard: the bullet-evidenced competences the same document
    carries legitimately are whole tokens and keep grounding."""
    idx = _index()
    for name in ("Werkscontrolling", "Herstellkosten", "Vertriebsreporting"):
        assert ground_skill_claim(name, idx) is not None, name


def test_ledger_surface_form_grounds_the_jobs_own_vocabulary():
    """The false negative: the ledger row is the recorded fact that
    "Produktionscontrolling" and "Werkscontrolling" name one competence, so
    the grounding follows it to the vault unit that actually backs it."""
    idx = _index()
    assert ground_skill_claim("Produktionscontrolling", idx) is None
    unit = ground_skill_claim(
        "Produktionscontrolling",
        idx,
        [["Produktionscontrolling", "Werkscontrolling", "Production Controlling"]],
    )
    assert unit is not None
    assert "werkscontrolling" in unit.text_norm


def test_ledger_arm_never_rescues_a_form_the_vault_does_not_name():
    """A ledger row whose siblings are equally absent from the vault grounds
    nothing — the arm follows the row to real evidence, it never substitutes
    for it."""
    idx = _index()
    assert (
        ground_skill_claim(
            "Make-or-buy-Entscheidungen",
            idx,
            [["Make-or-buy-Entscheidungen", "Make or Buy", "Eigenfertigung"]],
        )
        is None
    )


@pytest.mark.asyncio
async def test_delivered_document_verdicts_match_the_ruled_contract():
    """End to end on the probe's own chip set: the JD nouns lose their
    grounding, the ledger synonym gains it, everything else is untouched."""
    tailored = {
        "skills": [
            "Werkscontrolling",
            "Konsolidierung",
            "Produktionscontrolling",
            "Vertrieb",
            "Produktion",
        ],
        "work_history": [],
    }
    report = await audit_document(
        "cv", PROFILE, tailored_data=tailored, keyword_ledger=LEDGER
    )
    by_text = {r.claim.text: r.verdict.verdict for r in report.claims if r.claim.kind == "skill"}
    assert by_text["Werkscontrolling"] == "grounded"
    assert by_text["Konsolidierung"] == "grounded"
    assert by_text["Produktionscontrolling"] == "grounded"
    # ``Vertrieb``'s row is JD-keyword-only, so the ledger arm does not reach
    # it either — the generator refuses to put such a name on the page and the
    # audit refuses to endorse it (ADR-066 clause 2, one shared predicate).
    assert by_text["Vertrieb"] == "unbacked"
    assert by_text["Produktion"] == "unbacked"


@pytest.mark.asyncio
async def test_without_a_ledger_the_behaviour_is_vault_only():
    """Every caller with no job on hand (the agent door, the letter path)
    keeps the vault-only behaviour."""
    tailored = {"skills": ["Produktionscontrolling", "Werkscontrolling"], "work_history": []}
    report = await audit_document("cv", PROFILE, tailored_data=tailored)
    by_text = {r.claim.text: r.verdict.verdict for r in report.claims}
    assert by_text["Werkscontrolling"] == "grounded"
    assert by_text["Produktionscontrolling"] == "unbacked"
