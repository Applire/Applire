# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US250 (E044, ADR-054) — render_agent_cv / render_agent_letter.

The agent door's core invariant: Applire renders and reports, it never
rewrites. Content must be persisted VERBATIM (modulo the security photo strip
and the letter chrome backfill), with origin='agent' and both reports in place
before the row is observable as 'ready'.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


PROFILE_JSON = {
    "personal_info": {"name": "Anna Bauer", "photo_url": "uploads/photos/anna.jpg"},
    "professional_summary": {"en": "Engineer focused on backend platforms."},
    "work_experience": [
        {
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "achievements": ["Led a team of 12 engineers."],
        }
    ],
    "skills": [{"name": "Python"}],
}

AGENT_CV_CONTENT = {
    "contact": {
        "name": "Anna Bauer",
        "email": "anna@example.de",
        "location": "Berlin",
        # hostile: agent points the photo at an arbitrary file on disk
        "photo_url": "/etc/passwd",
    },
    "summary": "Backend engineer with platform focus.",
    "work_history": [
        {
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "bullets": ["Led a team of 12 engineers."],
        }
    ],
    "skills": ["Python"],
    "education": [],
    "languages": [],
    "projects": [],
    "certifications": [],
    "show_photo": True,
}

AGENT_LETTER_CONTENT = {
    "header": {
        "name": "Anna Bauer",
        "address": "Hauptstraße 42, 10115 Berlin",
        "phone": None,
        "email": "anna@example.de",
        "photo_url": "/etc/passwd",  # hostile — must be stripped
    },
    "recipient": {
        "name": "Frau Schmidt",
        "title": None,
        "company": "TechVision GmbH",
        "address": None,
        "date": None,  # chrome: absent → injected
    },
    "body": {"paragraphs": ["Sehr geehrte Frau Schmidt,", "Hauptteil.", "Schluss."]},
    "signature": {"closing": None, "name": "Anna Bauer"},  # chrome: absent → injected
}


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            JobAnalysis(
                id=job_id,
                raw_text_hash="render-agent",
                raw_text="Backend Engineer job",
                role_title="Backend Engineer",
                required_skills=["Python"],
                nice_to_have_skills=[],
                keywords=["Python"],
                seniority_level="senior",
                company_culture_signals=[],
                language_requirement="de",
            ),
            make_master_profile(
                id=profile_id,
                profile_json=PROFILE_JSON,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return {"db": db, "job_id": job_id, "profile_id": profile_id}


def _cv_render_patches():
    return (
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages", return_value=("Anna Bauer Python", 3)),
    )


# ---------------------------------------------------------------------------
# CV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_agent_cv_persists_verbatim_with_reports(seeded):
    from applire.services.cv import render_agent_cv

    p1, p2, p3 = _cv_render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(
            dict(AGENT_CV_CONTENT), seeded["job_id"], seeded["db"], target_pages=1
        )

    assert record.origin == "agent"
    assert record.status == "ready"
    assert record.target_pages == 1  # per-call override wins
    assert record.content_snapshot is not None
    # "ready implies reports available" — same commit
    assert record.ats_report is not None
    assert record.truthfulness_report is not None
    # verbatim persistence modulo the photo strip: the profile's stored photo
    # replaces the hostile path (show_photo=True). "Verbatim" = schema-normalized
    # identity (pydantic fills declared defaults like work-entry id="" on dump);
    # no semantic content may change.
    from applire.schemas.cv import TailoredCVData

    expected = TailoredCVData.model_validate(AGENT_CV_CONTENT).model_dump(mode="json")
    persisted = record.tailored_data
    assert persisted["contact"]["photo_url"] == "uploads/photos/anna.jpg"
    for key in ("summary", "work_history", "skills", "education", "show_photo"):
        assert persisted[key] == expected[key]
    # page overrun (3 rendered vs target 1) is ADVISORY — content untouched
    assert persisted["work_history"][0]["bullets"] == ["Led a team of 12 engineers."]


@pytest.mark.asyncio
async def test_render_agent_cv_show_photo_false_strips_entirely(seeded):
    from applire.services.cv import render_agent_cv

    content = dict(AGENT_CV_CONTENT)
    content["show_photo"] = False
    p1, p2, p3 = _cv_render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(content, seeded["job_id"], seeded["db"])

    assert record.tailored_data["contact"]["photo_url"] is None


@pytest.mark.asyncio
async def test_render_agent_cv_ats_report_not_applicable_persisted_and_excluded(seeded):
    """E057/ADR-079 clause 4 groundwork (#629, story #637) — the CV twin of
    test_render_agent_letter_ats_report_not_applicable_persisted_and_excluded.
    Unlike the letter pipeline, production no longer calls the audit_cv
    wrapper (see its docstring) — services/cv._update_ats_report calls
    _audit_cv_text directly, so that is the patch target here. This
    substitutes _audit_cv_text's RETURN VALUE for one test only; it does not
    modify the frozen function's source, which this task's non-goals
    forbid."""
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    from applire.services.cv import render_agent_cv

    report = ATSReport(
        document="cv",
        checks=[
            ATSCheck(id="contact-name", status="pass"),
            ATSCheck(id="page-length", status="not_applicable"),
        ],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
        not_applicable=1,
    )
    p1, p2, p3 = _cv_render_patches()
    with p1, p2, p3, patch("applire.services.ats_audit._audit_cv_text", return_value=report):
        record = await render_agent_cv(dict(AGENT_CV_CONTENT), seeded["job_id"], seeded["db"])

    assert record.ats_report is not None
    assert record.ats_report["passed"] == 1
    assert record.ats_report["failed"] == 0
    assert record.ats_report["not_applicable"] == 1


@pytest.mark.asyncio
async def test_render_agent_cv_unknown_field_rejected_with_path(seeded):
    from applire.services.cv import render_agent_cv

    content = dict(AGENT_CV_CONTENT)
    content["work_history"] = [
        {**AGENT_CV_CONTENT["work_history"][0], "position": "typo-for-role"}
    ]
    with pytest.raises(ValueError) as exc_info:
        await render_agent_cv(content, seeded["job_id"], seeded["db"])
    assert "work_history[0].position" in str(exc_info.value)


@pytest.mark.asyncio
async def test_render_agent_cv_type_error_carries_field_path(seeded):
    from applire.services.cv import render_agent_cv

    content = dict(AGENT_CV_CONTENT)
    content["skills"] = "Python"  # must be a list
    with pytest.raises(ValidationError) as exc_info:
        await render_agent_cv(content, seeded["job_id"], seeded["db"])
    assert any(err["loc"][0] == "skills" for err in exc_info.value.errors())


@pytest.mark.asyncio
async def test_render_agent_cv_unknown_job_and_missing_profile(db):
    from applire.models.job import JobAnalysis
    from applire.services.cv import render_agent_cv

    with pytest.raises(LookupError):
        await render_agent_cv(dict(AGENT_CV_CONTENT), uuid.uuid4(), db)

    job_id = uuid.uuid4()
    db.add(
        JobAnalysis(
            id=job_id,
            raw_text_hash="no-profile",
            raw_text="x",
            role_title="X",
            required_skills=[],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="mid",
            company_culture_signals=[],
            language_requirement="de",
        )
    )
    await db.commit()
    with pytest.raises(LookupError):
        await render_agent_cv(dict(AGENT_CV_CONTENT), job_id, db)


# ---------------------------------------------------------------------------
# Cover letter
# ---------------------------------------------------------------------------


def _letter_patches():
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport

    report = ATSReport(
        document="cover_letter",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )
    return (
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            new=AsyncMock(return_value=b"%PDF"),
        ),
        patch("applire.services.ats_audit.audit_cover_letter", return_value=report),
    )


@pytest.mark.asyncio
async def test_render_agent_letter_chrome_injected_when_absent(seeded):
    from applire.services.cover_letter import render_agent_letter

    p1, p2 = _letter_patches()
    with p1 as render_mock, p2:
        cl = await render_agent_letter(
            dict(AGENT_LETTER_CONTENT), seeded["job_id"], seeded["db"]
        )

    assert cl.origin == "agent"
    assert cl.status == "ready"
    assert cl.ats_report is not None
    assert cl.truthfulness_report is not None
    # pre-render used the allow_unready path with the row already committed
    render_mock.assert_awaited_once_with(cl.id, allow_unready=True)
    # chrome injected (DE job): a real date and the German closing
    assert cl.letter_data["recipient"]["date"]
    assert cl.letter_data["signature"]["closing"] == "Mit freundlichen Grüßen"
    # photo stripped; body verbatim
    assert cl.letter_data["header"]["photo_url"] is None
    assert cl.letter_data["body"]["paragraphs"] == AGENT_LETTER_CONTENT["body"]["paragraphs"]


@pytest.mark.asyncio
async def test_render_agent_letter_ats_report_not_applicable_persisted_and_excluded(seeded):
    """E057/ADR-079 clause 4 groundwork (#629, story #637): render_agent_letter
    persists whatever ATSReport the audit engine returns verbatim — a
    not_applicable check must survive that path with its count in its own
    bucket, never folded into passed/failed. No producer emits one yet; this
    mocks the audit engine's return value the same way _letter_patches() does
    above, just with a not_applicable check added."""
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    from applire.services.cover_letter import render_agent_letter

    report = ATSReport(
        document="cover_letter",
        checks=[
            ATSCheck(id="contact-name", status="pass"),
            ATSCheck(id="page-length", status="not_applicable"),
        ],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
        not_applicable=1,
    )
    with (
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            new=AsyncMock(return_value=b"%PDF"),
        ),
        patch("applire.services.ats_audit.audit_cover_letter", return_value=report),
    ):
        cl = await render_agent_letter(
            dict(AGENT_LETTER_CONTENT), seeded["job_id"], seeded["db"]
        )

    assert cl.ats_report is not None
    assert cl.ats_report["passed"] == 1
    assert cl.ats_report["failed"] == 0
    assert cl.ats_report["not_applicable"] == 1


@pytest.mark.asyncio
async def test_render_agent_letter_missing_anrede_gets_the_generic_floor_564(seeded):
    """#564: the agent door has run the #224 floor since #224 — this pins it
    against the omission case, which no prior test in this file drove (every
    existing AGENT_LETTER_CONTENT fixture already opens with an author-
    written salutation, so the injection branch was never actually
    exercised here). Content without an Anrede at all must gain the generic
    DE floor as its own first paragraph on the PERSISTED row, with the
    agent's own opening pushed down — the same contract test 1 pins for the
    pipeline door (tests/unit/test_letter_salutation_floor_564.py)."""
    from applire.services.cover_letter import render_agent_letter

    content = {
        **AGENT_LETTER_CONTENT,
        "body": {"paragraphs": ["Hauptteil ohne Anrede.", "Schluss."]},
    }
    p1, p2 = _letter_patches()
    with p1, p2:
        cl = await render_agent_letter(content, seeded["job_id"], seeded["db"])

    assert cl.status == "ready"
    paragraphs = cl.letter_data["body"]["paragraphs"]
    assert paragraphs[0] == "Sehr geehrte Damen und Herren,"
    assert paragraphs[1:] == ["Hauptteil ohne Anrede.", "Schluss."]


@pytest.mark.asyncio
async def test_render_agent_letter_keeps_caller_chrome_verbatim(seeded):
    """Deviation from the pipeline (which OVERWRITES date+closing): the agent
    is the author — supplied chrome is kept (ADR-054 §4)."""
    from applire.services.cover_letter import render_agent_letter

    content = {
        **AGENT_LETTER_CONTENT,
        "recipient": {**AGENT_LETTER_CONTENT["recipient"], "date": "1. April 2026"},
        "signature": {"closing": "Beste Grüße", "name": "Anna Bauer"},
    }
    p1, p2 = _letter_patches()
    with p1, p2:
        cl = await render_agent_letter(content, seeded["job_id"], seeded["db"])

    assert cl.letter_data["recipient"]["date"] == "1. April 2026"
    assert cl.letter_data["signature"]["closing"] == "Beste Grüße"


@pytest.mark.asyncio
async def test_render_agent_letter_unknown_field_rejected(seeded):
    from applire.services.cover_letter import render_agent_letter

    content = {**AGENT_LETTER_CONTENT, "subject": "Bewerbung"}
    with pytest.raises(ValidationError) as exc_info:
        await render_agent_letter(content, seeded["job_id"], seeded["db"])
    assert "subject" in str(exc_info.value)


@pytest.mark.asyncio
async def test_render_agent_letter_pdf_failure_fails_open(seeded):
    """A Playwright failure must not fail the render — ATS degrades to NULL,
    the truthfulness self-audit (no PDF needed) still lands, row goes ready."""
    from applire.services.cover_letter import render_agent_letter

    with patch(
        "applire.services.cover_letter_pdf.render_pdf",
        new=AsyncMock(side_effect=RuntimeError("chromium died")),
    ):
        cl = await render_agent_letter(
            dict(AGENT_LETTER_CONTENT), seeded["job_id"], seeded["db"]
        )

    assert cl.status == "ready"
    assert cl.ats_report is None
    assert cl.truthfulness_report is not None


# ---------------------------------------------------------------------------
# ADR-082 clause 4 / ADR-058 — redundancy DETECTION reaches the agent door
# ---------------------------------------------------------------------------

#: Agent-authored content carrying the #659 shape: one achievement stated twice.
#: The caller wrote both wordings; Applire renders, checks and reports — it does
#: not rewrite them (ADR-054 §4).
_AGENT_CV_WITH_REDUNDANCY = {
    "contact": {"name": "Anna Bauer", "email": "anna@example.de", "location": "Berlin"},
    "summary": "Backend engineer with platform focus.",
    "work_history": [
        {
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "bullets": [
                "Led a team of twelve backend engineers through the platform "
                "migration from a monolith to services, delivered over eighteen months.",
                "Led a team of twelve backend engineers through the platform "
                "migration, moving the monolith onto services within eighteen months.",
            ],
        }
    ],
    "skills": [],
}


def _dupe_check(report_dict):
    return next(
        (c for c in (report_dict or {}).get("checks", []) if c.get("id") == "duplicate-bullets"),
        None,
    )


@pytest.mark.asyncio
async def test_duplicate_bullets_detection_reaches_the_agent_door(seeded):
    """ADR-058 parity, asserted BEHAVIOURALLY — the same duplicate-bearing payload
    is driven through the real agent door and the verdict is read off the PERSISTED
    report, not off a shared symbol.

    ADR-082 clause 4: the generation-side dedup passes (`_nest_projects`,
    `_suppress_duplicate_project_bullets`) deliberately do NOT run here — that is
    ADR-054 §4 verbatim persistence, not a parity gap. What parity is owed on is
    DETECTION, and it is satisfied because `_update_ats_report` runs on both doors.
    """
    from applire.services.cv import render_agent_cv

    p1, p2, p3 = _cv_render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(
            dict(_AGENT_CV_WITH_REDUNDANCY), seeded["job_id"], seeded["db"], target_pages=1
        )

    check = _dupe_check(record.ats_report)
    assert check is not None, "the agent door must emit duplicate-bullets at all"
    assert check["status"] == "fail", record.ats_report

    # ADR-054 §4 / ADR-082 clause 2-3: it REPORTS, it does not repair. The
    # caller's two bullets are both still there, verbatim, in the delivered row.
    assert record.tailored_data["work_history"][0]["bullets"] == \
        _AGENT_CV_WITH_REDUNDANCY["work_history"][0]["bullets"]


@pytest.mark.asyncio
async def test_agent_door_and_audit_seam_agree_on_the_same_content(seeded):
    """The other half of the behavioural claim: same input, same verdict. If the
    door ever grows its own copy of the predicate, these two diverge and this
    fails — which symbol-identity assertions cannot detect."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.ats_audit import _audit_cv_text
    from applire.services.cv import render_agent_cv

    p1, p2, p3 = _cv_render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(
            dict(_AGENT_CV_WITH_REDUNDANCY), seeded["job_id"], seeded["db"], target_pages=1
        )
    door_check = _dupe_check(record.ats_report)

    tailored = TailoredCVData.model_validate(_AGENT_CV_WITH_REDUNDANCY)
    text = "\n".join(b for w in tailored.work_history for b in (w.bullets or []))
    seam_report = _audit_cv_text(text, tailored, keywords=[])
    seam_check = next(c for c in seam_report.checks if c.id == "duplicate-bullets")

    assert door_check["status"] == seam_check.status
    assert door_check["details"] == seam_check.details


@pytest.mark.asyncio
async def test_agent_door_reports_pass_when_the_caller_wrote_distinct_bullets(seeded):
    """Negative control for the parity claim: the door is not simply always red."""
    from applire.services.cv import render_agent_cv

    content = dict(_AGENT_CV_WITH_REDUNDANCY)
    content["work_history"] = [
        {**_AGENT_CV_WITH_REDUNDANCY["work_history"][0],
         "bullets": [
             "Led a team of twelve backend engineers through the platform migration.",
             "Cut median API latency from 480 ms to 120 ms by adding a read-through cache.",
         ]}
    ]

    p1, p2, p3 = _cv_render_patches()
    with p1, p2, p3:
        record = await render_agent_cv(content, seeded["job_id"], seeded["db"], target_pages=1)

    check = _dupe_check(record.ats_report)
    assert check is not None and check["status"] == "pass", record.ats_report
