# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#670 — ADR-048 amended: the ledger refresh PERSISTS and RE-SCORES at the read seams.

Founder ruling 9 (Stracciatella RC walk-through, 2026-09-05). #592 made
`refresh_ledger_against_vault` run at the four document-facing reads and deliberately
left them read-only, which left the divergence its own report recorded: the generated
document sees the current vault while the persisted row — and with it the match score,
the Gaps screen and the interview routing — still sees the old one. **Ruling: persist.**
The convergence is worth a moving score.

Two consequences the ruling names explicitly, both under test here:

* **ADR-061 / #318's demotion direction is re-run at a READ.** `refresh_ledger_against_vault`'s
  own docstring records why #592 did not: the invariant is specified for PERSIST seams,
  and running it at a read newly demotes claimable rows whenever the vault SHRANK. That
  is the direction the ruling asks for.
* **A score the candidate has already been shown may move.** The E037 PQ-#3 monotonic-up
  clamp is therefore deliberately NOT applied at this seam — it exists where adding
  evidence can only raise the score, and this seam can remove evidence too.

**One named test per read seam** (the verification hierarchy's rule for a shared helper
at N call sites), plus an enumeration test over the positive set, so a seam that exists
in the prose and in no test name is visible rather than assumed.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile  # noqa: E402

_WORK_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

#: A ledger row the vault has since ANSWERED — #592's lift direction.
_STALE_GAP = {
    "concept": "Kubernetes",
    "surface_forms": ["Kubernetes"],
    "sources": ["required"],
    "fit_weight": 1.0,
    "status": "gap",
    "evidence": "",
    "claimable": False,
}
#: A ledger row the vault no longer backs — ADR-061's demotion direction, the half
#: #592 explicitly did not run at a read.
_UNBACKED_CLAIM = {
    "concept": "Rust",
    "surface_forms": ["Rust"],
    "sources": ["required"],
    "fit_weight": 1.0,
    "status": "direct",
    "evidence": "",
    "claimable": True,
}


def _profile() -> dict:
    return {
        "contact": {"first_name": "Anna", "last_name": "Bauer",
                    "email": "anna@example.com", "phone": None, "location": "Berlin",
                    "linkedin": None, "xing": None, "portfolio": None},
        "professional_summary": {"de": "Erfahrene Entwicklerin", "en": ""},
        "work_experience": [
            {
                "id": _WORK_ID, "company": "Acme GmbH", "role": "Software Engineer",
                "start_date": "2020-01", "end_date": None, "is_current": True,
                "responsibilities": [
                    "Betrieb der Services auf Kubernetes verantwortet.",
                    "Backend-Services in Python gebaut.",
                ],
            }
        ],
        "education": [], "skills": [], "languages": [], "certifications": [],
    }


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


async def _seed(db, ledger):
    from applire.models.cv import GeneratedCV
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job_id, profile_id, cv_id, gap_id = (uuid.uuid4() for _ in range(4))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="job", role_title="Engineer",
            required_skills=["Kubernetes", "Rust"], nice_to_have_skills=[],
            keywords=[], seniority_level="mid", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(id=profile_id, profile_json=_profile(),
                            created_at=now, updated_at=now),
        GapAnalysis(
            id=gap_id, job_analysis_id=job_id, profile_id=profile_id,
            match_score=0.5, critical_gaps=[], minor_gaps=[], strengths=[],
            keyword_gaps=[], category_a=[], category_b=[], category_c=[],
            keyword_ledger=ledger, gap_clusters=[], requirement_breakdown=[],
            created_at=now,
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id, tailored_data={},
            template="classic_german", status="pending", target_pages=2,
            created_at=now, expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return job_id, profile_id, cv_id, gap_id


async def _row(db, gap_id):
    from applire.models.gap import GapAnalysis

    await db.commit()
    db.expire_all()
    return await db.get(GapAnalysis, gap_id)


def _status(ledger, concept):
    return next((e["status"] for e in ledger if e["concept"] == concept), None)


# ---------------------------------------------------------------------------
# Seam 1 of 4 — `cv.py::_latest_keyword_ledger` ("cv ledger read")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_cv_ledger_read_persists_the_refreshed_row(db):
    """The ATS-audit / DOCX-report read. Before #670 this returned a corrected ledger
    and left the row stale, so the report and the Gaps screen disagreed."""
    from applire.services.cv import _latest_keyword_ledger

    job_id, _pid, _cid, gap_id = await _seed(db, [dict(_STALE_GAP)])
    returned = await _latest_keyword_ledger(db, job_id, profile_json=_profile())
    assert _status(returned, "Kubernetes") == "direct", "the read still corrects"
    row = await _row(db, gap_id)
    assert _status(row.keyword_ledger, "Kubernetes") == "direct", (
        "and now the persisted row agrees with what the document was built from"
    )


@pytest.mark.asyncio
async def test_seam_cv_ledger_read_rescopes_the_match_score(db):
    """ADR-048 §5: the score is re-sourced from the ledger. A persisted ledger with a
    stale score is the same divergence one field along."""
    job_id, _pid, _cid, gap_id = await _seed(db, [dict(_STALE_GAP)])
    from applire.services.cv import _latest_keyword_ledger

    before = (await _row(db, gap_id)).match_score
    await _latest_keyword_ledger(db, job_id, profile_json=_profile())
    row = await _row(db, gap_id)
    assert row.match_score != before
    assert row.requirement_breakdown, "the breakdown is written with the score"


# ---------------------------------------------------------------------------
# Seam 2 of 4 — `cv.py` generation read ("cv generation")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_cv_generation_persists_the_refreshed_row(db):
    """The generation read. Driven through `_render_cv_background`, not through the
    helper, because the seam under test is the CALL SITE — reverting it must turn this
    test red BY NAME while the helper's own tests stay green.

    `_latest_keyword_ledger` is stubbed out for exactly that reason, and the stub is the
    point of the test rather than a convenience: without it, reverting the generation
    seam left this test GREEN because the ATS audit's own read seam — running later in
    the same `_render_cv_background` call — persisted the row anyway. Two seams inside
    one function, and a test that cannot tell them apart measures the pair
    (`feedback_seam_test_per_call_site`: revert each call site independently and read
    which test fails)."""
    from applire.services.cv import _render_cv_background

    job_id, profile_id, cv_id, gap_id = await _seed(db, [dict(_STALE_GAP)])
    provider = AsyncMock()
    provider.aparse_json.return_value = {
        "summary": "Erfahrene Entwicklerin.",
        "work": [{"id": _WORK_ID, "bullets": ["Backend-Services in Python gebaut."]}],
        "skills": ["Python"],
    }
    extract = MagicMock(side_effect=lambda pdf: ("text", 2))
    with patch("applire.services.cv.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(patch("applire.services.cv.get_provider", return_value=provider))
            stack.enter_context(patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 0))
            stack.enter_context(patch("applire.services.cv.get_cv_html",
                                      new=AsyncMock(return_value="<html></html>")))
            stack.enter_context(patch("applire.services.cv._html_to_pdf",
                                      new=AsyncMock(return_value=b"pdf")))
            stack.enter_context(patch("applire.services.ats_audit.extract_text_and_pages",
                                      new=extract))
            stack.enter_context(patch(
                "applire.services.cv._latest_keyword_ledger",
                new=AsyncMock(side_effect=lambda db_, jid, **kw: [dict(_STALE_GAP)]),
            ))
            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    row = await _row(db, gap_id)
    assert _status(row.keyword_ledger, "Kubernetes") == "direct"


# ---------------------------------------------------------------------------
# Seams 3 and 4 — the letter reads (RULING W1-4: all four wired)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_letter_ledger_read_persists_the_refreshed_row(db):
    """The letter's ATS-report read — the CV twin, and it must not diverge from it:
    two documents built from one ledger with only one of them writing it back is the
    same defect this issue closes, wearing the other document's name."""
    from applire.services.cover_letter import _latest_keyword_ledger

    job_id, _pid, _cid, gap_id = await _seed(db, [dict(_STALE_GAP)])
    returned = await _latest_keyword_ledger(db, job_id, profile_json=_profile())
    assert _status(returned, "Kubernetes") == "direct"
    row = await _row(db, gap_id)
    assert _status(row.keyword_ledger, "Kubernetes") == "direct"


@pytest.mark.asyncio
async def test_seam_letter_generation_persists_the_refreshed_row(db):
    """The letter's GENERATION read (`cover_letter.py:1085`). Driven through the helper
    with the letter seam's own label rather than through `_render_letter_background`:
    the letter chain needs an application, a CV and a company row to reach its ledger
    read, and a fixture that heavy would test the chain rather than the seam. The call
    site itself is covered by the enumeration test below, which is what makes reverting
    it red."""
    from applire.models.gap import GapAnalysis
    from applire.services.keyword_ledger import refresh_persist_and_rescore

    _job_id, _pid, _cid, gap_id = await _seed(db, [dict(_STALE_GAP)])
    gap = await db.get(GapAnalysis, gap_id)
    ledger = await refresh_persist_and_rescore(
        gap, _profile(), db, seam="letter generation"
    )
    assert _status(ledger, "Kubernetes") == "direct"
    row = await _row(db, gap_id)
    assert _status(row.keyword_ledger, "Kubernetes") == "direct"


def test_every_refresh_call_site_is_enumerated_and_its_persistence_named():
    """Prove the coverage by exhausting the POSITIVE set, not by counting what is
    covered (`feedback_prove_absence_by_exhausting_positive_set`).

    ADR-048's amendment names FOUR document-facing read seams — two in `cv.py`, two in
    `cover_letter.py`. All four persist as of RULING W1-4. A seam that reverts to the
    read-only helper turns this red by file, which is the half a per-seam behavioural
    test cannot give: `cover_letter.py`'s generation seam sits behind a chain fixture
    too heavy to stand up here, so the call site is pinned structurally.
    """
    import re

    root = Path(__file__).parent.parent.parent / "backend" / "applire" / "services"
    for path, expected in (("cv.py", 2), ("cover_letter.py", 2)):
        text = (root / path).read_text(encoding="utf-8")
        calls = re.findall(
            r"(?<!def )(refresh_persist_and_rescore|refresh_ledger_against_vault)\(", text
        )
        assert calls.count("refresh_persist_and_rescore") == expected, (
            f"{path}: expected {expected} PERSISTING read seams, found {calls}"
        )
        assert "refresh_ledger_against_vault" not in calls, (
            f"{path}: a read seam still refreshes without persisting — the "
            "convergence #670 rules on is half-built"
        )


# ---------------------------------------------------------------------------
# The two directions, and the clamp that must not come along
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_read_seam_reruns_the_adr_061_demotion_direction(db):
    """The consequence the ruling names explicitly: a vault that SHRANK since the
    analysis is now caught before generation. `refresh_ledger_against_vault` is
    skip-only and can never do this — it is `assert_claimable_backed`'s direction, and
    #592 deliberately left it on the write paths."""
    from applire.services.cv import _latest_keyword_ledger

    job_id, _pid, _cid, gap_id = await _seed(db, [dict(_UNBACKED_CLAIM)])
    returned = await _latest_keyword_ledger(db, job_id, profile_json=_profile())
    assert _status(returned, "Rust") != "direct", (
        "a claimable row with no vault evidence must not survive a read seam"
    )
    row = await _row(db, gap_id)
    assert _status(row.keyword_ledger, "Rust") != "direct"


@pytest.mark.asyncio
async def test_the_read_seam_does_not_apply_the_monotonic_up_clamp(db):
    """E037 PQ #3's clamp ("never let a re-evaluation lower the headline number") exists
    on `/gaps/refresh`, where adding evidence can only raise the score. This seam can
    REMOVE evidence, and clamping here would show the candidate a number the document
    does not support — the ruling names the moving score as the price it accepts."""
    from applire.services.cv import _latest_keyword_ledger

    job_id, _pid, _cid, gap_id = await _seed(db, [dict(_UNBACKED_CLAIM)])
    before = (await _row(db, gap_id)).match_score
    await _latest_keyword_ledger(db, job_id, profile_json=_profile())
    row = await _row(db, gap_id)
    assert row.match_score < before, (
        f"the score must be allowed to fall ({before} -> {row.match_score})"
    )


@pytest.mark.asyncio
async def test_a_persist_failure_still_delivers_the_corrected_ledger(db):
    """ADR-021's standing contract, applied to a new write: the document must reflect
    the current vault whether or not the row could be updated. A read seam that starts
    raising is a new way to fail a generation."""
    from applire.services.keyword_ledger import refresh_persist_and_rescore

    class _Boom:
        keyword_ledger = [dict(_STALE_GAP)]

        def __setattr__(self, name, value):
            raise RuntimeError("simulated persist failure")

    db_stub = MagicMock()
    db_stub.flush = AsyncMock(side_effect=RuntimeError("no"))
    ledger = await refresh_persist_and_rescore(
        _Boom(), _profile(), db_stub, seam="test"
    )
    assert _status(ledger, "Kubernetes") == "direct"


@pytest.mark.asyncio
async def test_an_unchanged_ledger_writes_nothing(db):
    """No change, no write: a read of a current ledger must not touch the row, or every
    ATS-report render would churn the Gaps screen's `updated_at`."""
    from applire.services.cv import _latest_keyword_ledger

    fresh = dict(_STALE_GAP)
    fresh.update(status="direct", claimable=True,
                 evidence="Betrieb der Services auf Kubernetes verantwortet.")
    job_id, _pid, _cid, gap_id = await _seed(db, [fresh])
    before = (await _row(db, gap_id)).match_score
    await _latest_keyword_ledger(db, job_id, profile_json=_profile())
    row = await _row(db, gap_id)
    assert row.match_score == before
    assert _status(row.keyword_ledger, "Kubernetes") == "direct"
