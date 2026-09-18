# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Writer collector #672 line 102, the LANGUAGE half — bilingual duplicate skill
chips on a real bilingual vault.

Founder's edge UAT 2026-09-18: 31 chips on one German CV, among them
'Große Sprachmodelle' next to 'Large Language Models' and 'Datenintegrität' next
to 'Data Integrity'. `_dedup_skills` saw none of them, because it compares within
one language.

The premise the collector line offered — that the ADR-038 language pass does not
cover the skills list — is REFUTED, twice over. The pass's own prompt names
`skills` explicitly and rules that discipline and competency phrases must be
translated; and measured on `openai/gpt-5.6-luna`, n=5 on a synthetic bilingual
list, the pass folded every bilingual twin into one German chip in 5 of 5 runs,
both before and after this branch's prompt narrowing. The pass was not the
producer.

The producer is deterministic and is the SKILLS mount of #724's own class: a pass
that puts vault-verbatim text on the page AFTER the last language pass.
`_tailor_skills_to_jd`'s #192 guarantee re-adds a JD-required master-profile skill
the page is missing, **in the profile's own spelling**, at the very end of the
tail — long after `_dedup_skills` ran. On a bilingual vault the English twin comes
straight back, and nothing downstream compares the pair.

Fixed with the same vehicle as #724 and with NO language judgement about the chip
itself: the guarantee set is computed once (`_guaranteed_vault_skills`, ADR-066)
and placed in front of the language pass, and `_tailor_skills_to_jd` does not
place a spelling the preseed already placed. The vault is never rewritten — only
the document renders the candidate's own skill name in the document's language
(founder ruling W-1b).

Why the skills half is gated on the VAULT's dominant language and not on the
per-item predicate #724 uses: measured 2026-09-18, `detect_language` classifies
"Large Language Models", "Data Integrity", "Agentic Systems", "Stakeholder
Management" and "Continuous Improvement" ALL as German, because a two- or
three-word competency chip carries no English function word. The per-item
instrument cannot see this population at all; the concatenated-vault verdict
(ADR-068 clause 2a's own rule) can.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

W1 = "11111111-1111-1111-1111-111111111111"

DE_CHIP = "Große Sprachmodelle"
EN_CHIP = "Large Language Models"
EN_BULLET_1 = "Built an assistant platform with large language models for the business units."
EN_BULLET_2 = "Led a team of twelve engineers and owned the delivery roadmap."
DE_BULLET_1 = "Baute eine Assistenzplattform mit grossen Sprachmodellen fuer die Fachbereiche."
DE_BULLET_2 = "Fuehrte ein Team von zwoelf Personen und verantwortete die Roadmap."

JOB = {"required_skills": [EN_CHIP], "nice_to_have_skills": [], "keywords": []}


def _profile(*, english: bool) -> dict:
    b1, b2 = (EN_BULLET_1, EN_BULLET_2) if english else (DE_BULLET_1, DE_BULLET_2)
    return {
        "contact": {"full_name": "Testperson", "email": "kontakt@applire.de"},
        "work_experience": [
            {
                "id": W1,
                "company": "Nordwerk Digital GmbH",
                "role": "Bereichsleiter KI",
                "start_date": "2021-01",
                "end_date": None,
                "responsibilities": [b1],
                "achievements": [b2],
            }
        ],
        "skills": [
            {"name": DE_CHIP, "status": "confirmed"},
            {"name": EN_CHIP, "status": "confirmed"},
            {"name": "Python", "status": "confirmed"},
        ],
        "projects": [],
        "education": [],
        "languages": [],
        "certifications": [],
    }


def _prose(skills: list[str]) -> dict:
    return {
        "summary": "Bereichsleiter fuer KI-Produkte.",
        "skills": list(skills),
        "work": [{"id": W1, "bullets": [DE_BULLET_1], "projects": []}],
        "projects": [],
    }


def _plan(prose, profile):
    from applire.services.cv import _plan_language_preseed

    return _plan_language_preseed(
        prose, profile, keyword_ledger=None, budget=None,
        job_dict=JOB, document_language="de",
    )


def _tailored(skills: list[str]):
    from applire.schemas.cv import TailoredCVData

    return TailoredCVData.model_validate(
        {"contact": {"name": "Testperson"}, "summary": "", "work_history": [], "skills": skills}
    )


# ─────────────────────────────────────────────────────────────────────────────
# the baseline: the producer, asserted rather than assumed
# ─────────────────────────────────────────────────────────────────────────────

def test_the_guarantee_re_adds_the_english_twin_when_nothing_tells_it_not_to():
    """The delivered shape the founder saw: the page already carries the German
    chip, and the #192 guarantee puts the vault's English spelling back beside it
    — after `_dedup_skills` has already run and can no longer see the pair."""
    from applire.services.cv import _tailor_skills_to_jd

    out = _tailor_skills_to_jd(_tailored([DE_CHIP, "Python"]), _profile(english=True), JOB, None)

    assert EN_CHIP in out.skills
    assert DE_CHIP in out.skills  # both — the bilingual pair


def test_the_preseed_stops_the_guarantee_from_placing_that_spelling_again():
    from applire.services.cv import _tailor_skills_to_jd

    profile = _profile(english=True)
    _new_prose, plan = _plan(_prose([DE_CHIP, "Python"]), profile)

    out = _tailor_skills_to_jd(
        _tailored([DE_CHIP, "Python"]), profile, JOB, None, preseed=plan
    )

    assert EN_CHIP not in out.skills
    assert DE_CHIP in out.skills


# ─────────────────────────────────────────────────────────────────────────────
# one instrument, and the injection
# ─────────────────────────────────────────────────────────────────────────────

def test_the_preseed_and_the_guarantee_compute_the_same_set():
    """ADR-066. The preseed may not place a different set from the one the
    guarantee would have placed, or the two would drift silently."""
    from applire.services.cv import _tailor_skills_to_jd

    profile = _profile(english=True)
    _new_prose, plan = _plan(_prose([DE_CHIP, "Python"]), profile)
    without = _tailor_skills_to_jd(_tailored([DE_CHIP, "Python"]), profile, JOB, None)

    added_by_guarantee = [s for s in without.skills if s not in {DE_CHIP, "Python"}]
    assert sorted(plan.skills) == sorted(added_by_guarantee) == [EN_CHIP]


def test_the_guaranteed_vault_skill_is_injected_into_the_prose_skills_list():
    profile = _profile(english=True)
    prose = _prose([DE_CHIP, "Python"])

    new_prose, plan = _plan(prose, profile)

    assert new_prose["skills"] == [DE_CHIP, "Python", EN_CHIP]
    assert plan._pre_skills_len == 2
    assert prose["skills"] == [DE_CHIP, "Python"]  # input unmutated


def test_the_translated_chip_becomes_an_exact_duplicate_that_dedup_collapses():
    """The whole point of sending it through the pass: once both chips read the
    same, the EXISTING page-scope dedup is enough — no cross-language predicate
    is invented anywhere."""
    from applire.services.cv import _dedup_skills, _settle_language_preseed, _tailor_skills_to_jd

    profile = _profile(english=True)
    new_prose, plan = _plan(_prose([DE_CHIP, "Python"]), profile)

    settled = dict(new_prose)
    settled["skills"] = [DE_CHIP, "Python", DE_CHIP]  # the pass translated the twin
    _settle_language_preseed(settled, plan)

    deduped = _dedup_skills(_tailored(settled["skills"]))
    final = _tailor_skills_to_jd(deduped, profile, JOB, None, preseed=plan)

    assert final.skills == [DE_CHIP, "Python"]
    assert plan.skills[EN_CHIP] == DE_CHIP


# ─────────────────────────────────────────────────────────────────────────────
# inert on a same-language vault
# ─────────────────────────────────────────────────────────────────────────────

def test_a_german_vault_feeding_a_german_document_never_touches_the_skills_list():
    profile = _profile(english=False)
    prose = _prose([DE_CHIP, "Python"])

    new_prose, plan = _plan(prose, profile)

    assert plan.skills == {}
    assert new_prose["skills"] == [DE_CHIP, "Python"]


def test_the_vault_dominant_language_is_read_over_the_concatenated_text():
    """The gate itself: a per-chip verdict is meaningless on this population, so
    the vault's language is read the way ADR-068 clause 2a reads it."""
    from applire.services.cv import _vault_dominant_language
    from applire.utils.language_detection import detect_language

    assert _vault_dominant_language(_profile(english=True)) == "en"
    assert _vault_dominant_language(_profile(english=False)) == "de"
    # …and why: every one of these chips reads as German on its own.
    for chip in (EN_CHIP, "Data Integrity", "Agentic Systems", "Stakeholder Management"):
        assert detect_language(chip) == "de"


# ─────────────────────────────────────────────────────────────────────────────
# no skill is ever lost
# ─────────────────────────────────────────────────────────────────────────────

def test_a_required_vault_skill_missing_from_the_page_still_reaches_it():
    """The #192 guarantee's own job survives the change: the skill arrives — it
    just arrives through the language pass instead of behind it."""
    profile = _profile(english=True)

    new_prose, plan = _plan(_prose(["Python"]), profile)

    assert EN_CHIP in new_prose["skills"]
    assert set(plan.skills) == {EN_CHIP}


def test_the_settle_guard_re_appends_a_skill_the_language_pass_dropped(caplog):
    import logging

    from applire.services.cv import _settle_language_preseed

    profile = _profile(english=True)
    new_prose, plan = _plan(_prose([DE_CHIP, "Python"]), profile)
    settled = dict(new_prose)
    settled["skills"] = [DE_CHIP, "Python"]  # the pass dropped the injected chip

    with caplog.at_level(logging.WARNING, logger="applire.services.cv"):
        _settle_language_preseed(settled, plan)

    assert EN_CHIP in settled["skills"]
    assert any(
        "LANGUAGE_PRESEED_SETTLE_FALLBACK (#672 L102)" in r.getMessage()
        for r in caplog.records
    )
