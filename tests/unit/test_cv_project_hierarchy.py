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
# #169 — a bullet the LLM emits BOTH as a role bullet AND inside the project
# nested under that role must not render twice. Suppression is deterministic
# (normalized-form equality via ats_audit._norm) and only touches nested
# projects; standalone projects are never deduped against a role.
# ---------------------------------------------------------------------------


def _tailored_role_with_bullets(bullets: list[str]):
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry

    return TailoredCVData(
        contact=TailoredContact(name="Anna"),
        work_history=[
            TailoredWorkEntry(
                company="Acme GmbH", role="Senior Engineer", start_date="2020",
                bullets=bullets,
            ),
        ],
    )


def test_nest_projects_drops_exact_duplicate_role_bullet():
    from applire.services.cv import _nest_projects

    tailored = _tailored_role_with_bullets(
        ["Led the platform migration", "Mentored juniors"]
    )
    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "Atlas",
                "responsibilities": ["Led the platform migration", "Built a new API"],
                "associated_experience": "work-1",
            }
        ],
    }
    nested = _nest_projects(tailored, profile_json)
    acme = next(w for w in nested.work_history if w.company == "Acme GmbH")
    proj_bullets = acme.projects[0].bullets
    assert "Built a new API" in proj_bullets
    assert "Led the platform migration" not in proj_bullets, (
        "a bullet already on the parent role must not repeat inside its nested project"
    )


def test_nest_projects_drops_dash_and_case_variant_bullet():
    from applire.services.cv import _nest_projects

    tailored = _tailored_role_with_bullets(["Enforced Code-Review standards"])
    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "Atlas",
                "responsibilities": ["enforced code review standards", "Shipped v2"],
                "associated_experience": "work-1",
            }
        ],
    }
    nested = _nest_projects(tailored, profile_json)
    acme = next(w for w in nested.work_history if w.company == "Acme GmbH")
    assert acme.projects[0].bullets == ["Shipped v2"], (
        "dash/case-variant duplicate must be suppressed via the ats_audit normalizer"
    )


def test_nest_projects_keeps_project_when_all_bullets_suppressed():
    from applire.services.cv import _nest_projects

    tailored = _tailored_role_with_bullets(["Owned the rollout"])
    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "Rollout Project",
                "responsibilities": ["Owned the rollout"],
                "associated_experience": "work-1",
            }
        ],
    }
    nested = _nest_projects(tailored, profile_json)
    acme = next(w for w in nested.work_history if w.company == "Acme GmbH")
    assert [p.name for p in acme.projects] == ["Rollout Project"], (
        "US187: the project heading survives even when all its bullets are suppressed"
    )
    assert acme.projects[0].bullets == []


def test_nest_projects_does_not_dedupe_standalone_against_role():
    """A standalone project (no parent) must keep a bullet that coincidentally
    matches some role's bullet — suppression is scoped to the nesting parent."""
    from applire.services.cv import _nest_projects

    tailored = _tailored_role_with_bullets(["Shared infra work"])
    profile_json = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "Open Source CLI",
                "responsibilities": ["Shared infra work"],
                "associated_experience": None,
            }
        ],
    }
    nested = _nest_projects(tailored, profile_json)
    assert nested.projects[0].bullets == ["Shared infra work"]


# ---------------------------------------------------------------------------
# #172 — render-side skill dedup. The CV must be clean even when the master
# profile still carries near-duplicate skills. Uses the shared predicate; keeps
# the more-specific occurrence in the first-seen position (stable order).
# ---------------------------------------------------------------------------


def _cv_with_skills(skills: list[str]):
    from applire.schemas.cv import TailoredCVData, TailoredContact

    return TailoredCVData(contact=TailoredContact(name="Anna"), skills=skills)


def test_dedup_skills_collapses_uat_near_dupes_stable_order():
    from applire.services.cv import _dedup_skills

    cv = _cv_with_skills([
        "Team Leadership",
        "Python",
        "Team Leadership and Mentorship",
        "Project Management",
        "Cross Functional Project Management",
    ])
    out = _dedup_skills(cv)
    assert out.skills == [
        "Team Leadership and Mentorship",
        "Python",
        "Cross Functional Project Management",
    ]


def test_dedup_skills_keeps_first_on_equal_specificity():
    from applire.services.cv import _dedup_skills

    out = _dedup_skills(_cv_with_skills(["Python", "python", "FastAPI"]))
    assert out.skills == ["Python", "FastAPI"]


def test_dedup_skills_noop_when_all_distinct():
    from applire.services.cv import _dedup_skills

    cv = _cv_with_skills(["Python", "Kubernetes", "FastAPI"])
    out = _dedup_skills(cv)
    assert out.skills == ["Python", "Kubernetes", "FastAPI"]


def test_dedup_skills_collapses_single_token_containment_on_the_page():
    """#386 (E049/ADR-067 clause 6) — INVERTS the old
    test_dedup_skills_keeps_single_token_containment_distinct, whose vault-merge
    premise no longer governs this pass. ``_dedup_skills`` now runs the PAGE-scope
    predicate ``ats_audit.skills_page_dupe``, a strict superset of the vault-merge
    predicate ``skills_near_dupe`` — it additionally covers bare single-token
    containment ('React' ⊂ 'React Native') and the German-compound suffix shape.
    On a RENDERED page, 'React' next to 'React Native' reads as one skill named
    twice, even though the two are correctly kept SEPARATE by the vault-merge
    predicate (``skills_near_dupe`` is UNCHANGED — the reconciler's containment
    confirmation flow still governs there, never here). The more-specific name
    wins, same as any other collapse in this pass."""
    from applire.services.cv import _dedup_skills

    cv = _cv_with_skills(["React", "React Native", "AWS", "AWS Lambda"])
    out = _dedup_skills(cv)
    assert out.skills == ["React Native", "AWS Lambda"]


def test_dedup_skills_runs_after_language_pass_in_pipeline():
    """The dedup must sit after the ADR-038 language pass — skill tags are reworded
    there, so deduping earlier would miss twins the reviewer introduces."""
    import inspect
    import applire.services.cv as cv

    source = inspect.getsource(cv)
    lang_call = source.index("await _review_cv_language(")
    dedup_call = source.index("= _dedup_skills(")
    assert lang_call < dedup_call, "_dedup_skills must run after the language pass"


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
    from applire.templates.filters import build_template_env

    templates_dir = _backend / "applire" / "templates"
    # #307: the ONE factory — a hand-rolled Environment misses shared filters.
    return build_template_env(templates_dir)


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
# Blind PQ 2026-07-04 found English project bullets shipped in a German CV, back
# when the deterministic nesting step ran AFTER the ADR-038 language pass on a
# document the LLM had already emitted whole. E049/ADR-067 DELIBERATELY INVERTS
# that order (this test used to be test_projects_are_nested_before_the_language_pass):
# ---------------------------------------------------------------------------


def test_projects_are_nested_after_assembly_and_the_language_pass():
    """E049/ADR-067: ``_nest_projects`` now runs AFTER ``assemble_tailored_cv``
    (it matches a source project's parent onto the joined company/role identity,
    which does not exist before assembly) and therefore also after
    ``_review_cv_language``, which runs on the writer's PROSE shape BEFORE
    assembly. The nested project copies are verbatim VAULT facts — like
    education, carried in the vault's own language — so it is correct that no
    LLM pass re-words them. The blind-PQ leak this test used to guard against
    (2026-07-04) is now prevented structurally: a nested project bullet is never
    routed through an LLM pass at all, not by running nesting before one."""
    import inspect

    import applire.services.cv as cv

    source = inspect.getsource(cv)
    language_call = source.index("await _review_cv_language(")
    nest_call = source.index("= _nest_projects(")
    assert language_call < nest_call, (
        "deterministic project nesting must run AFTER the ADR-038 language pass "
        "(and after assembly) — E049/ADR-067 clause 3: nested vault project "
        "copies are verbatim facts, never routed through any LLM pass"
    )


def test_nest_projects_skips_vault_copy_when_writer_already_tailored_it():
    """E049 charter run 11: the writer's schema now carries nested projects, so
    _nest_projects appending the vault's verbatim copy next to the writer's
    tailored version rendered the same project heading twice. Same normalised
    name (a fact) ⇒ the reviewed tailored version wins; the copy is skipped."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _nest_projects

    tailored = TailoredCVData.model_validate({
        "contact": {"name": "x"},
        "work_history": [{
            "id": "w1", "company": "Weberit", "role": "PL", "start_date": "2017",
            "bullets": ["b"],
            "projects": [{"name": "Einführung eines MES-Systems",
                          "bullets": ["Tailored MES bullet with OEE 61 % auf 73 %"]}],
        }],
    })
    profile = {
        "work_experience": [{"id": "w1", "company": "Weberit", "role": "PL"}],
        "projects": [{
            "name": "Einführung eines MES-Systems",
            "description": "Verbatim vault description",
            "responsibilities": ["Vault resp"],
            "achievements": ["Vault achievement"],
            "associated_experience": "w1",
        }],
    }
    out = _nest_projects(tailored, profile)
    projects = out.work_history[0].projects
    assert len(projects) == 1
    assert projects[0].bullets == ["Tailored MES bullet with OEE 61 % auf 73 %"]
    assert out.projects == []


# --- ADR-072 clause 5: a company name is not an identity ---------------------
#
# Found by the adversarial pass on ADR-071/072 and reproduced 2026-08-02. When a
# candidate held TWO tenures at one employer (a promotion — an ordinary DACH CV
# shape) and a project's `associated_experience` is the company NAME rather than
# an id (the documented CV-extraction shape), the name index resolved to
# whichever tenure came first in vault order.


def _two_tenures_one_employer():
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry

    work = [
        TailoredWorkEntry(
            id="work-1", company="Acme GmbH", role="Junior Engineer",
            start_date="2015-01", end_date="2018-01", bullets=[],
        ),
        TailoredWorkEntry(
            id="work-2", company="Acme GmbH", role="Senior Engineer",
            start_date="2018-02", end_date=None, bullets=[],
        ),
    ]
    tailored = TailoredCVData(
        summary="s", contact=TailoredContact(), work_history=work, skills=[]
    )
    profile = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Junior Engineer"},
            {"id": "work-2", "company": "Acme GmbH", "role": "Senior Engineer"},
        ],
        "projects": [
            {
                "name": "Senior-tenure Migration",
                "responsibilities": ["Evidence that belongs to work-2 only"],
                "associated_experience": "Acme GmbH",  # NAME, not id
            }
        ],
    }
    return tailored, profile


def test_ambiguous_company_name_never_nests_under_a_guessed_tenure():
    """The project must not be asserted under EITHER tenure — it goes standalone,
    which claims no ownership the data cannot support. Before the fix it landed
    under 'Junior Engineer', the tenure that merely happened to be listed first."""
    from applire.services.cv import _nest_projects

    tailored, profile = _two_tenures_one_employer()
    nested = _nest_projects(tailored, profile)

    for entry in nested.work_history:
        assert not (entry.projects or []), (
            f"{entry.role} was given a project it may not own"
        )
    assert [p.name for p in (nested.projects or [])] == ["Senior-tenure Migration"]


def test_ambiguous_company_name_does_not_inflate_both_bullet_budgets():
    """The budget path's variant was worse: it attached the project to BOTH
    tenures, so one tenure's evidence raised the other's relevance tier and
    therefore its bullet ceiling."""
    from applire.services.cv_budget import attach_projects

    _, profile = _two_tenures_one_employer()
    entries = [
        {"id": "work-1", "company": "Acme GmbH", "role": "Junior Engineer", "bullets": []},
        {"id": "work-2", "company": "Acme GmbH", "role": "Senior Engineer", "bullets": []},
    ]
    enriched = attach_projects(entries, profile["projects"])
    assert [len(e["projects"]) for e in enriched] == [0, 0]


def test_unambiguous_company_name_still_resolves():
    """The regression guard: the name path is narrowed, not removed — a single
    tenure at an employer still nests by name (the CV-extraction shape)."""
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry
    from applire.services.cv import _nest_projects
    from applire.services.cv_budget import attach_projects

    work = [
        TailoredWorkEntry(
            id="work-1", company="Acme GmbH", role="Senior Engineer",
            start_date="2018-02", end_date=None, bullets=[],
        )
    ]
    tailored = TailoredCVData(
        summary="s", contact=TailoredContact(), work_history=work, skills=[]
    )
    profile = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer"}
        ],
        "projects": [
            {
                "name": "Migration",
                "responsibilities": ["Owned by work-1"],
                "associated_experience": "Acme GmbH",
            }
        ],
    }
    nested = _nest_projects(tailored, profile)
    assert [p.name for p in (nested.work_history[0].projects or [])] == ["Migration"]

    enriched = attach_projects(
        [{"id": "work-1", "company": "Acme GmbH", "role": "Senior Engineer", "bullets": []}],
        profile["projects"],
    )
    assert len(enriched[0]["projects"]) == 1


def _two_tenures_same_company_and_role():
    """A rehire into the SAME title — the shape the first ambiguity fix missed.

    ADR-072 clause 5 guarded the company-only fallback but left the exact
    company+role branch on first-match-wins, so an UNAMBIGUOUS association (the
    project names the vault id of the second tenure) still resolved to whichever
    tailored entry happened to be listed first. Found by this branch's own
    adversarial pass, 2026-08-02.
    """
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry

    work = [
        TailoredWorkEntry(
            id="work-1", company="Acme GmbH", role="Consultant",
            start_date="2015-01", end_date="2017-01", bullets=[],
        ),
        TailoredWorkEntry(
            id="work-2", company="Acme GmbH", role="Consultant",
            start_date="2020-01", end_date=None, bullets=[],
        ),
    ]
    tailored = TailoredCVData(
        summary="s", contact=TailoredContact(), work_history=work, skills=[]
    )
    profile = {
        "work_experience": [
            {"id": "work-1", "company": "Acme GmbH", "role": "Consultant"},
            {"id": "work-2", "company": "Acme GmbH", "role": "Consultant"},
        ],
        "projects": [
            {
                "name": "Second-tenure Programme",
                "responsibilities": ["Evidence owned by work-2 only"],
                "associated_experience": "work-2",  # the vault id — unambiguous
            }
        ],
    }
    return tailored, profile


def test_an_unambiguous_id_reaches_the_right_tenure_even_when_role_also_matches():
    """The association carries a vault id, so there is nothing to guess. Before
    the fix the id resolved the SOURCE entry correctly and was then thrown away:
    the tailored-side lookup re-matched on company+role strings and returned the
    first hit — the tenure that ended in 2017."""
    from applire.services.cv import _nest_projects

    tailored, profile = _two_tenures_same_company_and_role()
    nested = _nest_projects(tailored, profile)

    by_id = {e.id: [p.name for p in (e.projects or [])] for e in nested.work_history}
    assert by_id["work-2"] == ["Second-tenure Programme"]
    assert by_id["work-1"] == []
    assert not (nested.projects or [])


def test_the_tailored_lookup_uses_the_id_not_the_company_role_strings():
    """Structural: TailoredWorkEntry.id exists so this lookup never needs string
    matching (schemas/cv.py). Every sibling pass — _apply_role_facts,
    _restore_ledger_bullets — matches by id; this one is now consistent with
    them. Proven by making the strings USELESS: identical company and role on
    both entries, distinct ids."""
    from applire.services.cv import _nest_projects

    tailored, profile = _two_tenures_same_company_and_role()
    profile["projects"][0]["associated_experience"] = "work-1"
    nested = _nest_projects(tailored, profile)

    by_id = {e.id: [p.name for p in (e.projects or [])] for e in nested.work_history}
    assert by_id["work-1"] == ["Second-tenure Programme"]
    assert by_id["work-2"] == []
