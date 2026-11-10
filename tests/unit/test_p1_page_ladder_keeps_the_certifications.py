# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:256 / ruling P-1 = A (Hazelnut build 8, main default MD8-4): the automatic
page ladder emptied the certifications section.

Rung 4 ("the oldest certification, one at a time") removed one certification per
re-render. One certification is one or two lines, so no single step ever flipped
a page, and the rung ran until the section was gone. Delivery runs 7 and 8 of
build 5: 3 of 6 CVs lost all 5 certifications. In run 8 rep a, the ladder also cut
8 chips and the Abitur and the CV **still** ended at 4 pages over a limit of 3.
The certifications went and bought nothing.

The ruling, automatic ``target + 1`` ladder only (the person's "Kürzen" proposal
is unchanged and names every item):
1. a certification the posting names (the #192 rank the skills rung uses: tier
   below 3) is never cut;
2. the ``AUTO_CERT_KEEP`` newest certifications always stay (precedent: D-23
   ``AUTO_SKILL_FLOOR``, ``_KEEP_EDUCATION``; ADR-099: an automatic cut must not
   empty a section);
3. when the ladder ends still over the limit, the trailing cuts that did not lower
   the page count are put back, so ``auto_cuts`` (the card) names only what stayed cut.

Synthetic data only.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.cv_page_ladder import (
    AUTO_CERT_KEEP,
    OFF_JOB_SKILLS,
    OLDEST_CERTIFICATION,
    OLDEST_EDUCATION,
    ladder_steps,
    merge_cuts,
    steps_that_paid,
)

_CERTS = [
    {"name": "Musterzertifikat Projektleitung", "date_obtained": "2012"},
    {"name": "ITIL 4 Foundation", "date_obtained": "2014"},
    {"name": "Scrum Master Basis", "date_obtained": "2016"},
    {"name": "Testzertifikat Architektur", "date_obtained": "2019"},
    {"name": "Cloud-Grundlagen Probe", "date_obtained": "2022"},
]


def _cv() -> dict:
    return {
        "contact": {"name": "Paula Probe", "email": "paula.probe@example.de"},
        "summary": "Synthetische Probe für die Seitenleiter.",
        "work_history": [
            {"id": "r1", "company": "Probe GmbH", "role": "Leitung IT", "start_date": "2018-01",
             "end_date": None, "bullets": ["Etwas Belegtes."]},
        ],
        "skills": ["Python", "Golf", "Häkeln", "Segeln"],
        "certifications": [dict(c) for c in _CERTS],
        "education": [
            {"degree": "M.Sc. Informatik", "institution": "Probe-Uni", "end_date": "2010"},
            {"degree": "B.Sc. Informatik", "institution": "Probe-Uni", "end_date": "2008"},
            {"degree": "Abitur", "institution": "Probe-Gymnasium", "end_date": "2004"},
        ],
    }


_JOB = {"python", "itil 4 foundation"}


def _tier(text: str) -> int:
    return 0 if text.casefold() in _JOB else 3


def _cert_names(data: dict) -> list[str]:
    return [c["name"] for c in data.get("certifications") or []]


# --- 1 + 2: the rung keeps the newest and what the posting names ------------------


def test_the_automatic_rung_keeps_the_newest_and_the_posting_named_certification():
    steps = list(ladder_steps(
        _cv(), _tier, cert_keep=AUTO_CERT_KEEP, cert_protected=lambda n: _tier(n) < 3,
    ))
    cert_cuts = [s.cut.items[0] for s in steps if s.cut.kind == OLDEST_CERTIFICATION]
    # oldest first, only the two that are neither newest-2 nor posting-named
    assert cert_cuts == ["Musterzertifikat Projektleitung", "Scrum Master Basis"]
    assert _cert_names(steps[-1].data) == [
        "ITIL 4 Foundation", "Testzertifikat Architektur", "Cloud-Grundlagen Probe",
    ]


def test_the_section_is_never_emptied_even_without_a_posting_match():
    steps = list(ladder_steps(_cv(), None, cert_keep=AUTO_CERT_KEEP))
    assert len(_cert_names(steps[-1].data)) == AUTO_CERT_KEEP == 2
    assert _cert_names(steps[-1].data) == ["Testzertifikat Architektur", "Cloud-Grundlagen Probe"]


def test_two_or_fewer_certifications_are_never_cut_by_the_automatic_rung():
    data = _cv()
    data["certifications"] = data["certifications"][:2]
    kinds = [s.cut.kind for s in ladder_steps(data, None, cert_keep=AUTO_CERT_KEEP)]
    assert OLDEST_CERTIFICATION not in kinds


def test_undated_certifications_go_before_dated_ones_and_newest_uses_the_list_order_on_ties():
    data = _cv()
    data["certifications"] = [
        {"name": "A", "date_obtained": "2020"},
        {"name": "B", "date_obtained": None},
        {"name": "C", "date_obtained": "2020"},
    ]
    steps = list(ladder_steps(data, None, cert_keep=AUTO_CERT_KEEP))
    assert [s.cut.items[0] for s in steps if s.cut.kind == OLDEST_CERTIFICATION] == ["B"]
    assert _cert_names(steps[-1].data) == ["A", "C"]


def test_the_persons_proposal_is_unchanged_and_may_still_cut_every_certification():
    """The ruling covers the automatic ladder only: the default call (the proposal's)
    still walks every certification, oldest first, and names each one."""
    steps = list(ladder_steps(_cv(), _tier))
    cert_cuts = [s.cut.items[0] for s in steps if s.cut.kind == OLDEST_CERTIFICATION]
    assert cert_cuts == [c["name"] for c in _CERTS]


# --- 3: cuts that bought no page are put back --------------------------------------


@pytest.mark.parametrize(
    ("before", "counts", "limit", "kept"),
    [
        (5, [5, 5, 4, 4, 4], 3, 3),  # the 3rd step lowered the count; steps 4-5 bought nothing
        (5, [5, 5, 5], 3, 0),        # nothing paid: everything goes back
        (5, [4, 3], 3, 2),           # reached the limit: every step stays
        (4, [4, 4, 3], 3, 3),        # the last step reached it (cumulative cuts paid together)
        (5, [], 3, 0),               # no step at all
        (6, [5, 5, 4, 4], 3, 3),     # two gains: keep through the later one
    ],
)
def test_steps_that_paid(before, counts, limit, kept):
    assert steps_that_paid(before, counts, limit) == kept


# --- the seam: _measure_and_condense (generation's automatic ladder) ---------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, data):
    from applire.models.cv import GeneratedCV
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv_section_editor import build_content_snapshot

    cv = GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        tailored_data=data, template="classic_german", status="ready",
        content_snapshot=build_content_snapshot(TailoredCVData.model_validate(data)),
        section_overrides=None, ats_report=None, target_pages=2,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add(cv)
    await db.commit()
    return cv


async def _measure(cv, db, pages):
    from applire.services.cv import CondenseContext, _measure_and_condense
    from applire.services.cv_budget import BudgetResult

    ctx = CondenseContext(
        budgets=BudgetResult(roles={}, tiers={}, target_pages=2, region="DACH"),
        target=2, condense=False, page_ladder=True, skill_tier=_tier,
    )
    with (
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html/>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              side_effect=lambda _pdf: ("text", pages(cv.tailored_data))),
    ):
        return await _measure_and_condense(cv, db, ctx)


@pytest.mark.asyncio
async def test_generation_keeps_the_newest_and_posting_named_certifications(db, monkeypatch):
    """Run 7 rep a's shape: the page flips only once the Abitur goes. Before the fix
    the rung cut all 5 certifications on the way there."""
    monkeypatch.setattr("applire.services.cv_page_ladder.AUTO_SKILL_FLOOR", 0)
    cv = await _seed(db, _cv())

    def pages(d):
        return 4 if len(d.get("education") or []) > 2 else 3

    measured = await _measure(cv, db, pages)
    assert measured.page_count == 3
    assert _cert_names(cv.tailored_data) == [
        "ITIL 4 Foundation", "Testzertifikat Architektur", "Cloud-Grundlagen Probe",
    ]
    cut = merge_cuts(list(measured.ladder_cuts))
    assert [c["kind"] for c in cut] == [OFF_JOB_SKILLS, OLDEST_CERTIFICATION, OLDEST_EDUCATION]
    assert cut[1]["items"] == ["Musterzertifikat Projektleitung", "Scrum Master Basis"]


@pytest.mark.asyncio
async def test_generation_puts_back_the_cuts_that_bought_no_page(db, monkeypatch):
    """Run 8 rep a's shape: the ladder runs out still over the limit. Only the cuts
    up to the last page gain stay; the record, its snapshot and ``ladder_cuts``
    (the card's ``auto_cuts``) agree on what stayed cut."""
    monkeypatch.setattr("applire.services.cv_page_ladder.AUTO_SKILL_FLOOR", 0)
    data = _cv()
    cv = await _seed(db, data)

    def pages(d):
        # 5 pages; dropping the off-job chips buys one page, nothing else buys any
        return 5 if len(d.get("skills") or []) > 1 else 4

    measured = await _measure(cv, db, pages)
    assert measured.page_count == 4
    assert measured.condensation_exhausted is True
    cut = merge_cuts(list(measured.ladder_cuts))
    assert [c["kind"] for c in cut] == [OFF_JOB_SKILLS], cut
    assert _cert_names(cv.tailored_data) == [c["name"] for c in _CERTS]
    assert len(cv.tailored_data["education"]) == 3
    assert cv.tailored_data["skills"] == ["Python"]
    snap = str(cv.content_snapshot.get("skills"))
    assert "Golf" not in snap and "Python" in snap


@pytest.mark.asyncio
async def test_generation_puts_everything_back_when_no_cut_paid(db, monkeypatch):
    monkeypatch.setattr("applire.services.cv_page_ladder.AUTO_SKILL_FLOOR", 0)
    data = _cv()
    cv = await _seed(db, data)
    measured = await _measure(cv, db, lambda _d: 5)
    assert measured.page_count == 5
    assert measured.ladder_cuts == ()
    assert cv.tailored_data == _cv()
