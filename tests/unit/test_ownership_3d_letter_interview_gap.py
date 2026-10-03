# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry W2-3d — letter, interview and gap services are owner-scoped
(ADR-092 cl. 5, 6, 14; S-17; US333 letter half; System-FMEA SF-OWN.7).

Two users A and B, ONE shared posting (``job_analyses`` has no owner), each with
their own application link, profile, flow, gap analysis and interview. B's rows
are always the NEWER ones, so every "latest row for the job" lookup that forgot
its owner predicate reads B's row while acting for A — each seam test below
fails on exactly that omission (mutation-checked, see the 3d report).

``get_profile_for_user`` is 3b's (W2) to make owner-keyed; until that lands on
the leading branch the tests install a faithful double with the ADR-092 cl. 2
semantics (the owner's live row) — at integration it is a no-op.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.application import Application
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.models.user import User
from applire.models.user_settings import UserSettings
from tests.support.profile_factory import make_master_profile

pytestmark = pytest.mark.no_owner_context

_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


async def _owner_keyed_profile(db, user_id=None):
    """ADR-092 cl. 2 read path (3b fills the real one): the owner's live row."""
    return (
        await db.execute(
            select(MasterProfile).where(
                MasterProfile.user_id == user_id, MasterProfile.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()


@pytest.fixture(autouse=True)
def _profile_read_path(monkeypatch):
    monkeypatch.setattr(
        "applire.services.profile.get_profile_for_user", _owner_keyed_profile
    )


def _cluster(cid: str, label: str) -> dict:
    return {
        "id": cid, "label": label, "category": "C", "gaps": [label],
        "jd_skills": [label], "jd_context": "",
    }


def _gap(job_id, profile, *, cluster_id, created_at, fingerprint=None, ledger=None):
    return GapAnalysis(
        job_analysis_id=job_id, profile_id=profile.id, user_id=profile.user_id,
        match_score=0.5, critical_gaps=[], minor_gaps=[], strengths=[],
        keyword_gaps=[], category_a=[], category_b=[], category_c=[cluster_id],
        gap_clusters=[_cluster(cluster_id, cluster_id)],
        keyword_ledger=ledger, input_fingerprint=fingerprint,
        created_at=created_at,
    )


def _interview(job_id, profile, *, created_at, status="active", micro=False):
    return InterviewSession(
        job_analysis_id=job_id, profile_id=profile.id, user_id=profile.user_id,
        mode="targeted", status=status, questions_asked=0, hard_ceiling=12,
        state={
            "mode": "targeted", "job_id": str(job_id), "profile_id": str(profile.id),
            "critical_gaps": ["c"], "gap_categories": {}, "gap_clusters_by_id": {},
            "current_gap_index": 0, "current_question": "Q?", "messages": [],
            "questions_asked": 0, "micro_session": micro,
        },
        created_at=created_at, updated_at=created_at,
        expires_at=created_at + timedelta(days=30),
    )


@pytest_asyncio.fixture
async def world():
    """A and B on one shared posting; B's rows are newer than A's."""
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with factory() as db:
            a = User(id=uuid.uuid4(), email="a-3d@example.org", role="user")
            b = User(id=uuid.uuid4(), email="b-3d@example.org", role="user")
            db.add_all([a, b])
            job = JobAnalysis(
                raw_text_hash=uuid.uuid4().hex,
                raw_text="Senior Python Engineer. Kubernetes, GCP.",
                role_title="Posting Title", company_name="Posting GmbH",
                required_skills=["Python", "Kubernetes"], nice_to_have_skills=[],
                keywords=["Python"], seniority_level="Senior",
                company_culture_signals=[], language_requirement="English",
            )
            db.add(job)
            await db.flush()
            pa = make_master_profile(user_id=a.id, profile_json={"personal_info": {"name": "Anna A"}})
            pb = make_master_profile(user_id=b.id, profile_json={"personal_info": {"name": "Bernd B"}})
            db.add_all([pa, pb])
            await db.flush()
            app_a = Application(user_id=a.id, job_analysis_id=job.id,
                                role_title="A's Title", company_name="A's Company")
            app_b = Application(user_id=b.id, job_analysis_id=job.id,
                                role_title="B's Title", company_name="B's Company")
            db.add_all([app_a, app_b])
            ga = _gap(job.id, pa, cluster_id="cluster-a", created_at=_T0)
            gb = _gap(job.id, pb, cluster_id="cluster-b", created_at=_T0 + timedelta(hours=1))
            db.add_all([ga, gb])
            await db.flush()
            ia = _interview(job.id, pa, created_at=_T0)
            ib = _interview(job.id, pb, created_at=_T0 + timedelta(hours=1))
            db.add_all([ia, ib])
            await db.flush()
            fa = FlowSession(user_id=a.id, job_id=job.id, application_id=app_a.id)
            fb = FlowSession(user_id=b.id, job_id=job.id, application_id=app_b.id)
            db.add_all([fa, fb])
            await db.flush()
            cla = GeneratedCoverLetter(job_analysis_id=job.id, profile_id=pa.id, user_id=a.id,
                                       letter_data={}, status="ready")
            clb = GeneratedCoverLetter(job_analysis_id=job.id, profile_id=pb.id, user_id=b.id,
                                       letter_data={}, status="ready")
            db.add_all([cla, clb])
            await db.flush()
            fa.generated_cover_letter_id, fb.generated_cover_letter_id = cla.id, clb.id
            db.add_all([
                UserSettings(user_id=a.id, ui_language="de"),
                UserSettings(user_id=b.id, ui_language="en"),
            ])
            await db.commit()
    w = SimpleNamespace(
        factory=factory, a=a, b=b, job=job, pa=pa, pb=pb, ga=ga, gb=gb, ia=ia, ib=ib,
        cla=cla, clb=clb, app_a=app_a,
    )
    yield w
    await eng.dispose()


async def _as(w, user, fn):
    """Run ``fn(db)`` the way a request does: the owner context names the user."""
    with ownership.owner_context(user.id):
        async with w.factory() as db:
            return await fn(db)


# ---------------------------------------------------------------------------
# The headline: two users on one posting each get their own interview + gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_users_on_one_shared_posting_each_get_their_own_interview_session(world):
    """SF-OWN.7: A's interview start on the shared posting returns A's session
    (resume), never B's newer active one — and B's returns B's."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    req = SessionCreateRequest(job_id=world.job.id, mode="targeted")
    ra = await _as(world, world.a, lambda db: create_session(req, db, MagicMock(), user_id=world.a.id))
    rb = await _as(world, world.b, lambda db: create_session(req, db, MagicMock(), user_id=world.b.id))
    assert ra.session_id == world.ia.id
    assert rb.session_id == world.ib.id


@pytest.mark.asyncio
async def test_a_fresh_session_on_a_shared_posting_is_created_for_its_owner(world):
    """Both users start a NEW interview on the one posting: two active sessions
    coexist (the unique is per (user, job) since 0075), each owned by its user
    and pointing at that user's profile and gap analysis."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    async def _retire(db):
        for s in (await db.execute(select(InterviewSession))).scalars():
            s.status = "complete"
        await db.commit()

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            await _retire(db)

    req = SessionCreateRequest(job_id=world.job.id, mode="targeted")
    gen = AsyncMock(return_value={"question": "Tell me about it.", "choices": None})
    with patch("applire.services.session.question_generator_with_profile", new=gen):
        ra = await _as(world, world.a, lambda db: create_session(req, db, MagicMock(), user_id=world.a.id))
        rb = await _as(world, world.b, lambda db: create_session(req, db, MagicMock(), user_id=world.b.id))
    assert ra.session_id != rb.session_id
    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            sa = await db.get(InterviewSession, ra.session_id)
            sb = await db.get(InterviewSession, rb.session_id)
    assert (sa.user_id, sa.profile_id, sa.gap_analysis_id, sa.status) == (
        world.a.id, world.pa.id, world.ga.id, "active")
    assert (sb.user_id, sb.profile_id, sb.gap_analysis_id, sb.status) == (
        world.b.id, world.pb.id, world.gb.id, "active")


@pytest.mark.asyncio
async def test_two_users_on_one_posting_each_get_their_own_gap_analysis(world):
    """``analyze_gaps`` idempotency: B holds a row with A's exact fingerprint
    (same posting, identical gap inputs). A must NOT reuse B's row — the run
    goes on to the LLM (here: a provider that refuses) instead of returning it."""
    from applire.services.gap import _input_fingerprint, analyze_gaps

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            gb = await db.get(GapAnalysis, world.gb.id)
            pa = await db.get(MasterProfile, world.pa.id)
            job = await db.get(JobAnalysis, world.job.id)
            gb.input_fingerprint = _input_fingerprint(job, pa)
            await db.commit()

    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=RuntimeError("LLM reached"))
    provider.acomplete = AsyncMock(side_effect=RuntimeError("LLM reached"))
    with patch("applire.services.flow.orchestrator.repoint_flow_gap_analysis", new=AsyncMock()):
        with pytest.raises(Exception) as exc:
            await _as(world, world.a, lambda db: analyze_gaps(world.job.id, db, provider, user_id=world.a.id))
    assert "LLM reached" in str(exc.value)
    # And B reuses its own row (no LLM) when its own fingerprint matches.
    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            gb = await db.get(GapAnalysis, world.gb.id)
            pb = await db.get(MasterProfile, world.pb.id)
            gb.input_fingerprint = _input_fingerprint(await db.get(JobAnalysis, world.job.id), pb)
            await db.commit()
    with patch("applire.services.flow.orchestrator.repoint_flow_gap_analysis", new=AsyncMock()):
        rb = await _as(world, world.b, lambda db: analyze_gaps(world.job.id, db, provider, user_id=world.b.id))
    assert rb.id == world.gb.id


@pytest.mark.asyncio
async def test_a_user_without_a_link_to_the_posting_cannot_start_an_interview_on_it(world):
    """ADR-092 cl. 5(c) + S-10: a third user who never analysed the posting gets
    the missing-job answer, not an interview on someone else's posting."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    c = User(id=uuid.uuid4(), email="c-3d@example.org", role="user")
    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            db.add(c)
            await db.commit()
    req = SessionCreateRequest(job_id=world.job.id, mode="targeted")
    with pytest.raises(LookupError, match="not found"):
        await _as(world, c, lambda db: create_session(req, db, MagicMock(), user_id=c.id))


# ---------------------------------------------------------------------------
# Seam tests — one per changed owner-keyed lookup (B's row is always newer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_session_gap_cluster_ids_reads_the_owners_newest_analysis(world):
    from applire.services.session import gap_cluster_ids

    ids = await _as(world, world.a, lambda db: gap_cluster_ids(world.job.id, db, user_id=world.a.id))
    assert ids == ["cluster-a"]


@pytest.mark.asyncio
async def test_seam_session_cluster_coverage_for_reads_the_owners_analysis(world):
    from applire.services.session import cluster_coverage_for

    cov_b = await _as(world, world.a, lambda db: cluster_coverage_for(world.job.id, "cluster-b", db, user_id=world.a.id))
    assert cov_b is None  # B's cluster is not in A's analysis


@pytest.mark.asyncio
async def test_seam_session_active_full_interview_exists_is_per_owner(world):
    from applire.services.session import active_full_interview_exists

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            (await db.get(InterviewSession, world.ia.id)).status = "complete"
            await db.commit()
    # B still has an active full interview on the posting; A has none.
    assert await _as(world, world.a, lambda db: active_full_interview_exists(world.job.id, db, user_id=world.a.id)) is False
    assert await _as(world, world.b, lambda db: active_full_interview_exists(world.job.id, db, user_id=world.b.id)) is True


@pytest.mark.asyncio
async def test_seam_session_left_open_ids_read_the_owners_analysis(world):
    from applire.services.session import _left_open_cluster_ids

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            gb = await db.get(GapAnalysis, world.gb.id)
            gb.gap_clusters = [{**_cluster("cluster-b", "cluster-b"), "outcome": {"left_open": True}}]
            await db.commit()
    ids_a = await _as(world, world.a, lambda db: _left_open_cluster_ids(world.job.id, db, user_id=world.a.id))
    assert ids_a == set()


@pytest.mark.asyncio
async def test_seam_session_get_session_state_and_send_message_refuse_a_foreign_session(world):
    from applire.services.session import get_session_state, send_message

    with pytest.raises(LookupError, match="not found"):
        await _as(world, world.a, lambda db: get_session_state(world.ib.id, db, user_id=world.a.id))
    with pytest.raises(LookupError, match="not found"):
        await _as(world, world.a, lambda db: send_message(world.ib.id, "hi", db, MagicMock(), user_id=world.a.id))


@pytest.mark.asyncio
async def test_seam_session_language_reads_the_owners_settings(world):
    from applire.services.session import get_conversation_language, get_ui_language

    assert await _as(world, world.a, lambda db: get_ui_language(db, user_id=world.a.id)) == "de"
    assert await _as(world, world.b, lambda db: get_ui_language(db, user_id=world.b.id)) == "en"
    assert await _as(world, world.a, lambda db: get_conversation_language(db, world.job.id, user_id=world.a.id)) == "de"


@pytest.mark.asyncio
async def test_seam_gap_set_cluster_left_open_writes_the_owners_row(world):
    from applire.services.gap import set_cluster_left_open

    with pytest.raises(LookupError):  # cluster-b lives only in B's (newer) row
        await _as(world, world.a, lambda db: set_cluster_left_open(world.job.id, "cluster-b", True, db, user_id=world.a.id))
    row = await _as(world, world.a, lambda db: set_cluster_left_open(world.job.id, "cluster-a", True, db, user_id=world.a.id))
    assert row.id == world.ga.id


@pytest.mark.asyncio
async def test_seam_gap_downgrade_keyword_liability_reads_the_owners_row(world):
    from applire.services.gap import downgrade_keyword_liability

    out = await _as(world, world.a, lambda db: downgrade_keyword_liability(world.job.id, "Nothing", db, user_id=world.a.id))
    assert out.id == world.ga.id


@pytest.mark.asyncio
async def test_seam_gap_analyze_gaps_for_session_refuses_a_foreign_session(world):
    from applire.services.gap import analyze_gaps_for_session

    with pytest.raises(LookupError, match="not found"):
        await _as(world, world.a, lambda db: analyze_gaps_for_session(world.ib.id, db, MagicMock(), user_id=world.a.id))


@pytest.mark.asyncio
async def test_seam_letter_ledger_reads_the_owners_newest_analysis(world):
    from applire.services.cover_letter import _latest_keyword_ledger

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            (await db.get(GapAnalysis, world.ga.id)).keyword_ledger = [{"term": "A-row"}]
            (await db.get(GapAnalysis, world.gb.id)).keyword_ledger = [{"term": "B-row"}]
            await db.commit()
    seen = {}

    async def _spy(gap, profile_json, db, *, seam):
        seen["gap"] = gap.id
        return gap.keyword_ledger

    with patch("applire.services.keyword_ledger.refresh_persist_and_rescore", new=_spy):
        await _as(world, world.a, lambda db: _latest_keyword_ledger(db, world.job.id, user_id=world.a.id))
    assert seen["gap"] == world.ga.id


@pytest.mark.asyncio
async def test_seam_letter_by_job_returns_the_owners_letter(world):
    from applire.services.cover_letter import get_cover_letter_by_job

    out = await _as(world, world.a, lambda db: get_cover_letter_by_job(world.job.id, db, "http://x", user_id=world.a.id))
    assert out.cover_letter_id == world.cla.id


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", [
    "get_cover_letter_status", "get_cover_letter_ats_report",
    "get_cover_letter_truthfulness_report", "get_cover_letter_critic_report",
    "get_cover_letter_html", "get_cover_letter_docx", "get_cover_letter_pdf_filename",
    "get_cover_letter_docx_filename", "set_cover_letter_signature_override",
    "patch_cover_letter_section",
])
async def test_seam_letter_by_id_reads_refuse_a_foreign_letter(world, fn):
    import applire.services.cover_letter as svc

    args = {
        "get_cover_letter_status": lambda db: svc.get_cover_letter_status(world.clb.id, db, "http://x", user_id=world.a.id),
        "set_cover_letter_signature_override": lambda db: svc.set_cover_letter_signature_override(world.clb.id, True, db, user_id=world.a.id),
        "patch_cover_letter_section": lambda db: svc.patch_cover_letter_section(world.clb.id, "body", "x", db, user_id=world.a.id),
    }.get(fn, lambda db: getattr(svc, fn)(world.clb.id, db, user_id=world.a.id))
    with pytest.raises(LookupError, match="not found"):
        await _as(world, world.a, args)


@pytest.mark.asyncio
async def test_seam_letter_generate_uses_the_owners_flow_on_a_shared_posting(world):
    """Two flows on one posting: before ADR-092 the job-keyed flow lookup raised
    MultipleResultsFound; now A's letter hangs on A's flow, with A's owner."""
    from applire.schemas.cover_letter import CoverLetterGenerateRequest
    from applire.services.cover_letter import generate_cover_letter

    bg = MagicMock()
    out = await _as(world, world.a, lambda db: generate_cover_letter(
        CoverLetterGenerateRequest(job_id=world.job.id), db, MagicMock(), bg, user_id=world.a.id))
    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            cl = await db.get(GeneratedCoverLetter, out.cover_letter_id)
            flows = (await db.execute(select(FlowSession))).scalars().all()
    assert (cl.user_id, cl.profile_id) == (world.a.id, world.pa.id)
    by_user = {f.user_id: f.generated_cover_letter_id for f in flows}
    assert by_user[world.a.id] == cl.id and by_user[world.b.id] == world.clb.id
    # The background task names its owner (ADR-092 cl. 14).
    assert bg.add_task.call_args.kwargs["user_id"] == world.a.id


@pytest.mark.asyncio
async def test_seam_letter_html_subject_uses_the_owners_posting_label(world):
    """ADR-092 cl. 5(f): the subject line names A's own role title override."""
    from applire.services.cover_letter import get_cover_letter_html

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            cl = await db.get(GeneratedCoverLetter, world.cla.id)
            cl.letter_data = {"header": {}, "recipient": {}, "body": {"paragraphs": ["x"]}, "signature": {}}
            cl.document_language = "en"
            await db.commit()
    with patch("applire.services.signature.resolve_signature_data_uri", new=AsyncMock(return_value=None)):
        html = await _as(world, world.a, lambda db: get_cover_letter_html(world.cla.id, db, user_id=world.a.id))
    assert "A&#39;s Title" in html or "A's Title" in html
    assert "Posting Title" not in html and "B's Title" not in html


@pytest.mark.asyncio
async def test_seam_letter_filename_uses_the_owners_posting_labels(world):
    from applire.services.cover_letter import get_cover_letter_pdf_filename

    name = await _as(world, world.a, lambda db: get_cover_letter_pdf_filename(world.cla.id, db, user_id=world.a.id))
    assert "A" in name and "Company" in name and "Posting" not in name


@pytest.mark.asyncio
async def test_background_task_entry_sets_the_owner_context(world):
    """ADR-092 cl. 14: the render task acts for the user it was handed."""
    import applire.services.cover_letter as svc

    seen = {}

    async def _body(cl_id, cv_id, job_id, application_id, *, user_id):
        seen["ctx"] = ownership.current_owner().user_id
        seen["arg"] = user_id

    with patch.object(svc, "_render_cover_letter_body", new=_body):
        await svc._render_cover_letter_background(world.cla.id, None, world.job.id, user_id=world.a.id)
    assert seen == {"ctx": world.a.id, "arg": world.a.id}


@pytest.mark.asyncio
async def test_no_user_and_no_owner_context_refuses(world):
    """Ruling 3d-1: a per-user service never runs for nobody."""
    from applire.services.session import get_ui_language

    async with world.factory() as db:
        with pytest.raises(ownership.OwnerContextMissing):
            await get_ui_language(db)
        with ownership.unscoped("tooling"):
            with pytest.raises(ownership.OwnerContextMissing):
                await get_ui_language(db)


@pytest.mark.asyncio
async def test_colour_default_is_the_document_owners(world):
    from applire.models.color_profile import ColorProfile
    from applire.services.color_detection import resolve_color_context

    with ownership.unscoped("tooling"):
        async with world.factory() as db:
            cp = ColorProfile(seed_primary="#123456", derived={"--cv-accent": "#123456"}, source="user")
            db.add(cp)
            await db.flush()
            sa = (await db.execute(select(UserSettings).where(UserSettings.user_id == world.a.id))).scalar_one()
            sa.default_color_profile_id = cp.id
            await db.commit()
    rec_a = SimpleNamespace(color_profile_id=None, job_analysis_id=world.job.id, user_id=world.a.id)
    rec_b = SimpleNamespace(color_profile_id=None, job_analysis_id=world.job.id, user_id=world.b.id)
    ctx_a = await _as(world, world.a, lambda db: resolve_color_context(rec_a, db))
    ctx_b = await _as(world, world.b, lambda db: resolve_color_context(rec_b, db))
    assert ctx_a.primary == "#123456"
    assert ctx_b.primary != "#123456"


# ---------------------------------------------------------------------------
# RD-7: both colour fetches go through safe_get; a refusal is a miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch,expected_url", [
    ("_fetch_favicon_color", "https://www.google.com/s2/favicons?domain=intranet.local&sz=128"),
    ("_fetch_meta_color", "https://intranet.local"),
])
async def test_colour_fetches_go_through_safe_get_and_a_refusal_is_a_miss(fetch, expected_url):
    import applire.services.color_detection as cd
    from applire.services import safe_fetch

    calls = []

    async def _refuse(url, **kw):
        calls.append(url)
        raise safe_fetch.UnsafeFetchRefused(f"refused {url}")

    with patch.object(safe_fetch, "safe_get", new=_refuse), \
            patch("httpx.AsyncClient", side_effect=AssertionError("raw httpx used")):
        assert await getattr(cd, fetch)("intranet.local") is None
    assert calls == [expected_url]


@pytest.mark.asyncio
async def test_meta_colour_fetch_reads_the_safe_get_response():
    import applire.services.color_detection as cd
    from applire.services import safe_fetch

    resp = SimpleNamespace(status_code=200, text='<meta name="theme-color" content="#ab12cd">')
    with patch.object(safe_fetch, "safe_get", new=AsyncMock(return_value=resp)) as sg:
        assert await cd._fetch_meta_color("example.org") == "#ab12cd"
    assert sg.call_args.kwargs["headers"]["User-Agent"].startswith("Applire/")
