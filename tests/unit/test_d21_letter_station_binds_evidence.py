# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-21 (Hazelnut build 5, delivery run 4): "At Blutspendedienst …, I led
software implementation … and set project and validation standards." graded
grounded with a BioNTech project as evidence. The project's lone leading
token "Implementation" matched the word "implementation" case-blind, so the
sentence "named" two companies and the anchor failed open. And "At BioNTech
SE," anchored to nothing because one id's project name overwrote its company
name. Synthetic data only."""

from __future__ import annotations

import asyncio

from applire.services.oracle.selfaudit import build_self_audit_report

VAULT = {
    "personal_info": {"name": "Jana Lehmann"},
    "work_experience": [
        {"id": "w-now", "company": "Nordpharm SE", "role": "Associate Director", "start_date": "2024-12",
         "is_current": True,
         "responsibilities": ["Lead an agentic GenAI system for validation documents."],
         "achievements": ["Recruited a cross-team group of volunteers to build the prototype."]},
        {"id": "w-old", "company": "Nordpharm SE", "role": "System Engineer", "start_date": "2018-10",
         "end_date": "2021-05", "responsibilities": ["Operated laboratory systems."]},
        {"id": "w-brk", "company": "Blutbank Mitte gGmbH", "role": "Systementwickler",
         "start_date": "2012-08", "end_date": "2018-09",
         "responsibilities": ["Managed software implementation projects from requirements to rollout."]},
    ],
    "projects": [
        {"id": "p-gen", "name": "Generative Validation Platform", "associated_experience": "w-now",
         "responsibilities": ["Drive an enterprise generative AI platform."]},
        {"id": "p-lims", "name": "Implementation of Nordpharm Global QC LIMS", "associated_experience": "w-old",
         "responsibilities": ["Established company standards for software project deliverables and validation approaches."]},
    ],
    "skills": [],
}


def _verdicts(*paragraphs: str) -> dict[str, str]:
    letter = {"recipient": {"company": "Agilent"}, "body": {"paragraphs": list(paragraphs)}}
    report = asyncio.run(build_self_audit_report(VAULT, letter_data=letter, document_language="en"))
    return {c["claim"]["text"]: c["verdict"]["verdict"] for c in report["claims"]}


def test_a_project_fact_under_another_station_is_misattributed():
    v = _verdicts("At Blutbank Mitte gGmbH, as Systementwickler, I managed software implementation "
                  "projects from requirements to rollout, and set project and validation standards for "
                  "software deliverables.")
    standards = [t for t in v if "validation standards" in t]
    assert standards and v[standards[0]] == "misattributed", v


def test_negative_control_the_right_station_stays_grounded():
    v = _verdicts("At Nordpharm SE, I established company standards for software project deliverables "
                  "and validation approaches.")
    assert list(v.values()) == ["grounded"], v


def test_a_multi_role_employer_still_anchors_to_its_current_role():
    v = _verdicts("At Nordpharm SE, I lead an agentic GenAI system for validation documents. "
                  "I recruited a cross-team group of volunteers to build the prototype.",
                  "At Blutbank Mitte gGmbH, I managed software implementation projects from "
                  "requirements to rollout.")
    assert set(v.values()) == {"grounded"}, v
