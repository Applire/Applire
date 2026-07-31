# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""E049 / ADR-067 clauses 2–3 — the deterministic prose→facts join.

Replaces ``test_cv_work_id_backfill.py`` (premise inverted: the writer no longer
emits entries whose ids could need back-filling — ``assemble_tailored_cv``
establishes identity structurally, and an unknown id FAILS CLOSED instead of
being fuzzy-matched)."""

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.cv import UnknownWorkEntryIdError, assemble_tailored_cv


PROFILE = {
    "personal_info": {
        "name": "Anna Bauer",
        "email": "anna.bauer@example.de",
        "phone": "+49 170 1234567",
        "location": "Munich, Germany",
        "linkedin": "linkedin.com/in/annabauer",
    },
    # Reverse-chronological, as _render_cv_background sorts it before the prompt.
    "work_experience": [
        {"id": "w-new", "company": "TechVision GmbH", "role": "Senior Software Engineer",
         "start_date": "2021-03", "end_date": None},
        {"id": "w-old", "company": "StartupX AG", "role": "Software Engineer",
         "start_date": "2018-06", "end_date": "2021-02"},
    ],
    "education": [
        {"institution": "TU Berlin", "degree": "MSc", "field": "CS",
         "start_date": "2016-10", "end_date": "2018-05"},
    ],
    "languages": [
        {"language": "German", "level": "Native"},
        {"language": "English", "level": "C1"},
    ],
}


def _prose(work=None, **over):
    prose = {
        "summary": "Erfahrene Backend-Ingenieurin.",
        "work": work if work is not None else [
            {"id": "w-new", "bullets": ["Neu A", "Neu B"], "projects": []},
            {"id": "w-old", "bullets": ["Alt A"], "projects": []},
        ],
        "skills": ["Python", "FastAPI"],
    }
    prose.update(over)
    return prose


def test_facts_are_joined_verbatim_from_the_vault():
    t = TailoredCVData.model_validate(assemble_tailored_cv(_prose(), PROFILE))
    assert t.contact.name == "Anna Bauer"
    assert t.contact.email == "anna.bauer@example.de"
    assert t.contact.linkedin == "linkedin.com/in/annabauer"
    e = t.work_history[0]
    assert (e.id, e.company, e.role, e.start_date, e.end_date) == (
        "w-new", "TechVision GmbH", "Senior Software Engineer", "2021-03", None
    )
    assert e.bullets == ["Neu A", "Neu B"]
    assert t.summary == "Erfahrene Backend-Ingenieurin."
    assert t.skills == ["Python", "FastAPI"]


def test_document_order_is_the_vault_order_not_the_prose_order():
    # The writer returning entries in the wrong order must not reorder the page —
    # order is a vault join now (this is what retired _enforce_work_order).
    reversed_work = [
        {"id": "w-old", "bullets": ["Alt A"]},
        {"id": "w-new", "bullets": ["Neu A"]},
    ]
    t = TailoredCVData.model_validate(
        assemble_tailored_cv(_prose(work=reversed_work), PROFILE)
    )
    assert [w.id for w in t.work_history] == ["w-new", "w-old"]
    assert t.work_history[0].bullets == ["Neu A"]


def test_unknown_id_fails_closed():
    bad = _prose(work=[{"id": "invented", "bullets": ["x"]}])
    with pytest.raises(UnknownWorkEntryIdError):
        assemble_tailored_cv(bad, PROFILE)


def test_omitted_entry_keeps_its_factual_line_with_empty_bullets():
    t = TailoredCVData.model_validate(
        assemble_tailored_cv(_prose(work=[{"id": "w-new", "bullets": ["Neu A"]}]), PROFILE)
    )
    assert [w.id for w in t.work_history] == ["w-new", "w-old"]
    assert t.work_history[1].company == "StartupX AG"
    assert t.work_history[1].bullets == []


def test_education_and_languages_are_copied_wholesale():
    t = TailoredCVData.model_validate(assemble_tailored_cv(_prose(), PROFILE))
    assert t.education[0].institution == "TU Berlin"
    assert t.education[0].degree == "MSc"
    assert [(l.language, l.level) for l in t.languages] == [
        ("German", "Native"), ("English", "C1")
    ]


def test_segmented_standalone_projects_are_carried():
    prose = _prose(projects=[{"name": "OSS Tool", "bullets": ["Built it"]}])
    t = TailoredCVData.model_validate(assemble_tailored_cv(prose, PROFILE))
    assert t.projects[0].name == "OSS Tool"
    assert t.projects[0].bullets == ["Built it"]


def test_nested_writer_projects_survive_the_join():
    work = [{"id": "w-new", "bullets": ["Neu A"],
             "projects": [{"name": "Migration", "bullets": ["Did it"]}]}]
    t = TailoredCVData.model_validate(assemble_tailored_cv(_prose(work=work), PROFILE))
    assert t.work_history[0].projects[0].name == "Migration"


def test_junk_tolerance():
    prose = {
        "summary": None,
        "work": [{"id": "w-new", "bullets": ["ok", 42, None], "projects": ["junk", {}]},
                 "not-a-dict"],
        "skills": ["Python", 7, None],
    }
    t = TailoredCVData.model_validate(assemble_tailored_cv(prose, PROFILE))
    assert t.work_history[0].bullets == ["ok"]
    assert t.skills == ["Python"]
    assert t.summary == ""
