# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#701 — a matched figure grounds only what its own vault evidence carries.

Adversarial probe A2 (Nougat build 3, writer-controls): the sentence below was
graded ``grounded``/``numbers`` ("All figures trace to vault evidence") because
"38" matched the vault, while the ISO-45001-Lead-Auditor certificate it also
claims exists nowhere in the vault. ADR-052 amended 2026-10-07: when the units
holding the matched figures carry less than ``GROUNDED_MIN_COVERAGE`` of the
claim's content, the claim takes the figure-free chain instead.

One seam test per call path (CV ``extract_claims_from_tailored``, letter
``extract_claims_from_letter``) plus the direct ``verify_claim`` shape, and the
negative cases that pin "never an accusation" and "a figure that carries the
claim still grounds it".
"""
from __future__ import annotations

import asyncio

import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle import audit_document, verify_claim
from applire.services.oracle.matchers import build_vault_index, coverage_by_units

A2 = (
    "Zwei Fertigungsbereiche mit 38 Mitarbeitenden gefuehrt und dabei den "
    "ISO-45001-Lead-Auditor-Zertifikat erworben"
)
TRUE_HALF = "Zwei Fertigungsbereiche mit 38 Mitarbeitenden geführt"

PROFILE = {
    "personal_info": {"name": "Max Muster"},
    "work_experience": [
        {
            "id": "w-weberit",
            "company": "Weberit GmbH",
            "role": "Produktionsleiter",
            "start_date": "2015-01",
            "responsibilities": [
                "Zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb geführt.",
                "Einführung von KVP und SMED in beiden Bereichen.",
            ],
        }
    ],
    "skills": [{"name": "SMED", "category": "domain"}],
}


def _run(coro):
    return asyncio.run(coro)


def test_a2_sentence_is_not_grounded_by_its_figure():
    v = _run(verify_claim(A2, PROFILE))
    assert v.verdict != "grounded", v
    # Never an accusation: the gate only withdraws a grounded the figure
    # could not earn.
    assert v.verdict == "unverifiable"


def test_the_true_half_alone_still_grounds_on_its_figure():
    v = _run(verify_claim(TRUE_HALF, PROFILE))
    assert (v.verdict, v.checker) == ("grounded", "numbers")


def test_fixture_property_a2_is_under_the_floor_and_true_half_over_it():
    # The fixtures must have the property the tests claim, measured by the
    # production predicate itself.
    idx = build_vault_index(PROFILE)
    unit = [u for u in idx.units if "38 Mitarbeitenden" in u.text]
    assert coverage_by_units(A2, unit) < 0.6
    assert coverage_by_units(TRUE_HALF, unit) >= 0.6


def test_anchored_honest_multi_fact_bullet_grounds_via_role_union():
    # Figure unit carries "Zwei Fertigungsbereiche … 38 … geführt"; the role's
    # OTHER bullet carries the rest. The routed claim reaches the role union
    # and stays grounded, with BOTH evidence sets.
    claim = Claim(
        text="Zwei Fertigungsbereiche mit 38 Mitarbeitenden geführt und KVP sowie "
        "SMED in beiden Bereichen eingeführt",
        location="work_history[0].bullets[0]",
        kind="bullet",
        source_experience_id="w-weberit",
    )
    v = _run(verify_claim(claim, PROFILE))
    assert (v.verdict, v.checker) == ("grounded", "grounding"), v
    refs = {e.ref for e in v.evidence}
    assert "work_experience[0].responsibilities[0]" in refs
    assert "work_experience[0].responsibilities[1]" in refs


def test_anchored_a2_bullet_is_not_rescued_by_the_role_union():
    claim = Claim(text=A2, location="work_history[0].bullets[0]", kind="bullet",
                  source_experience_id="w-weberit")
    v = _run(verify_claim(claim, PROFILE))
    assert v.verdict == "unverifiable", v


def test_seam_cv_path_extract_claims_from_tailored():
    tailored = {
        "summary": "",
        "work_history": [
            {"id": "w-weberit", "company": "Weberit GmbH", "role": "Produktionsleiter",
             "bullets": [A2, TRUE_HALF]},
        ],
        "skills": [],
    }
    report = _run(audit_document("cv", PROFILE, tailored_data=tailored))
    by_text = {cr.claim.text: cr.verdict for cr in report.claims}
    assert by_text[A2].verdict != "grounded", by_text[A2]
    assert (by_text[TRUE_HALF].verdict, by_text[TRUE_HALF].checker) == ("grounded", "numbers")


def test_seam_letter_path_extract_claims_from_letter():
    letter = {
        "recipient": {"company": "Zielfirma AG"},
        "body": {"paragraphs": [
            "Sehr geehrte Damen und Herren,",
            f"Bei der Weberit GmbH habe ich {A2[0].lower()}{A2[1:]}.",
            f"Bei der Weberit GmbH habe ich {TRUE_HALF[0].lower()}{TRUE_HALF[1:]}.",
        ]},
    }
    report = _run(audit_document("cover_letter", PROFILE, letter_data=letter))
    a2 = [cr for cr in report.claims if "Lead-Auditor" in cr.claim.text]
    true_half = [cr for cr in report.claims
                 if "38" in cr.claim.text and "Lead-Auditor" not in cr.claim.text]
    assert a2 and all(cr.verdict.verdict != "grounded" for cr in a2), a2
    assert true_half and all(cr.verdict.verdict == "grounded" for cr in true_half), true_half
