# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-19 (Hazelnut build 5, delivery run 3): `duplicate-bullets` flagged two
unrelated bullets because both named one delivered skill ("computer system
validation") — a 3-token shared run read as a copied clause. A run inside a
delivered skills entry is that skill named twice. Synthetic data only."""

from __future__ import annotations

from applire.services.ats_audit import redundant_bullet_pairs

A = ("Initiated and lead an agentic GenAI system that automates authoring and review of "
     "computer system validation documentation on a data platform.")
B = ("Served as IT quality representative for supplier audits and ensured computer "
     "system validation in line with GxP requirements.")
COPY = ("Served as IT quality representative for supplier audits and ensured release "
        "readiness for every laboratory system.")


def _cv(bullets, skills):
    return {"skills": skills, "work_history": [
        {"id": "w1", "company": "Nord AG", "role": "Lead", "bullets": [bullets[0]]},
        {"id": "w2", "company": "Süd eV", "role": "Engineer", "bullets": bullets[1:]},
    ]}


def test_a_shared_skill_name_alone_is_not_a_duplicate():
    assert redundant_bullet_pairs(_cv([A, B], ["Computer system validation (CSV)", "GxP"])) == []


def test_without_that_skill_the_run_still_fires_as_before():
    # the instrument's old behaviour is unchanged where no delivered term explains the run
    assert len(redundant_bullet_pairs(_cv([A, B], ["GxP"]))) == 1


def test_negative_control_a_copied_clause_is_still_caught():
    pairs = redundant_bullet_pairs(_cv([A, B, COPY], ["Computer system validation (CSV)"]))
    assert [(a[:20], b[:20]) for _, a, _, b in pairs] == [(B[:20], COPY[:20])]
