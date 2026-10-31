# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-11 (Hazelnut build 5, delivery re-run): an English CV shipped the German
vault chip "Führung und Mentoring cross-funktionaler Teams" next to "Team
Leadership and Development". The writer had translated the chip; the terminal
corrector copied the vault names back because a code check told it "the profile's
name is X" — a positional zip in ``cv.skill_spelling`` paired the wrong entries
after one collapse, and the acronym-expansion matcher read an ordinary word
("Development") as an acronym. Nothing after that read the chips' language.
Synthetic data only."""

from __future__ import annotations

from types import SimpleNamespace

from applire.schemas.cv import TailoredCVData
from applire.services import cv as cv_mod
from applire.services.ats_audit import foreign_language_items
from applire.services.cv import _acronym_expansion_vault_match, _terminal_language_trigger
from applire.services.cv_v2 import V2CheckContext, _skill_findings

DE_CHIP = "Führung und Mentoring cross-funktionaler Teams"


def _tailored(skills: list[str]) -> TailoredCVData:
    return TailoredCVData.model_validate({"contact": {"name": "Jana Lehmann"}, "summary": "Lead.",
                                          "work_history": [], "skills": skills})


def test_the_spelling_finding_pairs_each_entry_with_its_own_vault_name():
    """The spelling pass collapses the first entry into a later one; a positional
    zip then paired every later entry with its neighbour's name."""
    def spelling(t, _profile):
        out = []
        for s in t.skills:
            v = {"ai governance and risk management": "AI governance & risk management"}.get(s.lower(), s)
            if v not in out:
                out.append(v)
        return t.model_copy(update={"skills": out})

    ident = lambda t, *a, **k: t  # noqa: E731
    fake = SimpleNamespace(_dedup_skills=ident, _drop_ungrounded_jd_echo_skills=ident,
                           _tailor_skills_to_jd=ident, _restore_skill_spelling=spelling,
                           _restore_narrative_named_skills=ident, CV_MAX_SKILLS=24)
    ctx = V2CheckContext(profile_json={}, keyword_ledger=[], job_dict={}, language="en")
    composed = _tailored(["AI governance & risk management", "AI governance and risk management",
                          "Cross-functional team leadership and mentoring", "System architecture"])
    found = {f.check: f for f in _skill_findings(composed, ctx, fake)}
    spell = found["cv.skill_spelling"]
    assert spell.subjects == ("AI governance and risk management",)
    assert "Cross-functional" not in spell.text and "System architecture" not in spell.text


def test_an_ordinary_leftover_word_is_no_acronym_expansion():
    vault = ["Team Leadership and Development", "GxP Compliance & Computer System Validation"]
    assert _acronym_expansion_vault_match("Cross-functional team leadership and mentoring", vault) is None
    assert _acronym_expansion_vault_match(
        "Good Practice Compliance & Computer System Validation", vault
    ) == "GxP Compliance & Computer System Validation"


def test_the_document_language_check_reads_skill_chips():
    items = foreign_language_items(_tailored(["Large Language Models", DE_CHIP]), "en")
    assert items == [("Kenntnisse", DE_CHIP)]
    # a 2–3-word chip stays below the evidence floor
    assert foreign_language_items(_tailored(["Large Language Models"]), "de") == []


def test_a_new_foreign_chip_fires_the_terminal_recheck_on_a_same_language_vault():
    profile = {"work_experience": [{"responsibilities": [
        "Led five technical leads and the system owners of the supply-chain platform.",
        "Built an agentic workflow that drafts validation documents for review.",
    ]}]}
    before = {"summary": "Engineering lead.", "work": [], "projects": [],
              "skills": ["Cross-functional team leadership and mentoring"]}
    after = {**before, "skills": ["Team Leadership and Development", DE_CHIP]}
    trig = _terminal_language_trigger(before, after, profile, "en")
    assert trig.cross_language is False
    assert trig.fired
    assert ("skills", DE_CHIP) in trig.foreign_prose


def test_baseline_the_old_zip_paired_the_wrong_entries():
    """What the fix rescues, asserted rather than assumed."""
    s3 = ["AI governance & risk management", "AI governance and risk management",
          "Cross-functional team leadership and mentoring", "System architecture"]
    s4 = ["AI governance & risk management", "Cross-functional team leadership and mentoring",
          "System architecture"]
    old = [(b, a) for b, a in zip(s3, s4) if b != a]
    assert ("Cross-functional team leadership and mentoring", "System architecture") in old
    assert cv_mod is not None
