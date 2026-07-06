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

"""US187 — the generated CV renders ProjectEntry under its associated parent
position. Projects are dropped entirely during tailoring today (no `projects`
field on TailoredCVData); these tests pin the schema extension, the deterministic
code-side nesting step, and the template rendering of the hierarchy.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Schema: TailoredCVData must carry projects nested under work entries and a
# top-level standalone list.
# ---------------------------------------------------------------------------


def test_tailored_work_entry_carries_id_and_projects():
    from applire.schemas.cv import TailoredWorkEntry, TailoredProjectEntry

    entry = TailoredWorkEntry(
        id="w1",
        company="Acme GmbH",
        role="Engineer",
        start_date="2020",
        projects=[TailoredProjectEntry(name="Atlas", bullets=["Shipped v1"])],
    )
    assert entry.id == "w1"
    assert entry.projects[0].name == "Atlas"
    assert entry.projects[0].bullets == ["Shipped v1"]


def test_tailored_cv_data_has_standalone_projects():
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredProjectEntry

    cv = TailoredCVData(
        contact=TailoredContact(name="X"),
        projects=[TailoredProjectEntry(name="Side Project", bullets=["b"])],
    )
    assert cv.projects[0].name == "Side Project"


def test_tailored_cv_data_defaults_no_projects():
    """Back-compat: legacy tailored_data without project fields still validates,
    with empty project lists everywhere."""
    from applire.schemas.cv import TailoredCVData

    cv = TailoredCVData.model_validate(
        {
            "contact": {"name": "Legacy"},
            "work_history": [
                {"company": "Old Co", "role": "Dev", "start_date": "2015"}
            ],
        }
    )
    assert cv.projects == []
    assert cv.work_history[0].projects == []


# ---------------------------------------------------------------------------
# Deterministic nesting: _nest_projects matches each source ProjectEntry to its
# parent via associated_experience, places it under the corresponding tailored
# work entry (matched by company+role), unparented projects → top-level list.
# ---------------------------------------------------------------------------


def _tailored_with_two_jobs():
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry

    return TailoredCVData(
        contact=TailoredContact(name="Anna"),
        work_history=[
            TailoredWorkEntry(company="Acme GmbH", role="Senior Engineer", start_date="2020"),
            TailoredWorkEntry(company="StartupX AG", role="Engineer", start_date="2017"),
        ],
    )


def test_nest_projects_places_project_under_parent_work_entry():
    from applire.services.cv import _nest_projects

    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
            {"id": "work-2", "company": "StartupX AG", "role": "Engineer"},
        ],
        "projects": [
            {
                "name": "Atlas Migration",
                "responsibilities": ["Designed the cutover plan"],
                "achievements": ["Cut downtime to zero"],
                "associated_experience": "work-1",
            }
        ],
    }
    tailored = _tailored_with_two_jobs()
    nested = _nest_projects(tailored, profile_json)

    # The project lands under the first work entry (work-1 == Acme GmbH), not the second.
    acme = next(w for w in nested.work_history if w.company == "Acme GmbH")
    startupx = next(w for w in nested.work_history if w.company == "StartupX AG")
    assert [p.name for p in acme.projects] == ["Atlas Migration"]
    assert startupx.projects == []
    # A bullet from the source project survives onto the nested project.
    assert any("cutover" in b.lower() or "downtime" in b.lower() for b in acme.projects[0].bullets)
    # Nothing standalone.
    assert nested.projects == []


def test_nest_projects_matches_parent_by_company_name():
    """The CV-extraction path stores associated_experience as a company NAME, not an
    id (prompts/cv_extraction.py). Nesting must resolve that too — this is the shape
    the mock-stack profile fixture (_PROFILE_PARSE_RESPONSE) uses."""
    from applire.services.cv import _nest_projects

    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "TechVision GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "CI/CD Migration",
                "responsibilities": ["Designed pipeline architecture"],
                "associated_experience": "TechVision GmbH",
            }
        ],
    }
    tailored = _tailored_with_two_jobs()
    # Rename the tailored entry's company to match the source company exactly.
    tailored.work_history[0].company = "TechVision GmbH"
    tailored.work_history[0].role = "Senior Engineer"
    nested = _nest_projects(tailored, profile_json)

    tech = next(w for w in nested.work_history if w.company == "TechVision GmbH")
    assert [p.name for p in tech.projects] == ["CI/CD Migration"]
    assert nested.projects == []


def test_nest_projects_standalone_when_unparented():
    from applire.services.cv import _nest_projects

    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "Open Source CLI",
                "responsibilities": ["Maintained the release pipeline"],
                "associated_experience": None,
            }
        ],
    }
    tailored = _tailored_with_two_jobs()
    nested = _nest_projects(tailored, profile_json)

    assert [p.name for p in nested.projects] == ["Open Source CLI"]
    assert all(w.projects == [] for w in nested.work_history)


def test_nest_projects_volunteer_parent_falls_back_to_standalone():
    """A project parented to a volunteer activity (no matching work entry) must
    still appear — it falls back to the standalone list rather than vanishing."""
    from applire.services.cv import _nest_projects

    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "volunteer_activities": [
            {"id": "vol-1", "organization": "Code e.V.", "role": "Mentor"},
        ],
        "projects": [
            {
                "name": "Hackathon Coaching",
                "responsibilities": ["Coached three teams"],
                "associated_experience": "vol-1",
            }
        ],
    }
    tailored = _tailored_with_two_jobs()
    nested = _nest_projects(tailored, profile_json)

    assert [p.name for p in nested.projects] == ["Hackathon Coaching"]
    assert all(w.projects == [] for w in nested.work_history)


def test_nest_projects_noop_when_no_projects():
    from applire.services.cv import _nest_projects

    tailored = _tailored_with_two_jobs()
    nested = _nest_projects(tailored, {"work_experience": [], "projects": []})
    assert nested.projects == []
    assert all(w.projects == [] for w in nested.work_history)


# ---------------------------------------------------------------------------
# Templates: every CV template renders a nested project's name + bullet, and the
# project text appears after its parent company in the source order.
# ---------------------------------------------------------------------------

ALL_TEMPLATES = [
    "lebenslauf.html.j2",
    "modern_swiss.html.j2",
    "executive.html.j2",
    "tech_developer.html.j2",
    "creative_sidebar.html.j2",
    "academic.html.j2",
    "compact_pro.html.j2",
]


@pytest.fixture(scope="module")
def jinja_env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = _backend / "applire" / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )


@pytest.fixture(scope="module")
def color_ctx():
    from applire.services.color_detection import _make_color_context

    return _make_color_context("#2b5fa8")


def _cv_with_nested_and_standalone():
    from applire.schemas.cv import (
        TailoredCVData,
        TailoredContact,
        TailoredWorkEntry,
        TailoredProjectEntry,
    )

    return TailoredCVData(
        contact=TailoredContact(name="Anna Bauer", location="Berlin"),
        summary="Engineer.",
        work_history=[
            TailoredWorkEntry(
                id="w1",
                company="Acme GmbH",
                role="Senior Engineer",
                start_date="2020",
                bullets=["Owned the platform"],
                projects=[
                    TailoredProjectEntry(
                        name="Atlas Migration",
                        bullets=["Designed the cutover plan"],
                    )
                ],
            ),
        ],
        skills=["Python"],
        projects=[
            TailoredProjectEntry(name="Open Source CLI", bullets=["Maintained release pipeline"]),
        ],
        show_photo=False,
    )


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_template_renders_nested_project(template_file, jinja_env, color_ctx):
    from applire.templates.labels import cv_labels

    cv = _cv_with_nested_and_standalone()
    html = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="de", labels=cv_labels("de")
    )
    assert "Atlas Migration" in html, f"{template_file}: nested project name missing"
    assert "Designed the cutover plan" in html, f"{template_file}: nested project bullet missing"
    # nested project text appears AFTER its parent company in the document
    assert html.index("Acme GmbH") < html.index("Atlas Migration"), (
        f"{template_file}: nested project not rendered under its parent position"
    )
    # standalone project also rendered
    assert "Open Source CLI" in html, f"{template_file}: standalone project missing"


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_template_projects_label_localised(template_file, jinja_env, color_ctx):
    from applire.templates.labels import cv_labels

    cv = _cv_with_nested_and_standalone()
    html_de = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="de", labels=cv_labels("de")
    )
    html_en = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="en", labels=cv_labels("en")
    )
    # Case-insensitive: tech_developer lowercases all section labels by design.
    assert "projekte" in html_de.lower(), f"{template_file}: German projects label missing"
    assert "projects" in html_en.lower(), f"{template_file}: English projects label missing"


# ---------------------------------------------------------------------------
# Section editor: projects must survive a snapshot/override round-trip and the
# editor must not crash on a CV that carries projects.
# ---------------------------------------------------------------------------


def test_section_editor_preserves_projects_round_trip():
    from applire.services.cv_section_editor import (
        apply_overrides_to_tailored,
        build_content_snapshot,
    )

    cv = _cv_with_nested_and_standalone()
    snapshot = build_content_snapshot(cv)  # must not raise
    # An introduction override applied on top of a project-carrying CV must not
    # drop the projects.
    out = apply_overrides_to_tailored(cv, snapshot, {"introduction": "New intro"})
    assert out.summary == "New intro"
    assert out.work_history[0].projects[0].name == "Atlas Migration"
    assert out.projects[0].name == "Open Source CLI"


# ---------------------------------------------------------------------------
# Blind PQ 2026-07-04: English project bullets shipped in a German CV. The
# deterministic nesting step ran AFTER the ADR-038 language pass, so verbatim
# profile-project copies were never language-reviewed. Nesting must happen
# BEFORE _review_cv_language in the generation pipeline.
# ---------------------------------------------------------------------------


def test_projects_are_nested_before_the_language_pass():
    import inspect

    import applire.services.cv as cv

    source = inspect.getsource(cv)
    nest_call = source.index("= _nest_projects(")
    language_call = source.index("await _review_cv_language(")
    assert nest_call < language_call, (
        "deterministic project nesting must precede the ADR-038 language pass — "
        "otherwise verbatim profile-project bullets ship unreviewed (blind PQ "
        "2026-07-04)"
    )
