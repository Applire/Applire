# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-20 (Hazelnut build 5, delivery run 5 + r5 bench): the CV terminal review ran to
its round cap because two v2 code advisories asked for an edit that another
finding had just undone.

1. ``cv.project_not_mentioned`` matched only the project NAME. The reviewer said
   "the nested QC LIMS project repeats the role bullets", the corrector dropped the
   project, and the advisory asked for it back ("Vault projects neither on the CV
   nor mentioned: Implementation of … Global QC LIMS"). The role bullet still said
   "… the global QC LIMS introduction …". The fix: a project whose parent role's
   bullet carries every distinctive token of its name is mentioned.
2. ``cv.skill_named_in_bullet`` ("add these to the skills list") fired at a full
   list (25 of 35 captured reviewer prompts). The corrector added chips and the
   blocking ``cv.skills_cap`` asked to drop them. The fix: the advisory names at
   most the free slots under the cap.

Both fixes are facts (ADR-062 cl. 1: token presence, a count). Synthetic data only.
"""
from __future__ import annotations

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services import cv as cv_mod
from applire.services.cv_v2 import V2CheckContext, _project_findings, _skill_findings

LIMS_PROJECT = "Implementation of Nordpharm Global QC LIMS"


def _vault(project_name: str = LIMS_PROJECT, skills=("Python",)) -> dict:
    return {
        "personal_info": {"name": "Jana Lehmann"},
        "work_experience": [
            {"id": "w-old", "company": "Nordpharm SE", "role": "System Engineer",
             "start_date": "2018-10", "end_date": "2021-05",
             "responsibilities": ["Led the technical delivery of the global QC LIMS introduction "
                                  "across three sites and worked with the team on the release."],
             "achievements": []},
        ],
        "projects": [
            {"id": "p-lims", "name": project_name, "associated_experience": "w-old",
             "description": "The global QC LIMS deployment across three sites in seven months, "
                            "supporting the quality control of the company."},
        ],
        "skills": [{"name": s, "status": "confirmed"} for s in skills],
    }


def _cv(bullets: list[str], skills: list[str] | None = None, projects=None) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Jana Lehmann", "email": "jana@example.com"},
        "summary": "Systems engineer and architect with the release work for the team.",
        "work_history": [{"id": "w-old", "company": "Nordpharm SE", "role": "System Engineer",
                          "start_date": "2018-10", "end_date": "2021-05", "bullets": bullets,
                          "projects": projects or []}],
        "skills": skills or [],
    })


def _ctx(vault: dict) -> V2CheckContext:
    return V2CheckContext(profile_json=vault, keyword_ledger=[], job_dict={}, language="en")


def _checks(found) -> dict:
    return {f.check: f for f in found}


# --- 1. a project the parent role's bullet already names is mentioned ---------------


def test_a_project_named_by_its_parent_roles_bullet_is_not_asked_back():
    cv = _cv(["Led the technical delivery of the global QC LIMS introduction across three sites."])
    assert "cv.project_not_mentioned" not in _checks(_project_findings(cv, _ctx(_vault())))


def test_a_project_nobody_names_is_still_advised():
    cv = _cv(["Operated the laboratory systems for the quality-control team in the plant."])
    f = _checks(_project_findings(cv, _ctx(_vault())))["cv.project_not_mentioned"]
    assert LIMS_PROJECT in f.text and not f.blocking


def test_a_name_with_fewer_than_two_distinctive_tokens_is_not_matched_by_tokens():
    # "Implementation of LIMS" → {lims} only: one token is too weak to call it named
    cv = _cv(["Supported the LIMS team with the release of the plant systems."])
    found = _checks(_project_findings(cv, _ctx(_vault("Implementation of LIMS"))))
    assert "cv.project_not_mentioned" in found


def test_the_bullet_must_sit_under_the_projects_own_parent_role():
    vault = _vault()
    vault["work_experience"].append({"id": "w-new", "company": "Nordpharm SE", "role": "Director",
                                     "start_date": "2021-06", "is_current": True,
                                     "responsibilities": ["Lead the platform team."]})
    vault["projects"][0]["associated_experience"] = "w-new"
    cv = _cv(["Led the technical delivery of the global QC LIMS introduction across three sites."])
    assert "cv.project_not_mentioned" in _checks(_project_findings(cv, _ctx(vault)))


# --- 2. "add to the skills list" never past the cap ---------------------------------


def _named(n_chips: int) -> list[str]:
    vault = _vault(skills=("Python", "Docker", "FastAPI"))
    chips = [f"Chip {i}" for i in range(n_chips)]
    cv = _cv(["Built the services with Python, FastAPI and Docker for the release of the team."],
             skills=chips)
    f = _checks(_skill_findings(cv, _ctx(vault), cv_mod)).get("cv.skill_named_in_bullet")
    return list(f.subjects) if f else []


def test_named_in_bullet_is_silent_at_a_full_skills_list():
    assert _named(cv_mod.CV_MAX_SKILLS) == []


def test_named_in_bullet_names_only_the_free_slots():
    assert len(_named(cv_mod.CV_MAX_SKILLS - 2)) == 2
    assert set(_named(3)) == {"Python", "Docker", "FastAPI"}
