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

"""#312 — a project with no bullets must never reach the page as an orphan
bold heading.

Charter run #7 (case 2, ``operations_marcus_de``) delivered a CV PDF carrying

    PROJEKTE
    SAP-Rollout bei Rasselstein
    <nothing>

styled exactly like the well-populated project heading above it. To a reader
that is a section that failed to render — the worst thing to hand a skeptical
scanner. The stored data matched: ``{"name": "...", "bullets": []}``.

The empty state is MANUFACTURED BY OUR OWN CODE, not by the model:
``services.cv._suppress_duplicate_project_bullets`` (#169) drops every nested
project bullet that duplicates the parent role's own bullets, and its docstring
then deliberately KEPT the emptied project ("US187: the heading still carries
the project"). That choice is what #312 reverses — a heading over nothing
carries nothing.

Two guards, per the issue's belt-and-braces ask:

* **generation** — ``_nest_projects`` never emits a content-free project, and
  the #169 suppression drops a project it just emptied (mirroring what
  ``cv_budget.condense_to_budget`` already does when it cuts a project's last
  bullet);
* **render** — ``services.cv.strip_empty_projects`` runs in the render context
  for EVERY template, so no upstream pass (nor the ADR-054 agent door, which
  persists caller content verbatim and therefore has no generation-side pass at
  all) can ship the orphan.

The render half is parametrized over ``_TEMPLATE_FILES`` ITSELF — never a
hardcoded name list — so an eighth template added later is covered by
construction. It seeds ``tailored_data`` directly on a ready ``GeneratedCV``,
which isolates the render guard from the generation guards: if only the
generation half existed, these tests would still fail.
"""
import re
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv import _TEMPLATE_FILES  # noqa: E402

# The real run's shapes, kept verbatim so the test names the defect it pins.
_ROLE_BULLET = "Mitarbeit bei der Einführung von SAP in der Fertigung (PP/MM)"
_ORPHAN_NESTED = "SAP-Rollout bei Rasselstein"
_ORPHAN_STANDALONE = "Werksübergreifende Kennzahlen-Initiative"
_KEPT_PROJECT = "Einführung eines MES-Systems"
_KEPT_BULLET = "MES-Einführung über drei Werke gesteuert"


def _visible(html: str) -> str:
    """The rendered page minus HTML comments — several templates carry a
    ``<!-- ===== PROJEKTE (standalone) ===== -->`` section marker that is never
    on the page but would match a naive substring assertion."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def _tailored_with_orphans() -> dict:
    """A tailored CV in exactly the delivered shape: one project that still has
    bullets, one nested project emptied by the #169 suppression, and one
    standalone project that never had any."""
    return {
        "contact": {"name": "Marcus Weber", "email": "marcus@example.com"},
        "summary": "Erfahrener Produktionsleiter.",
        "work_history": [
            {
                "id": "w1",
                "company": "Rasselstein GmbH",
                "role": "Produktionsleiter",
                "start_date": "2004-07",
                "end_date": "2011-07",
                "bullets": [_ROLE_BULLET],
                "projects": [
                    {"name": _KEPT_PROJECT, "bullets": [_KEPT_BULLET]},
                    {"name": _ORPHAN_NESTED, "bullets": []},
                ],
            },
        ],
        "projects": [{"name": _ORPHAN_STANDALONE, "bullets": []}],
        "skills": ["SAP PP/MM"],
        "education": [],
        "languages": [],
    }


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.color_profile  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_ready_cv(db, *, template: str, tailored: dict, jd_language: str = "de"):
    from applire.models.cv import CVGenerationStatus, GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    db.add(User(id=uuid.uuid4(), email=f"orphan-{uuid.uuid4().hex[:8]}@test.com"))
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Produktionsleitung",
        role_title="Produktionsleiter",
        company_name="Rasselstein GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement=jd_language,
        jd_language=jd_language,
    )
    db.add(job)
    profile = MasterProfile(profile_json={"personal_info": {"name": "Marcus Weber"}})
    db.add(profile)
    await db.flush()

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template=template,
        tailored_data=tailored,
        status=CVGenerationStatus.ready.value,
        target_pages=2,
    )
    db.add(cv)
    await db.commit()
    await db.refresh(cv)
    return cv


# ---------------------------------------------------------------------------
# Render guard — every template, both project render paths.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_bullet_less_project_never_renders_a_heading(db, template_name):
    from applire.services.cv import get_cv_html

    cv = await _seed_ready_cv(db, template=template_name, tailored=_tailored_with_orphans())
    html = await get_cv_html(cv.id, db)

    assert _ORPHAN_NESTED not in html, (
        f"[{template_name}] a nested project with no bullets rendered its heading "
        "— the orphan bold line of #312"
    )
    assert _ORPHAN_STANDALONE not in html, (
        f"[{template_name}] a standalone project with no bullets rendered its heading"
    )
    # The populated project and its parent role are untouched.
    assert _KEPT_PROJECT in html and _KEPT_BULLET in html, (
        f"[{template_name}] the guard must not touch a project that has content"
    )
    assert _ROLE_BULLET in html, (
        f"[{template_name}] the role bullet carrying the fact must survive"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_standalone_projects_section_disappears_when_all_are_empty(db, template_name):
    """Dropping the last standalone project must also take the PROJEKTE section
    title with it — the templates gate the section on ``cv.projects``, so the
    guard has to empty the LIST, not blank the entries."""
    from applire.services.cv import get_cv_html
    from applire.templates.labels import cv_labels

    tailored = _tailored_with_orphans()
    # Strip the one project that has content, so nothing is left to carry a section.
    tailored["work_history"][0]["projects"] = [{"name": _ORPHAN_NESTED, "bullets": []}]
    cv = await _seed_ready_cv(db, template=template_name, tailored=tailored)
    html = await get_cv_html(cv.id, db)

    label = cv_labels("de")["projects"]
    assert label.lower() not in _visible(html).lower(), (
        f"[{template_name}] the {label!r} heading survived with no projects under it"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_populated_projects_are_unaffected(db, template_name):
    """Non-regression: with every project populated, nothing is dropped."""
    from applire.services.cv import get_cv_html
    from applire.templates.labels import cv_labels

    tailored = _tailored_with_orphans()
    tailored["work_history"][0]["projects"] = [
        {"name": _KEPT_PROJECT, "bullets": [_KEPT_BULLET]},
    ]
    tailored["projects"] = [{"name": _ORPHAN_STANDALONE, "bullets": ["Kennzahlen vereinheitlicht"]}]
    cv = await _seed_ready_cv(db, template=template_name, tailored=tailored)
    html = await get_cv_html(cv.id, db)

    assert _KEPT_PROJECT in html
    assert _ORPHAN_STANDALONE in html
    assert cv_labels("de")["projects"].lower() in _visible(html).lower()


@pytest.mark.asyncio
async def test_project_whose_bullets_are_only_blank_strings_is_dropped(db):
    """A project whose bullets are present but whitespace-only renders the same
    orphan heading — emptiness is about CONTENT, not list length."""
    from applire.services.cv import get_cv_html

    tailored = _tailored_with_orphans()
    tailored["work_history"][0]["projects"] = [
        {"name": _KEPT_PROJECT, "bullets": [_KEPT_BULLET]},
        {"name": _ORPHAN_NESTED, "bullets": ["   ", ""]},
    ]
    cv = await _seed_ready_cv(db, template="classic_german", tailored=tailored)
    html = await get_cv_html(cv.id, db)

    assert _ORPHAN_NESTED not in html
    assert _KEPT_PROJECT in html


# ---------------------------------------------------------------------------
# Render guard reaches the section-editor preview too (the second render site).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_section_editor_preview_also_suppresses_the_orphan(db):
    """``PATCH /api/cv/{id}/sections/{sid}`` renders its own preview HTML — the
    second and only other place a user's CV reaches a template. ADR-066: one
    logical operation, one implementation; the guard must be on both call sites."""
    from applire.services.cv_section_editor import build_content_snapshot, patch_cv_section
    from applire.schemas.cv import TailoredCVData

    tailored = _tailored_with_orphans()
    cv = await _seed_ready_cv(db, template="classic_german", tailored=tailored)
    cv.content_snapshot = build_content_snapshot(TailoredCVData.model_validate(tailored))
    await db.commit()

    resp = await patch_cv_section(
        cv_id=cv.id,
        section_id="introduction",
        content="Produktionsleiter mit SAP-Erfahrung.",
        save_to_profile=False,
        db=db,
        background_tasks=None,
    )

    assert _ORPHAN_NESTED not in resp.html
    assert _ORPHAN_STANDALONE not in resp.html
    assert _KEPT_PROJECT in resp.html


# ---------------------------------------------------------------------------
# Generation guards — the empty project never gets built in the first place.
# ---------------------------------------------------------------------------


def _tailored_role(bullets: list[str]):
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry

    return TailoredCVData(
        contact=TailoredContact(name="Marcus Weber"),
        work_history=[
            TailoredWorkEntry(
                id="w1", company="Rasselstein GmbH", role="Produktionsleiter",
                start_date="2004-07", bullets=bullets,
            ),
        ],
    )


def test_nest_projects_skips_a_source_project_with_no_content():
    """A vault project carrying only a name has nothing to put under a heading."""
    from applire.services.cv import _nest_projects

    nested = _nest_projects(
        _tailored_role(["Werke gesteuert"]),
        {
            "work_experience": [
                {"id": "w1", "company": "Rasselstein GmbH", "role": "Produktionsleiter"},
            ],
            "projects": [
                {"name": _ORPHAN_NESTED, "associated_experience": "w1"},
                {"name": _ORPHAN_STANDALONE, "associated_experience": None},
            ],
        },
    )

    assert nested.work_history[0].projects == []
    assert nested.projects == []


def test_nest_projects_drops_the_project_the_169_suppression_emptied():
    """The #312 root cause: #169 strips a nested project's bullets because the
    parent role already states the same fact, and the emptied project was then
    kept for its heading alone. The fact is not lost — it is on the role bullet."""
    from applire.services.cv import _nest_projects

    nested = _nest_projects(
        _tailored_role([_ROLE_BULLET]),
        {
            "work_experience": [
                {"id": "w1", "company": "Rasselstein GmbH", "role": "Produktionsleiter"},
            ],
            "projects": [
                {
                    "name": _ORPHAN_NESTED,
                    "responsibilities": [_ROLE_BULLET],
                    "associated_experience": "w1",
                },
            ],
        },
    )

    assert nested.work_history[0].projects == [], (
        "#312: a project emptied by the #169 duplicate suppression must not "
        "survive as a bare heading"
    )
    # The fact itself is still on the page, via the role bullet that caused the
    # suppression — nothing the candidate owns has been lost.
    assert nested.work_history[0].bullets == [_ROLE_BULLET]


def test_nest_projects_keeps_a_partially_suppressed_project():
    """Non-regression on #169: suppression that leaves at least one bullet keeps
    the project, heading and all."""
    from applire.services.cv import _nest_projects

    nested = _nest_projects(
        _tailored_role([_ROLE_BULLET]),
        {
            "work_experience": [
                {"id": "w1", "company": "Rasselstein GmbH", "role": "Produktionsleiter"},
            ],
            "projects": [
                {
                    "name": _ORPHAN_NESTED,
                    "responsibilities": [_ROLE_BULLET, "Stammdaten migriert"],
                    "associated_experience": "w1",
                },
            ],
        },
    )

    assert [p.name for p in nested.work_history[0].projects] == [_ORPHAN_NESTED]
    assert nested.work_history[0].projects[0].bullets == ["Stammdaten migriert"]
