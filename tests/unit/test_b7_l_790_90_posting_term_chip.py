# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-L — 790:90: a skill chip that IS a POSTING TERM leaves the CV
language pass as it was handed.

UX audit round 3: the language reviewer flagged the posting's own "Supply Chain" chip
and the refiner translated it, although both prompts say POSTING TERMS stay as written;
the CV lost the keyword. The replay from persisted input did not recur (reviewer 0/30, refiner 0/15),
so the restore is a structural settle guard (ADR-069 cl. 4), not a prompt sentence.
Synthetic data.
"""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import cv as cv_mod  # noqa: E402

TERMS = ("Supply Chain", "Lean-Produktion")
HANDED = {
    "summary": "Leiter Fertigung mit Lean-Produktion.",
    "work": [{"id": "w1", "bullets": ["Führte KVP ein."], "projects": []}],
    "skills": ["Lean Management", "Supply Chain", "Team Leadership"],
}


def _settled(*skills):
    out = copy.deepcopy(HANDED)
    out["skills"] = list(skills)
    return out


def test_a_translated_posting_term_chip_is_restored_and_other_chips_keep_their_translation():
    got = cv_mod._settle_posting_term_chips(
        _settled("Lean Management", "Lieferkette", "Teamführung"), HANDED, TERMS
    )
    assert got["skills"] == ["Lean Management", "Supply Chain", "Teamführung"]


def test_case_and_whitespace_of_the_posting_list_do_not_matter():
    got = cv_mod._settle_posting_term_chips(
        _settled("Lean Management", "Lieferkette", "Teamführung"), HANDED, (" supply chain ",)
    )
    assert got["skills"][1] == "Supply Chain"


def test_no_terms_a_changed_length_or_an_untouched_chip_is_a_no_op():
    lossy = _settled("Lean Management", "Lieferkette", "Teamführung")
    assert cv_mod._settle_posting_term_chips(lossy, HANDED, ()) is lossy
    shorter = _settled("Lieferkette", "Teamführung")
    assert cv_mod._settle_posting_term_chips(shorter, HANDED, TERMS) is shorter
    same = _settled("Lean Management", "Supply Chain", "Teamführung")
    assert cv_mod._settle_posting_term_chips(same, HANDED, TERMS) is same


class _ScriptedProvider:
    """Reviewer: flags the posting term (the round-3 verdict shape). Refiner:
    translates it (the round-3 obedience). Then the reviewer approves."""

    def __init__(self):
        self.reviews = 0

    async def aparse_json(self, prompt, system=None, **_kw):
        from applire.prompts.review_cv_language import CV_LANGUAGE_REVIEW_SYSTEM_PROMPT

        if CV_LANGUAGE_REVIEW_SYSTEM_PROMPT.strip()[:200] in (system or ""):
            self.reviews += 1
            if self.reviews == 1:
                return {"approved": False,
                        "issues": [{"severity": "blocking", "issue": "Skill: Supply Chain"}],
                        "feedback": "Translate the skill Supply Chain into German."}
            return {"approved": True, "issues": [], "feedback": ""}
        return _settled("Lean Management", "Lieferkette", "Teamführung")


def test_seam_the_language_pass_returns_the_posting_term_as_handed(monkeypatch):
    monkeypatch.setattr(cv_mod, "CV_LANGUAGE_REVIEW_MAX_RETRIES", 2)
    provider = _ScriptedProvider()
    out = asyncio.run(cv_mod._review_cv_language(
        copy.deepcopy(HANDED), "de", provider, posting_terms=TERMS
    ))
    assert provider.reviews >= 1
    assert out["skills"] == ["Lean Management", "Supply Chain", "Teamführung"]


def test_seam_without_posting_terms_the_translation_stands(monkeypatch):
    monkeypatch.setattr(cv_mod, "CV_LANGUAGE_REVIEW_MAX_RETRIES", 2)
    out = asyncio.run(cv_mod._review_cv_language(
        copy.deepcopy(HANDED), "de", _ScriptedProvider()
    ))
    assert out["skills"][1] == "Lieferkette"
