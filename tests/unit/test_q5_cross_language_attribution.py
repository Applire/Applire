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
    # only the anchored role's own CONTENT units in the other language (MD-Q11 F1,
    # RULING Q5-3): no other role's sentence (not even a same-employer sibling's), no
    # role title, no company name
    for w in PROF["work_experience"]:
        if w["id"] != W3:
            assert not [r for r in w.get("responsibilities") or [] if r in judge.prompts[0]]
    assert "Industriemechaniker" not in judge.prompts[0].split("Claim")[-1]


@pytest.mark.asyncio
async def test_a_no_keeps_the_red_flag():
    """The counter-case: a bullet the judge does not find in its role's evidence
    stays misattributed (a bullet really moved from another role)."""
    v = await _verdict(FIX["bullet_run2"], _Judge(False, OWN))
    assert v.verdict == "misattributed"
    assert [e.ref for e in v.evidence][0] == "work_experience[0].responsibilities[8]"


class _Raw:
    """A judge double that returns ``reply`` verbatim (or raises it)."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _item(corresponds, quote):
    return {"items": [{"index": 0, "corresponds": corresponds, "vault_quote": quote}]}


#: MD-Q11 F1 + F2, the reviewer's probe (adv-2a test_probe_a.py): the Weberit
#: sentence, verbatim, rendered under w3. No answer below is positive evidence, so
#: every one keeps the flag. Before the fix: the fragment/title/company citations
#: grounded the bullet, and the no-judgement answers made it ``unverifiable``.
_NOT_EVIDENCE = {
    "fragment_one_char": _item(True, "a"),
    "fragment_two_words": _item(True, "in der"),
    "fragment_one_word": _item(True, "Instandhaltung"),
    "role_title_unit": _item(True, "Industriemechaniker"),
    "company_unit": _item(True, "Rasselstein Umformtechnik GmbH"),
    "sibling_role_unit": _item(True, "Führung einer Schicht mit 14 Mitarbeitenden in der Blechumformung."),
    "foreign_unit": _item(True, FOREIGN),
    "empty_quote": _item(True, ""),
    "truthy_string": _item("yes", OWN),
    "empty_response": {},
    "no_items": {"items": []},
    "provider_error": RuntimeError("boom"),
    "no": _item(False, OWN),
    "uncertain": _item("uncertain", OWN),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(_NOT_EVIDENCE))
async def test_only_a_verified_whole_unit_citation_lifts_the_flag(name):
    v = await _verdict(FOREIGN, _Raw(_NOT_EVIDENCE[name]))
    assert v.verdict == "misattributed", (name, v)
    assert [e.ref for e in v.evidence][0] == "work_experience[0].responsibilities[8]"


@pytest.mark.asyncio
async def test_no_provider_keeps_the_flag():
    """MD-Q11 F2: no ``unverifiable`` exit from a misattribution flag."""
    v = await _verdict(FIX["bullet_run1"], None)
    assert v.verdict == "misattributed" and v.detail_key != "crossLanguageUnconfirmed"


@pytest.mark.asyncio
async def test_a_fallen_back_flag_is_not_counted_as_an_unavailable_judgement():
    """Nothing degraded: the flag a judgement could not lift is the pre-existing
    verdict, like the skill-equivalence seam's fallback (790:90)."""
    rep = await audit_document("cv", PROF, tailored_data=_cv(FOREIGN), provider=_Raw({}),
                               document_language="en")
    assert rep.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_a_whole_sentence_without_its_full_stop_is_a_citation():
    v = await _verdict(FIX["bullet_run1"], _Judge(True, OWN.rstrip(".")))
    assert v.verdict == "grounded" and [e.ref for e in v.evidence] == ["work_experience[2].responsibilities[0]"]


def test_whole_unit_or_sentence_is_whole():
    from applire.services.oracle.audit import _whole_unit_or_sentence
    from applire.services.oracle.matchers.vault import EvidenceUnit

    text = "Planung der Instandhaltung. Führung von vier Mitarbeitenden in der Frühschicht."
    unit = EvidenceUnit(path="work_experience[0].responsibilities[0]", text=text, text_norm=text.lower())
    assert _whole_unit_or_sentence("Führung von vier Mitarbeitenden in der Frühschicht", [unit]) is unit
    assert _whole_unit_or_sentence(unit.text, [unit]) is unit
    assert _whole_unit_or_sentence("Führung von vier Mitarbeitenden", [unit]) is None
    assert _whole_unit_or_sentence("Instandhaltung. Führung", [unit]) is None


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


# ── MD-Q11 F3 / RULING Q5-3: the path opens on a per-pair language fact ───────
#
# Two mixed vaults, 60/40 by long units (``holds_other_language`` is True for BOTH
# document languages on each: the vault-wide share the reviewer's probe exploited).

MIXED_DE = {
    "personal_info": {"name": "Erika Beispiel", "location": "Musterstadt"},
    "skills": [{"name": "Shopfloor-Management", "category": "technical"}],
    "work_experience": [
        {"id": "g1", "company": "Alpha Kunststoff GmbH", "role": "Produktionsleiterin",
         "responsibilities": ["Einführung von Shopfloor-Management und KVP-Routinen in beiden Hallen.",
                              "Verantwortung für die Fertigungsplanung in der Spritzgießerei mit drei Schichten."]},
        {"id": "g2", "company": "Beta Metall AG", "role": "Schichtleiterin",
         "responsibilities": [
             "Coordinated weekly maintenance planning together with the supplier network and the plant management team.",
             "Introduced a structured onboarding programme for new shift staff across all three production halls."]},
        {"id": "g3", "company": "Gamma Pressen KG", "role": "Meisterin",
         "responsibilities": ["Planung der Instandhaltung für die Pressenlinie im Zweischichtbetrieb mit Wochenenddiensten."]},
    ],
}
DE_BULLET = "Einführung von Shopfloor-Management und KVP-Routinen in beiden Hallen."
G2_UNIT = "Coordinated weekly maintenance planning together with the supplier network and the plant management team."

MIXED_EN = {
    "personal_info": {"name": "Erika Beispiel", "location": "Musterstadt"},
    "skills": [{"name": "Excel", "category": "technical"}],
    "work_experience": [
        {"id": "e1", "company": "North Sales Ltd", "role": "Sales Analyst",
         "responsibilities": ["Built the monthly sales reporting in Excel for the regional managers.",
                              "Led the migration of the customer database to the new CRM system."]},
        {"id": "e2", "company": "South Trading plc", "role": "Team Lead",
         "responsibilities": ["Managed a team of four account managers in the northern region of the country."]},
        {"id": "e3", "company": "Mitte Vertrieb GmbH", "role": "Key Account Managerin",
         "responsibilities": ["Betreuung der Schlüsselkunden im Bereich Automotive mit Fokus auf das Neugeschäft.",
                              "Aufbau eines Partnernetzwerks für den Vertrieb in Österreich und der Schweiz."]},
    ],
}
EN_BULLET = "Built the monthly sales reporting in Excel for the regional managers."
E3_UNIT = "Betreuung der Schlüsselkunden im Bereich Automotive mit Fokus auf das Neugeschäft."


async def _moved(profile, bullet, wid, provider, language):
    rep = await audit_document("cv", profile, provider=provider, document_language=language, tailored_data={
        "summary": "", "skills": [], "work_history": [{"id": wid, "company": "x", "role": "x", "bullets": [bullet]}]})
    (c,) = [c for c in rep.claims if c.claim.text == bullet]
    return c.verdict


def test_the_mixed_vaults_hold_the_other_language_vault_wide():
    from applire.services.oracle.matchers.vault import build_vault_index, holds_other_language

    for profile in (MIXED_DE, MIXED_EN):
        idx = build_vault_index(profile)
        assert holds_other_language(idx, "de") and holds_other_language(idx, "en")
        assert sorted(round(v, 1) for v in idx.unit_language_shares.values()) == [0.4, 0.6]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,bullet,target,language", [
    (MIXED_DE, DE_BULLET, "g3", "de"),
    (MIXED_EN, EN_BULLET, "e2", "en"),
])
async def test_a_same_language_move_in_a_mixed_vault_never_reaches_the_judge(profile, bullet, target, language):
    """F3: the target role's content is in the bullet's language, so the lexical
    flag is decidable and stands — whatever the vault-wide share says. (The
    reviewer's grant "in" would have grounded it before.)"""
    judge = _Raw(_item(True, "in"))
    v = await _moved(profile, bullet, target, judge, language)
    assert v.verdict == "misattributed"
    assert judge.prompts == []


#: RULING Q5-3 case 1 — the stated residual. A bullet that belongs to another role,
#: rendered under a role whose content is in the OTHER language, does reach the
#: judge; a faithful double that grants with a WHOLE unit of that role grounds it.
#: The safety here is the judge's "no" alone (Luna replay, report Addendum 1).
@pytest.mark.asyncio
@pytest.mark.parametrize("profile,bullet,target,unit,language", [
    (MIXED_DE, DE_BULLET, "g2", G2_UNIT, "de"),
    (MIXED_EN, EN_BULLET, "e3", E3_UNIT, "en"),
])
async def test_a_cross_language_move_reaches_the_judge_and_its_no_keeps_the_flag(profile, bullet, target, unit, language):
    asked = _Raw(_item(False, unit))
    assert (await _moved(profile, bullet, target, asked, language)).verdict == "misattributed"
    assert unit in asked.prompts[0]
    granted = await _moved(profile, bullet, target, _Raw(_item(True, unit)), language)
    assert granted.verdict == "grounded" and granted.detail_key == "crossLanguageEquivalent"


#: RULING Q5-3 case 2: the same move with no positive evidence keeps the flag.
@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [None, {}, {"items": []}, RuntimeError("boom"), _item("yes", G2_UNIT),
                                   _item(True, "in"), _item(True, "weekly maintenance planning"),
                                   _item(True, "Schichtleiterin"), _item(True, "")])
async def test_a_cross_language_move_without_positive_evidence_stays_misattributed(reply):
    provider = None if reply is None else _Raw(reply)
    v = await _moved(MIXED_DE, DE_BULLET, "g2", provider, "de")
    assert v.verdict == "misattributed", (reply, v)


def test_an_undecided_text_opens_nothing():
    from applire.services.oracle.audit import _decided_language

    assert _decided_language("Industriemechaniker") is None          # too short
    assert _decided_language("Led 38 employees directly.") is None   # tie / no function word
    assert _decided_language(DE_BULLET) == "de" and _decided_language(EN_BULLET) == "en"


@pytest.mark.asyncio
async def test_a_role_title_or_technology_in_the_other_language_is_never_offered_or_cited():
    """MD-Q11 F1: only CONTENT units count. A long English role title and a
    technology label say nothing a bullet could restate, so citing either whole
    keeps the flag — and with no content unit in the other language, the judge is
    not even asked."""
    title = "Head of the maintenance team for the press line"
    tech = "Predictive maintenance with the vibration monitoring system"
    profile = {**MIXED_DE, "work_experience": [
        MIXED_DE["work_experience"][0],
        {"id": "g4", "company": "Delta Werke AG", "role": title, "technologies": [tech],
         "responsibilities": ["Planung der Wartung für alle Pressen im Werk mit dem eigenen Team."]},
    ]}
    for quote in (title, tech):
        judge = _Raw(_item(True, quote))
        v = await _moved(profile, DE_BULLET, "g4", judge, "de")
        assert v.verdict == "misattributed", (quote, v)
        assert judge.prompts == []
