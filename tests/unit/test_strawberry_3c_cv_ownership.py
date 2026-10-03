# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry W2 / package 3c — the CV side reads and writes for its owner
(ADR-092 cl. 1, 5c, 5f, 6, 14; US333 CV half; System-FMEA SF-OWN.1/.3).

One **seam test per call site** whose shared helper changed:

* ``_latest_keyword_ledger`` (owner-keyed GapAnalysis read) — generation's
  inline gap read, the ATS audit (PDF + .docx blocks);
* ``non_claim_names_for_job(job, application)`` — the PDF audit, the .docx
  audit, the review take-out's ``protected_names``;
* ``owned_cv`` — every CV door (the isolation suite covers the REST routes; the
  service-level twins here cover the doors that take no path id);
* the background tasks (``_render_cv_background``, ``_update_ats_report_by_id``)
  run under their user's owner context;
* the filename builders and the critic/writer title read the user's labels.

The world: users A and B, ONE shared posting both analysed (an ``applications``
row each, RD-2), each with their own profile, gap analysis and CV.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.application import Application
from applire.models.cv import GeneratedCV
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.user import User

pytestmark = pytest.mark.no_owner_context


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    w: dict = {"factory": factory}
    with ownership.unscoped("tooling"):
        async with factory() as s:
            job = JobAnalysis(
                raw_text_hash=uuid.uuid4().hex, raw_text="Platform Engineer, Python, Kubernetes.",
                role_title="Platform Engineer (m/w/d)", company_name="Shared Posting GmbH",
                seniority_level="mid", language_requirement="English",
                keywords=["Python", "Kubernetes"],
            )
            s.add(job)
            await s.flush()
            w["job"] = job.id
            for name in ("a", "b"):
                u = User(id=uuid.uuid4(), email=f"3c-{name}@example.org", role="user")
                s.add(u)
                await s.flush()
                with authorized_profile_write():
                    p = MasterProfile(user_id=u.id, profile_json={
                        "personal_info": {"name": f"Person {name.upper()}"},
                        "skills": [{"name": f"Skill{name.upper()}"}],
                    })
                s.add(p)
                await s.flush()
                app = Application(
                    user_id=u.id, job_analysis_id=job.id,
                    role_title=f"Title of {name.upper()}", company_name=f"Employer {name.upper()}",
                )
                s.add(app)
                gap = GapAnalysis(
                    job_analysis_id=job.id, profile_id=p.id, user_id=u.id, match_score=0.5,
                    keyword_ledger=[{"concept": f"ledger-{name}", "status": "gap"}],
                )
                s.add(gap)
                cv = GeneratedCV(
                    job_analysis_id=job.id, profile_id=p.id, user_id=u.id, status="ready",
                    tailored_data={"contact": {"name": f"Person {name.upper()}"}},
                    content_snapshot={"introduction": "Intro.", "positions": [], "skills": []},
                )
                s.add(cv)
                await s.flush()
                w[name] = {"user": u.id, "profile": p.id, "app": app.id, "gap": gap.id, "cv": cv.id}
            await s.commit()
    yield w
    await eng.dispose()


@contextlib.asynccontextmanager
async def _as(world, who):
    """A session acting as ``who`` (owner context set, like a request)."""
    with ownership.owner_context(world[who]["user"]):
        async with world["factory"]() as s:
            yield s


# ── owner resolution (ruling 3d-1) ───────────────────────────────────────────


def test_resolve_owner_prefers_explicit_and_counts_the_fallback():
    from applire.services.cv_owner import OWNER_FALLBACK_STATS, resolve_owner

    a, b = uuid.uuid4(), uuid.uuid4()
    OWNER_FALLBACK_STATS.pop("t.site", None)
    with ownership.owner_context(a):
        assert resolve_owner(b, site="t.site") == b
        assert OWNER_FALLBACK_STATS["t.site"] == 0
        assert resolve_owner(None, site="t.site") == a
    assert OWNER_FALLBACK_STATS["t.site"] == 1


def test_resolve_owner_refuses_without_a_user():
    from applire.services.cv_owner import resolve_owner

    with pytest.raises(ownership.OwnerContextMissing):
        resolve_owner(None, site="t.none")
    with ownership.unscoped("tooling"), pytest.raises(ownership.OwnerContextMissing):
        resolve_owner(None, site="t.unscoped")


# ── owned CV reads (cl. 6) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_foreign_cv_reads_exactly_like_a_missing_one(world):
    from applire.services import cv as cv_svc

    async with _as(world, "b") as db:
        with pytest.raises(LookupError) as foreign:
            await cv_svc.get_cv_ats_report(world["a"]["cv"], db, user_id=world["b"]["user"])
        missing_id = uuid.uuid4()
        with pytest.raises(LookupError) as missing:
            await cv_svc.get_cv_ats_report(missing_id, db, user_id=world["b"]["user"])
    assert str(foreign.value).replace(str(world["a"]["cv"]), "X") == str(missing.value).replace(
        str(missing_id), "X"
    )


@pytest.mark.asyncio
async def test_list_cvs_for_job_returns_only_the_callers_cvs(world):
    from applire.services.cv import list_cvs_for_job

    async with _as(world, "b") as db:
        out = await list_cvs_for_job(world["job"], db, "http://x", user_id=world["b"]["user"])
    assert [r.cv_id for r in out] == [world["b"]["cv"]]


@pytest.mark.asyncio
async def test_profile_diff_refuses_a_foreign_cv(world):
    from applire.services.cv_diff import get_cv_profile_diff

    async with _as(world, "b") as db:
        with pytest.raises(ValueError):
            await get_cv_profile_diff(world["a"]["cv"], db, user_id=world["b"]["user"])
        own = await get_cv_profile_diff(world["b"]["cv"], db, user_id=world["b"]["user"])
    assert own is not None


@pytest.mark.asyncio
async def test_cv_color_refuses_a_foreign_cv(world):
    from applire.routers.cv_color import apply_cv_color

    async with _as(world, "b") as db:
        with pytest.raises(LookupError):
            await apply_cv_color(world["a"]["cv"], "#12233E", db, user_id=world["b"]["user"])


# ── generation entry (cl. 1, 5c, 14) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_cv_needs_the_users_link_and_names_its_owner(world, monkeypatch):
    from applire.services import cv as cv_svc

    # A posting nobody of B's analysed: no applications row for B → not found.
    async with _as(world, "b") as db:
        other = JobAnalysis(raw_text_hash=uuid.uuid4().hex, raw_text="x", role_title="Other",
                            seniority_level="mid", language_requirement="English")
        db.add(other)
        await db.commit()
        with pytest.raises(LookupError):
            await cv_svc.generate_cv(other.id, db, provider=None, background_tasks=_Tasks(),
                                     user_id=world["b"]["user"])

    tasks = _Tasks()
    async with _as(world, "b") as db:
        resp = await cv_svc.generate_cv(world["job"], db, provider=None, background_tasks=tasks,
                                        user_id=world["b"]["user"])
        row = await db.get(GeneratedCV, resp.cv_id)
    assert row.user_id == world["b"]["user"]
    # the background task receives the user explicitly (cl. 14)
    (fn, args, kwargs), = tasks.calls
    assert fn is cv_svc._render_cv_background
    assert kwargs["user_id"] == world["b"]["user"]


class _Tasks:
    def __init__(self):
        self.calls = []

    def add_task(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


# ── background tasks run under their user (cl. 14) ───────────────────────────


@pytest.mark.asyncio
async def test_render_background_reads_the_owners_gap_analysis_not_the_other_users(world, monkeypatch):
    """Seam: generation's inline GapAnalysis read is owner-keyed. A's analysis of
    the SAME shared posting must never steer B's CV (A's is the newer row)."""
    from applire.services import cv as cv_svc
    from applire.services import keyword_ledger

    seen = {}

    async def _spy(gap, profile_json, db, **_k):
        seen["gap_user"] = gap.user_id
        seen["ctx"] = ownership.current_owner()
        raise RuntimeError("stop after the gap read")

    monkeypatch.setattr(keyword_ledger, "refresh_persist_and_rescore", _spy)
    monkeypatch.setattr(cv_svc, "AsyncSessionLocal", world["factory"])
    with ownership.unscoped("tooling"):  # A's analysis becomes the newest row
        async with world["factory"]() as s:
            g = await s.get(GapAnalysis, world["a"]["gap"])
            from datetime import datetime, timedelta, timezone
            g.created_at = datetime.now(timezone.utc) + timedelta(days=1)
            await s.commit()
    # no ambient context: the task must set its own
    await cv_svc._render_cv_background(
        world["b"]["cv"], world["job"], world["b"]["profile"], "classic_german",
        user_id=world["b"]["user"],
    )
    assert seen["gap_user"] == world["b"]["user"]
    assert seen["ctx"].user_id == world["b"]["user"]


@pytest.mark.asyncio
async def test_render_background_refuses_a_cv_of_another_user(world, monkeypatch):
    from applire.services import cv as cv_svc

    monkeypatch.setattr(cv_svc, "AsyncSessionLocal", world["factory"])
    await cv_svc._render_cv_background(
        world["a"]["cv"], world["job"], world["a"]["profile"], "classic_german",
        user_id=world["b"]["user"],
    )
    with ownership.unscoped("tooling"):
        async with world["factory"]() as s:
            assert (await s.get(GeneratedCV, world["a"]["cv"])).status == "ready"  # untouched


@pytest.mark.asyncio
async def test_update_ats_report_by_id_sets_the_owner_context(world, monkeypatch):
    from applire.services import cv as cv_svc

    seen = {}

    async def _spy(record, db, **_k):
        seen["record_user"] = record.user_id
        seen["ctx"] = ownership.current_owner()

    monkeypatch.setattr(cv_svc, "_update_ats_report", _spy)
    monkeypatch.setattr(cv_svc, "AsyncSessionLocal", world["factory"])
    await cv_svc._update_ats_report_by_id(world["b"]["cv"], user_id=world["b"]["user"])
    assert seen == {"record_user": world["b"]["user"], "ctx": seen["ctx"]}
    assert seen["ctx"].user_id == world["b"]["user"]
    seen.clear()
    await cv_svc._update_ats_report_by_id(world["a"]["cv"], user_id=world["b"]["user"])
    assert seen == {}  # a foreign id is never audited


# ── _latest_keyword_ledger seams (ATS audit, PDF + .docx) ────────────────────


@pytest.mark.asyncio
async def test_latest_keyword_ledger_is_owner_keyed(world, monkeypatch):
    from applire.services import cv as cv_svc
    from applire.services import keyword_ledger

    async def _passthrough(gap, profile_json, db, **_k):
        return gap.keyword_ledger

    monkeypatch.setattr(keyword_ledger, "refresh_persist_and_rescore", _passthrough)
    from datetime import datetime, timedelta, timezone

    with ownership.unscoped("tooling"):  # A's analysis is the newest of the posting
        async with world["factory"]() as s:
            g = await s.get(GapAnalysis, world["a"]["gap"])
            g.created_at = datetime.now(timezone.utc) + timedelta(days=1)
            await s.commit()
    async with _as(world, "b") as db:
        ledger = await cv_svc._latest_keyword_ledger(db, world["job"], user_id=world["b"]["user"])
    assert ledger == [{"concept": "ledger-b", "status": "gap"}]


@pytest.mark.asyncio
async def test_ats_audit_passes_the_rows_owner_and_the_owners_labels(world, monkeypatch):
    """Seams: ``_update_ats_report`` → ``_latest_keyword_ledger`` (PDF block and
    .docx block) with the ROW's owner, and → ``non_claim_names_for_job`` with the
    owner's application (both blocks)."""
    from applire.services import ats_audit
    from applire.services import cv as cv_svc

    ledger_users, label_apps = [], []

    async def _ledger(db, job_id, *, profile_json=None, user_id=None):
        ledger_users.append(user_id)
        return []

    real_nc = ats_audit.non_claim_names_for_job

    def _nc(job, application=None):
        label_apps.append(getattr(application, "id", None))
        return real_nc(job, application)

    async def _measured_html(*_a, **_k):
        return "<html></html>"

    async def _pdf(*_a, **_k):
        raise RuntimeError("no chromium in this seam test")

    monkeypatch.setattr(cv_svc, "_latest_keyword_ledger", _ledger)
    monkeypatch.setattr(ats_audit, "non_claim_names_for_job", _nc)
    monkeypatch.setattr(cv_svc, "_html_to_pdf", _pdf)
    from applire.services.cv import MeasuredRender  # noqa: F401 — shape check below

    async with _as(world, "b") as db:
        record = await db.get(GeneratedCV, world["b"]["cv"])
        measured = MeasuredRender(text="Intro.", page_count=1, condensation_exhausted=False,
                                  target=2, region="DE")
        await cv_svc._update_ats_report(record, db, measured=measured, commit=False)
    assert ledger_users and set(ledger_users) == {world["b"]["user"]}
    assert world["b"]["app"] in label_apps


# ── non_claim_names_for_job (shared helper) ──────────────────────────────────


def test_non_claim_names_mask_the_users_labels_and_the_postings():
    from types import SimpleNamespace

    from applire.services.ats_audit import non_claim_names_for_job

    job = SimpleNamespace(role_title="Platform Engineer (m/w/d)", company_name="Shared Posting GmbH")
    app = SimpleNamespace(role_title="Senior Platform Engineer", company_name="Acme Cloud AG")
    plain = non_claim_names_for_job(job)
    both = non_claim_names_for_job(job, app)
    assert set(plain.titles) < set(both.titles)
    assert "senior platform engineer" in both.titles
    assert any("acme cloud" in e for e in both.employers)
    assert set(plain.employers) <= set(both.employers)
    assert non_claim_names_for_job(job, None) == plain


@pytest.mark.asyncio
async def test_take_out_protected_names_include_the_owners_label(world):
    """Seam: ``review_actions.protected_names`` → ``non_claim_names_for_job`` with
    the owner's application."""
    from applire.services import review_actions as ra

    async with _as(world, "b") as db:
        record = await db.get(GeneratedCV, world["b"]["cv"])
        names = await ra.protected_names("cv", record, db)
    assert "employer b" in names
    assert "employer a" not in names


# ── labels: filename builders, rewrite context (cl. 5f) ──────────────────────


@pytest.mark.asyncio
async def test_pdf_and_docx_filenames_use_the_users_own_labels(world):
    from applire.services.cv import get_docx_filename, get_pdf_filename

    async with _as(world, "b") as db:
        pdf = await get_pdf_filename(world["b"]["cv"], db, user_id=world["b"]["user"])
        docx = await get_docx_filename(world["b"]["cv"], db, user_id=world["b"]["user"])
    assert pdf == "Person-B_Employer-B_Title-of-B.pdf"
    assert docx == "Person-B_Employer-B_Title-of-B.docx"


# ── assist micro-sessions belong to their user ───────────────────────────────


@pytest.mark.asyncio
async def test_assist_answer_on_a_foreign_cv_is_not_found(world):
    from applire.services import cv_assist

    async with _as(world, "b") as db:
        with pytest.raises(LookupError):
            await cv_assist.submit_assist_answer(
                world["a"]["cv"], "introduction", "s", "Yes.", provider=None, db=db,
                user_id=world["b"]["user"],
            )


@pytest.mark.asyncio
async def test_assist_session_of_another_user_is_refused(world):
    from applire.services import cv_assist

    sid = str(uuid.uuid4())
    cv_assist._sessions[sid] = {
        "user_id": str(world["a"]["user"]), "cv_id": str(world["b"]["cv"]),
        "section_id": "introduction", "gap_id": "g", "section_label": "Intro",
        "section_content": "A's text", "question": "?",
    }
    try:
        async with _as(world, "b") as db:
            with pytest.raises(ValueError):
                await cv_assist.submit_assist_answer(
                    world["b"]["cv"], "introduction", sid, "Yes.", provider=None, db=db,
                    user_id=world["b"]["user"],
                )
    finally:
        cv_assist._sessions.pop(sid, None)


# ── catch-all services (3c): gap_coverage, cover_letter_pdf ──────────────────


@pytest.mark.asyncio
async def test_record_turn_outcome_writes_only_the_owners_gap_analysis(world):
    """A's analysis of the SAME posting carries the same cluster id; B's turn
    must land on B's row and never on A's (A's is the newer row)."""
    from datetime import datetime, timedelta, timezone

    from applire.services import gap_coverage

    cluster = {"id": "cluster-1", "label": "Cloud", "category": "C",
               "gaps": ["Kubernetes"], "jd_skills": ["Kubernetes"], "jd_context": ""}
    with ownership.unscoped("tooling"):
        async with world["factory"]() as s:
            for who, shift in (("a", 1), ("b", 0)):
                g = await s.get(GapAnalysis, world[who]["gap"])
                g.gap_clusters = [dict(cluster)]
                g.created_at = datetime.now(timezone.utc) + timedelta(days=shift)
            await s.commit()
    async with _as(world, "b") as db:
        await gap_coverage.record_turn_outcome(
            db, job_id=world["job"], fallback_gap_analysis_id=world["a"]["gap"],
            cluster_id="cluster-1", member_facts={}, session_id="s-b",
            user_id=world["b"]["user"],
        )
        await db.commit()
    with ownership.unscoped("tooling"):
        async with world["factory"]() as s:
            a_row = await s.get(GapAnalysis, world["a"]["gap"])
            b_row = await s.get(GapAnalysis, world["b"]["gap"])
    assert a_row.gap_clusters == [cluster]  # A's row untouched
    assert b_row.gap_clusters != [cluster]  # B's turn recorded on B's row


@pytest.mark.asyncio
async def test_cover_letter_pdf_reads_the_owners_letter(world, monkeypatch):
    from applire.services import cover_letter_pdf

    seen = {}

    async def _html(cl_id, db, require_ready=True, *, user_id=None):
        seen["user_id"] = user_id
        raise LookupError("stop")

    monkeypatch.setattr(cover_letter_pdf, "get_cover_letter_html", _html)
    monkeypatch.setattr(cover_letter_pdf, "AsyncSessionLocal", world["factory"])
    with pytest.raises(LookupError):
        await cover_letter_pdf.render_pdf(uuid.uuid4(), user_id=world["b"]["user"])
    assert seen["user_id"] == world["b"]["user"]
