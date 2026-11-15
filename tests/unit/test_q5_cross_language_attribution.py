# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q5 (a), ADR-068 amended 2026-11-15: a misattribution red flag in a
cross-language pair is not raised on lexical evidence alone.

Captured (delivery 1b runs 1 and 2, synthetic persona Stefan Brandt, German vault,
English CV): the English bullet under w3 Industriemechaniker shares no word with
its role's German sentence "Facharbeiter in der Instandhaltung, später
Vorarbeiter."; its only lexical evidence was Weberit's English interview sentence
(work_experience[0].responsibilities[8], "… maintenance material"), so the flag
fired on a correctly placed bullet and set stop_and_fix. The tie across languages
is a verbatim span of a unit the ROLE OWNS, cited by Seam A's judge; code never
translates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from applire.services.oracle import audit_document

FIX = json.loads((Path(__file__).resolve().parents[1] / "files/q5_cross_delivery_1b.json").read_text())
PROF = FIX["profile"]
W3 = next(w["id"] for w in PROF["work_experience"] if w["role"] == FIX["role"])
W1 = next(w["id"] for w in PROF["work_experience"] if w["role"] == "Produktionsleiter")
OWN = "Facharbeiter in der Instandhaltung, später Vorarbeiter."
FOREIGN = "Worked with PP and MM daily for materials planning and purchase requisitions for maintenance material."


class _Judge:
    def __init__(self, corresponds, quote):
        self.answer = {"index": 0, "corresponds": corresponds, "vault_quote": quote}
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.prompts.append(prompt)
        return {"items": [self.answer]}


def _cv(bullet, wid=W3):
    return {"summary": "", "skills": [], "work_history": [
        {"id": wid, "company": "Rasselstein Umformtechnik GmbH", "role": "x", "bullets": [bullet]}]}


async def _verdict(bullet, provider, *, wid=W3, language="en"):
    rep = await audit_document("cv", PROF, tailored_data=_cv(bullet, wid), provider=provider,
                               document_language=language)
    (c,) = [c for c in rep.claims if c.claim.text == bullet]
    return c.verdict


@pytest.mark.asyncio
@pytest.mark.parametrize("bullet", [FIX["bullet_run1"], FIX["bullet_run2"]])
async def test_the_captured_bullet_is_grounded_on_its_own_roles_unit(bullet):
    judge = _Judge(True, OWN)
    v = await _verdict(bullet, judge)
    assert v.verdict == "grounded" and v.detail_key == "crossLanguageEquivalent", v
    assert [e.ref for e in v.evidence][0] == "work_experience[2].responsibilities[0]"
    # the judge saw the role's own units only — never the foreign unit that fired the flag
    assert OWN in judge.prompts[0] and FOREIGN not in judge.prompts[0]
    # only the anchored position and its same-employer siblings: no sentence of another employer
    w1 = next(w for w in PROF["work_experience"] if w["id"] == W1)
    assert not [r for r in w1.get("responsibilities") or [] if r in judge.prompts[0]]


@pytest.mark.asyncio
async def test_a_no_keeps_the_red_flag():
    """The counter-case: a bullet the judge does not find in its role's evidence
    stays misattributed (a bullet really moved from another role)."""
    v = await _verdict(FIX["bullet_run2"], _Judge(False, OWN))
    assert v.verdict == "misattributed"
    assert [e.ref for e in v.evidence][0] == "work_experience[0].responsibilities[8]"


@pytest.mark.asyncio
async def test_a_grant_citing_a_foreign_unit_never_grounds():
    """The id fact: only the role's own units are offered, so citing another
    role's sentence is no citation at all — unverifiable, never grounded."""
    v = await _verdict(FIX["bullet_run2"], _Judge(True, FOREIGN))
    assert v.verdict == "unverifiable" and v.detail_key == "crossLanguageUnconfirmed"


@pytest.mark.asyncio
async def test_no_judgement_is_no_accusation():
    v = await _verdict(FIX["bullet_run1"], None)
    assert v.verdict == "unverifiable" and v.detail_key == "crossLanguageUnconfirmed"


GERMAN_ONLY = {
    "personal_info": {"name": "Erika Beispiel", "location": "Musterstadt"},
    "professional_summary": {"de": "Produktionsleiterin mit langjähriger Erfahrung in der Kunststofffertigung."},
    "skills": [{"name": "Shopfloor-Management", "category": "technical"}],
    "work_experience": [
        {"id": "g1", "company": "Alpha Kunststoff GmbH", "role": "Produktionsleiterin",
         "responsibilities": ["Einführung von Shopfloor-Management und KVP-Routinen in beiden Hallen."]},
        {"id": "g2", "company": "Beta Metall AG", "role": "Schichtleiterin",
         "responsibilities": ["Führung einer Schicht in der Blechumformung mit wechselnden Aufträgen."]},
    ],
}


@pytest.mark.asyncio
async def test_a_same_language_bullet_moved_to_another_role_keeps_the_deterministic_flag():
    """Single-language pair: the flag is deterministic and no judge is asked."""
    bullet = "Einführung von Shopfloor-Management und KVP-Routinen in beiden Hallen."
    judge = _Judge(True, "Führung einer Schicht in der Blechumformung mit wechselnden Aufträgen.")
    rep = await audit_document("cv", GERMAN_ONLY, provider=judge, document_language="de", tailored_data={
        "summary": "", "skills": [], "work_history": [
            {"id": "g2", "company": "Beta Metall AG", "role": "Schichtleiterin", "bullets": [bullet]}]})
    (c,) = [c for c in rep.claims if c.claim.text == bullet]
    assert c.verdict.verdict == "misattributed"
    assert judge.prompts == []


@pytest.mark.asyncio
async def test_a_foreign_figure_stays_deterministic_across_languages():
    """A digit is language-free: the per-figure flag never consults the judge."""
    bullet = "Led a team of 38 employees in three-shift operation."
    judge = _Judge(True, OWN)
    v = await _verdict(bullet, judge)
    assert v.verdict == "misattributed"
    assert not any(OWN in p for p in judge.prompts)


@pytest.mark.asyncio
async def test_an_english_bullet_moved_from_another_role_stays_misattributed_on_a_no():
    bullet = "Led the master-data cleanup for routings."
    v = await _verdict(bullet, _Judge(False, OWN))
    assert v.verdict == "misattributed"
