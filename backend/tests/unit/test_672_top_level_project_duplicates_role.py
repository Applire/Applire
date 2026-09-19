# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Writer collector #672 line 100 — a top-level PROJEKTE section repeating a
project already rendered inside its role.

Founder's edge UAT 2026-09-18: two projects appeared BOTH under their employer
role AND under PROJEKTE, with the same facts restated up to three times; the ATS
`duplicate-project` and `duplicate-bullets` checks both FAILED and the document
shipped anyway. Both blind reviewers cited the repetition as the reason the CV
reads as inflated.

`_nest_projects` deduplicates project names WITHIN each destination bin and never
ACROSS them — the gap `ats_audit.duplicate_project_pairs`' own docstring names.
There are exactly two ways in, and this file pins both plus the negative control:

* **A — the vault TIES the project to a role and the writer also emitted it
  top-level.** The vault copy is nested; the writer's copy stays in
  `data["projects"]`. Fixed by MOVING the writer's copy into the role: ADR-067
  gives the container decision to code, and this module already ruled that the
  reviewed, tailored copy beats the vault's verbatim one — so nothing is deleted
  and no bullet is lost.
* **B — the vault leaves the project UNTIED and the writer nested its own copy.**
  The vault copy falls through to the standalone list. Fixed by the same rule one
  container over: a project already rendered inside a role is not rendered again
  at the top level.
* **C (negative control) — a project tied to NO role that no role renders** still
  renders top-level, exactly once. The fix must not empty the PROJEKTE section.

Synthetic twin throughout: two tenures at ONE employer (the shape ADR-072 clause
5 cares about) and one project whose facts are stated once. Nothing here is taken
from the founder's profile.
"""
from __future__ import annotations

import logging

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import duplicate_project_pairs
from applire.services.cv import _nest_projects, assemble_tailored_cv

SENIOR_ID = "11111111-1111-1111-1111-111111111111"
JUNIOR_ID = "22222222-2222-2222-2222-222222222222"

PROJECT_NAME = "Laborplattform Blueprint"
VAULT_BULLET = "Konsolidierte 25 Analysesysteme auf 4."
WRITER_BULLET = "Konsolidierung von 25 Analysesystemen auf 4 Plattformen."


def _profile(associated_experience):
    return {
        "contact": {"full_name": "Testperson", "email": "kontakt@applire.de"},
        "work_experience": [
            {
                "id": SENIOR_ID,
                "company": "Nordlicht Labs GmbH",
                "role": "Bereichsleiter Qualitaet",
                "start_date": "2021-01",
                "end_date": None,
            },
            {
                "id": JUNIOR_ID,
                "company": "Nordlicht Labs GmbH",
                "role": "Teamleiter Labor",
                "start_date": "2018-01",
                "end_date": "2020-12",
            },
        ],
        "projects": [
            {
                "name": PROJECT_NAME,
                "associated_experience": associated_experience,
                "responsibilities": [VAULT_BULLET],
                "achievements": [],
            }
        ],
        "education": [],
        "languages": [],
        "skills": [],
        "certifications": [],
    }


def _prose(*, nested_projects, top_level_projects):
    return {
        "summary": "Qualitaetsfuehrung in der Laboranalytik.",
        "skills": [],
        "work": [
            {
                "id": SENIOR_ID,
                "bullets": ["Leitete die Qualitaetsorganisation."],
                "projects": nested_projects,
            },
            {
                "id": JUNIOR_ID,
                "bullets": ["Verantwortete den Betrieb des Analytiklabors."],
                "projects": [],
            },
        ],
        "projects": top_level_projects,
    }


def _compose(profile, prose):
    tailored = TailoredCVData.model_validate(assemble_tailored_cv(prose, profile))
    return _nest_projects(tailored, profile)


def _rendered(tailored):
    data = tailored.model_dump()
    nested = {
        w["id"]: [p["name"] for p in (w.get("projects") or [])]
        for w in data["work_history"]
    }
    top = [p["name"] for p in (data.get("projects") or [])]
    return nested, top


def test_shape_a_role_tied_project_the_writer_also_put_top_level_renders_once():
    """The vault ties the project to the senior tenure; the writer emitted it as a
    standalone project as well. It must render ONCE, inside the role."""
    profile = _profile(SENIOR_ID)
    prose = _prose(
        nested_projects=[],
        top_level_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
    )

    nested, top = _rendered(_compose(profile, prose))

    assert nested[SENIOR_ID] == [PROJECT_NAME]
    assert nested[JUNIOR_ID] == []
    assert top == []


def test_shape_a_keeps_the_writers_tailored_bullets_not_the_vault_verbatim_copy():
    """The copy that survives is the one the review loop actually saw. Deleting the
    writer's copy and keeping the vault's would silently swap reviewed prose for
    untouched transcription."""
    profile = _profile(SENIOR_ID)
    prose = _prose(
        nested_projects=[],
        top_level_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
    )

    data = _compose(profile, prose).model_dump()
    surviving = data["work_history"][0]["projects"][0]

    assert surviving["bullets"] == [WRITER_BULLET]
    assert VAULT_BULLET not in surviving["bullets"]


def test_shape_a_relocation_is_logged(caplog):
    """ADR-072 clause 4's standard: no move in the tail is silent."""
    profile = _profile(SENIOR_ID)
    prose = _prose(
        nested_projects=[],
        top_level_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
    )

    with caplog.at_level(logging.INFO, logger="applire.services.cv"):
        _compose(profile, prose)

    assert any(
        "TAIL_RELOCATE (#672 L100)" in r.getMessage() for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_shape_b_untied_vault_project_the_writer_nested_renders_once():
    """The vault leaves the project untied, so the join sends it to the standalone
    list — while the writer already nested its own copy under the role."""
    profile = _profile(None)
    prose = _prose(
        nested_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
        top_level_projects=[],
    )

    nested, top = _rendered(_compose(profile, prose))

    assert nested[SENIOR_ID] == [PROJECT_NAME]
    assert top == []


def test_negative_control_a_project_no_role_renders_still_appears_top_level():
    """The fix disposes a DUPLICATE, never the PROJEKTE section. A project tied to
    no role, and rendered inside no role, keeps its top-level entry."""
    profile = _profile(None)
    prose = _prose(nested_projects=[], top_level_projects=[])

    nested, top = _rendered(_compose(profile, prose))

    assert nested[SENIOR_ID] == []
    assert nested[JUNIOR_ID] == []
    assert top == [PROJECT_NAME]


def test_negative_control_a_second_distinct_project_is_untouched():
    """Only the name that collides is disposed; an unrelated standalone project
    still renders."""
    profile = _profile(SENIOR_ID)
    profile["projects"].append(
        {
            "name": "Ringversuch Spurenanalytik",
            "associated_experience": None,
            "responsibilities": ["Organisierte den jaehrlichen Ringversuch."],
            "achievements": [],
        }
    )
    prose = _prose(
        nested_projects=[],
        top_level_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
    )

    nested, top = _rendered(_compose(profile, prose))

    assert nested[SENIOR_ID] == [PROJECT_NAME]
    assert top == ["Ringversuch Spurenanalytik"]


def test_the_delivered_duplicate_project_check_passes_on_both_shapes():
    """The delivered-document instrument, not the intermediate shape: #424's
    `duplicate_project_pairs` is what FAILED on the founder's CV."""
    tied = _compose(
        _profile(SENIOR_ID),
        _prose(
            nested_projects=[],
            top_level_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
        ),
    )
    untied = _compose(
        _profile(None),
        _prose(
            nested_projects=[{"name": PROJECT_NAME, "bullets": [WRITER_BULLET]}],
            top_level_projects=[],
        ),
    )

    assert duplicate_project_pairs(tied) == []
    assert duplicate_project_pairs(untied) == []
