# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#786 L9/L18 (Hazelnut build 3, founder design): the page ladder.

A CV whose length is carried by list sections shipped at 3 pages (Emma, UX audit
2026-10-20; edge CV 4121fa73) and once at 5 pages on a 2-page target (edge CV
8ad60e4d): ``condense_to_budget`` cuts bullets only and returned ``changed=False``.
The founder's rule: never more than target + 1; cut in a fixed order — duplicated
PROJEKTE, off-job skill chips, repeated "Branche:" lines, the oldest
certification/education detail — never a role's bullets, and name every cut.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.cv_page_ladder import (
    DUPLICATE_PROJECT,
    OFF_JOB_SKILLS,
    OLDEST_CERTIFICATION,
    OLDEST_EDUCATION,
    REPEATED_INDUSTRY,
    ladder_steps,
    merge_cuts,
)


def _heavy() -> dict:
    """Section-driven volume, modelled on CV 8ad60e4d: a PROJEKTE block repeating
    a role project, off-job chips, identical industry lines, 4 certs, 4 degrees."""
    return {
        "contact": {"name": "Marcus Weber", "email": "marcus@example.com"},
        "summary": "Produktionsleiter mit 14 Jahren Erfahrung.",
        "work_history": [
            {"id": "r1", "company": "Musterplast", "role": "Produktionsleiter",
             "start_date": "2017-04", "end_date": None,
             "bullets": ["Ausschussquote von 4,1 % auf 2,3 % gesenkt."],
             "projects": [{"name": "MES-Einführung", "bullets": ["MES in 2 Bereichen eingeführt."]}],
             "industry_context": "Diskrete Fertigung"},
            {"id": "r2", "company": "Muster Umformtechnik", "role": "Schichtleiter",
             "start_date": "2011-08", "end_date": "2017-03",
             "bullets": ["Schicht mit 14 Mitarbeitenden geführt."],
             "industry_context": "diskrete  fertigung"},
            {"id": "r3", "company": "Muster Umformtechnik", "role": "Industriemechaniker",
             "start_date": "2004-07", "end_date": "2011-07",
             "bullets": ["Instandhaltung."], "industry_context": "Blechumformung"},
        ],
        "projects": [{"name": "MES-Einführung", "bullets": ["MES in 2 Bereichen eingeführt."]},
                     {"name": "Eigenes Projekt", "bullets": ["Etwas anderes."]}],
        "skills": ["Lean Management", "SMED", "Kosmetik-Verpackungen", "Golf", "MES"],
        "certifications": [
            {"name": "REFA-Grundschein", "date_obtained": "2013"},
            {"name": "Sicherheitsbeauftragter", "date_obtained": "2015"},
            {"name": "Six Sigma Green Belt", "date_obtained": "2019"},
            {"name": "Erste Hilfe", "date_obtained": "2008"},
        ],
        "education": [
            {"degree": "Industriemeister Metall", "institution": "IHK", "end_date": "2010"},
            {"degree": "Ausbildung Industriemechaniker", "institution": "Muster", "end_date": "2004"},
            {"degree": "Realschulabschluss", "institution": "Realschule", "end_date": "2000"},
            {"degree": "Hauptschulabschluss", "institution": "Hauptschule", "end_date": "1998"},
        ],
    }


_JOB_TERMS = {"lean management", "smed", "mes"}


def _tier(skill: str) -> int:
    return 0 if skill.casefold() in _JOB_TERMS else 3


def test_ladder_cuts_in_the_founders_order_and_names_every_cut():
    steps = list(ladder_steps(_heavy(), _tier))
    kinds = [s.cut.kind for s in steps]
    assert kinds == [
        DUPLICATE_PROJECT, OFF_JOB_SKILLS, REPEATED_INDUSTRY,
        OLDEST_CERTIFICATION, OLDEST_CERTIFICATION, OLDEST_CERTIFICATION,
        OLDEST_CERTIFICATION, OLDEST_EDUCATION, OLDEST_EDUCATION,
    ]
    assert steps[0].cut.items == ("MES-Einführung",)
    assert steps[1].cut.items == ("Kosmetik-Verpackungen", "Golf")
    assert steps[2].cut.items == ("Muster Umformtechnik: diskrete  fertigung",)
    # oldest first: 2008, 2013, 2015, 2019
    assert [s.cut.items[0] for s in steps[3:7]] == [
        "Erste Hilfe", "REFA-Grundschein", "Sicherheitsbeauftragter", "Six Sigma Green Belt",
    ]
    assert [s.cut.items[0] for s in steps[7:]] == [
        "Hauptschulabschluss — Hauptschule", "Realschulabschluss — Realschule",
    ]
    final = steps[-1].data
    assert [p["name"] for p in final["projects"]] == ["Eigenes Projekt"]
    assert final["skills"] == ["Lean Management", "SMED", "MES"]
    assert [w["industry_context"] for w in final["work_history"]] == [
        "Diskrete Fertigung", None, "Blechumformung",
    ]
    assert len(final["education"]) == 2


def test_ladder_never_touches_a_bullet_the_summary_or_a_job_relevant_skill():
    data = _heavy()
    for step in ladder_steps(data, _tier):
        d = step.data
        assert d["summary"] == data["summary"]
        assert [w["bullets"] for w in d["work_history"]] == [w["bullets"] for w in data["work_history"]]
        assert [w["projects"] for w in d["work_history"] if "projects" in w] == [
            data["work_history"][0]["projects"]
        ]
        assert {"Lean Management", "SMED", "MES"} <= set(d["skills"])
    assert data == _heavy(), "the input must stay unmutated"


def test_ladder_spares_a_pinned_skill_and_certification():
    pinned = {"golf", "refa-grundschein"}
    steps = list(ladder_steps(_heavy(), _tier, pinned=lambda t: t.casefold() in pinned))
    final = steps[-1].data
    assert "Golf" in final["skills"]
    assert [c["name"] for c in final["certifications"]] == ["REFA-Grundschein"]


def test_ladder_without_a_rank_skips_the_skills_rung():
    kinds = {s.cut.kind for s in ladder_steps(_heavy(), None)}
    assert OFF_JOB_SKILLS not in kinds


def test_merge_cuts_folds_one_kind_into_one_entry():
    steps = list(ladder_steps(_heavy(), _tier))
    merged = merge_cuts([s.cut for s in steps])
    assert [m["kind"] for m in merged] == [
        DUPLICATE_PROJECT, OFF_JOB_SKILLS, REPEATED_INDUSTRY, OLDEST_CERTIFICATION, OLDEST_EDUCATION,
    ]
    assert len(merged[3]["items"]) == 4


# --- the seam: _measure_and_condense holds every delivery at <= target + 1 ----------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, target_pages=2):
    from applire.models.cv import GeneratedCV
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv_section_editor import build_content_snapshot

    data = _heavy()
    cv = GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        tailored_data=data, template="classic_german", status="ready",
        content_snapshot=build_content_snapshot(TailoredCVData.model_validate(data)),
        section_overrides=None, ats_report=None, target_pages=target_pages,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add(cv)
    await db.commit()
    return cv


def _volume_pages(cv):
    """A render stub whose page count follows the record's list-section volume —
    5 pages for the seeded CV, one page fewer per ~3 units removed."""
    def pages():
        d = cv.tailored_data
        units = (
            len(d.get("projects") or []) * 2 + len(d.get("skills") or [])
            + sum(1 for w in d["work_history"] if w.get("industry_context"))
            + len(d.get("certifications") or []) + len(d.get("education") or [])
        )
        return max(1, units // 3 - 1)
    return pages


async def _measure(cv, db, *, target, condense=False, page_ladder=True):
    from applire.services.cv import CondenseContext, _measure_and_condense
    from applire.services.cv_budget import BudgetResult

    ctx = CondenseContext(
        budgets=BudgetResult(roles={}, tiers={}, target_pages=target, region="DACH"),
        target=target, condense=condense, page_ladder=page_ladder, skill_tier=_tier,
    )
    pages = _volume_pages(cv)
    with (
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html/>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              side_effect=lambda _pdf: ("text", pages())),
    ):
        return await _measure_and_condense(cv, db, ctx)


@pytest.mark.asyncio
async def test_measure_holds_the_delivery_at_target_plus_one(db):
    cv = await _seed(db, target_pages=2)
    assert _volume_pages(cv)() == 5, "fixture must start well over target + 1"
    measured = await _measure(cv, db, target=2)
    assert measured.page_count <= 3
    assert measured.page_count == _volume_pages(cv)()
    assert measured.condensation_exhausted is True  # still over x: the user decides
    kinds = [c.kind for c in measured.ladder_cuts]
    assert kinds and kinds[0] == DUPLICATE_PROJECT
    # stops at the limit — the ladder is not run to its end
    assert len(kinds) < len(list(ladder_steps(_heavy(), _tier)))
    # bullets intact, snapshot rebuilt from the cut data
    assert [w["bullets"] for w in cv.tailored_data["work_history"]] == [
        w["bullets"] for w in _heavy()["work_history"]
    ]
    snap_skills = cv.content_snapshot.get("skills")
    assert snap_skills is None or "Golf" not in str(snap_skills)


@pytest.mark.asyncio
async def test_measure_leaves_a_cv_at_target_plus_one_alone(db):
    cv = await _seed(db, target_pages=4)  # 5 pages <= 4 + 1
    measured = await _measure(cv, db, target=4)
    assert measured.page_count == 5
    assert measured.ladder_cuts == ()
    assert cv.tailored_data == _heavy()


@pytest.mark.asyncio
async def test_measure_without_the_ladder_keeps_the_overrun(db):
    cv = await _seed(db, target_pages=2)
    measured = await _measure(cv, db, target=2, page_ladder=False)
    assert measured.page_count == 5 and measured.ladder_cuts == ()


def test_pin_predicate_protects_an_active_cv_skill_pin():
    from applire.services.cv import _ladder_pin_predicate

    pin = SimpleNamespace(stale=False, targets=("cv",), entry_type="skill", quote="Golf")
    pred = _ladder_pin_predicate([pin])
    assert pred("Golf") and not pred("Lean Management")
    assert _ladder_pin_predicate([]) is None
