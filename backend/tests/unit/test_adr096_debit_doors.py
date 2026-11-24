# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 2 (Sorbet invite-gate, MD-G6 follow-up) — every debited door,
entered the way a person or an agent really enters it.

The doors (cl. 7 as ruled): ``POST /api/cv/generate``, ``POST
/api/cover-letter/generate`` (one ``application`` per job, whichever document
comes first), ``POST /api/profile/import-jobs`` and the agent's ``import_cv``
with a PDF and with text (one ``profile_import`` per import), the agent door
through ``mcp.call_tool`` — the layer a stdio agent hits.

The REST doors run the real auth chain and the real admission
(``require_user`` → ``admit_model_work``, row 6 of the payer table: user mode,
no key, the instance pays within the person's limits). The provider the route
would build is replaced by a spy AFTER the admission, and the background render
or extraction by a recorder, so "before any model work" is observable: on a
refusal the spy saw no call and no background work was scheduled.

"Not committed" is shown the way production ends a refused request: the
session is closed without a commit (here: rolled back), and only what the door
committed before the refusal would survive.

Each door has a test that fails when its ``debit_for_door`` call is removed
(mutation record in ``Documents/Runs/Sorbet/invite-gate/a/report.md``).
"""

from __future__ import annotations

import base64
import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import func, select

from applire.auth import get_auth_provider, links
from applire.config import settings
from applire.db.session import get_db
from applire.models.allowance import AllowanceLedger
from applire.models.user import User
from applire.services import allowance, allowance_seam
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse: the MCP door signs its links
from tests.support.owners import HARNESS_USER_ID
from tests.support.owners_1b import client_for
from tests.support.profile_factory import make_master_profile

ORIGIN = {"Origin": "http://applire.test"}
SECRET = "provider-key-secret-for-debit-door-tests-0123456789"
CV_TEXT = "Emma Beispiel\nBackend Engineer\nDataCraft GmbH 2019-2024: built payment services."


class Acting:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.user_id = user_id

    async def get_current_user(self, request, db):
        request.state.auth_via = "session"
        return await db.get(User, self.user_id)


class NoPlan(allowance_seam.PlanProvider):
    """A registered plan provider (the Cloud overlay's place) where the person
    has chosen no plan yet."""

    name = "no-plan"

    async def ensure_month_grant(self, db, *, user, subject, period, period_start, expires_at):
        return False

    async def status(self, db, *, user, period_start):
        return {"plan": None, "pending": None}

    async def catalog(self, db):
        return {"currency": "EUR", "plans": [], "packs": [], "simulated": True}

    async def change_plan(self, db, *, user, subject, plan_id):
        return {"change": "unchanged"}

    async def purchase(self, db, *, user, subject, pack_id, idempotency_key):
        return {"purchase_id": idempotency_key, "applications": 0, "created": False}


@pytest.fixture(autouse=True)
def _clean():
    from applire.providers.llm import usage

    previous = links._instance_secret
    links.set_instance_secret("debit-door-tests-instance-secret-0123456789")
    allowance.reset_context_for_tests()
    allowance_seam.reset_for_tests()
    # The usage sink's process-wide latch: an earlier test without a database
    # turns it off, and under ``allowance`` an instance that cannot count fails
    # closed (503 ``allowance_unavailable``) — right in production, wrong here.
    usage.reset_sink_state()
    yield
    allowance.reset_context_for_tests()
    allowance_seam.reset_for_tests()
    links._instance_secret = previous


@pytest.fixture
def metered(monkeypatch):
    """The staging shape: user mode, no key of the person's own (row 6), the
    allowance policy with one application and two imports a month."""
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_applications_per_month", 1)
    monkeypatch.setattr(settings, "allowance_imports_per_month", 2)
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 10_000_000)
    monkeypatch.setattr(settings, "llm_key_mode", "user")
    monkeypatch.setattr(settings, "provider_key_secret", SECRET)
    monkeypatch.setattr(settings, "llm_provider", "mock")


@pytest.fixture
def community(monkeypatch):
    """The Community default: policy ``none``, instance mode."""
    monkeypatch.setattr(settings, "allowance_policy", "none")
    monkeypatch.setattr(settings, "llm_provider", "mock")
    assert settings.llm_key_mode == "instance"


# ── the world: one person, one job, one profile ───────────────────────────────


async def _seed(db) -> tuple[User, uuid.UUID, uuid.UUID]:
    """The harness user (the acting person) with a profile and two jobs, each
    with the person's application link and flow session."""
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from tests.support.owners_3c import link_job

    person = User(id=HARNESS_USER_ID, email="tester-debit@example.org", role="user")
    db.add(person)
    await db.flush()
    jobs = []
    for company in ("DataCraft GmbH", "Nordlicht AG"):
        job_id = uuid.uuid4()
        db.add(JobAnalysis(
            id=job_id, raw_text_hash=f"hash-{job_id}", raw_text="Backend Engineer, Python",
            role_title="Backend Engineer", company_name=company, required_skills=[],
            nice_to_have_skills=[], keywords=[], seniority_level="Senior",
            company_culture_signals=[], language_requirement="German",
        ))
        await db.flush()
        await link_job(db, job_id)
        db.add(FlowSession(user_id=person.id, job_id=job_id, available_actions={}))
        jobs.append(job_id)
    db.add(make_master_profile(
        id=uuid.uuid4(), profile_json={"personal_info": {"full_name": "Emma Beispiel"}},
    ))
    await db.commit()
    return person, jobs[0], jobs[1]


@pytest_asyncio.fixture
async def rest(async_db):
    """The three REST doors with the real auth chain and admission; the provider
    a door builds is a spy, the background work a recorder."""
    from applire.auth.deps import admit_model_work
    from applire.routers import cover_letter, cv, profile

    person, job_a, job_b = await _seed(async_db)
    spy = AsyncMock(name="provider")

    async def _spy_provider(_payer=Depends(admit_model_work)):
        return spy

    async def _db():
        yield async_db

    async def _auth():
        return Acting(person.id)

    app = FastAPI()
    for router in (cv.router, cover_letter.router, profile.router):
        app.include_router(router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _auth
    app.dependency_overrides[cv._get_provider] = _spy_provider
    app.dependency_overrides[cover_letter._get_provider] = _spy_provider

    work = {
        "cv": AsyncMock(name="render_cv"),
        "letter": AsyncMock(name="render_letter"),
        "import": AsyncMock(name="extract_import"),
    }
    with patch("applire.services.cv._render_cv_background", work["cv"]), patch(
        "applire.services.cover_letter._render_cover_letter_background", work["letter"]
    ), patch("applire.routers.profile.run_import_job_background", work["import"]), patch(
        "applire.routers.cv.record_generated_cv", AsyncMock()
    ):
        async with client_for(app) as client:
            yield {
                "db": async_db, "client": client, "person": person, "job_a": job_a,
                "job_b": job_b, "spy": spy, "work": work,
            }


async def _debits(db, action: str) -> list[AllowanceLedger]:
    rows = await db.execute(
        select(AllowanceLedger).where(AllowanceLedger.kind == "debit", AllowanceLedger.action == action)
    )
    return list(rows.scalars())


async def _ledger_rows(db) -> int:
    return (await db.execute(select(func.count()).select_from(AllowanceLedger))).scalar_one()


async def _count(db, model, **where) -> int:
    stmt = select(func.count()).select_from(model)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    return (await db.execute(stmt)).scalar_one()


async def _spend_the_month(db, person, action: str, n: int) -> None:
    """Use up the month at another door (a different ref), committed."""
    for i in range(n):
        await allowance.debit_for_door(db, person.id, action, ref=f"elsewhere-{i}")
    await db.commit()


def _refused(response, code: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error_code"] == code


async def _cv(client, job_id):
    return await client.post("/api/cv/generate", json={"job_id": str(job_id)}, headers=ORIGIN)


async def _letter(client, job_id):
    return await client.post(
        "/api/cover-letter/generate",
        json={"job_id": str(job_id), "tone": "formal", "save_availability_to_profile": False},
        headers=ORIGIN,
    )


async def _upload(client):
    return await client.post(
        "/api/profile/import-jobs",
        files={"file": ("emma.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        headers=ORIGIN,
    )


# ── door 1: POST /api/cv/generate ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_door_debits_one_application_and_a_regeneration_is_free(rest, metered):
    db, client = rest["db"], rest["client"]
    assert (await _cv(client, rest["job_a"])).status_code == 201
    debits = await _debits(db, "application")
    assert [(d.ref, d.amount) for d in debits] == [(str(rest["job_a"]), 1)]
    assert debits[0].grant_id is not None and debits[0].payer == "instance"
    # The same job again (a regeneration), with the month now used up: admitted, no new debit.
    assert (await _cv(client, rest["job_a"])).status_code == 201
    assert len(await _debits(db, "application")) == 1


@pytest.mark.asyncio
async def test_cv_door_refuses_at_zero_before_any_model_work_or_row(rest, metered):
    from applire.models.cv import GeneratedCV

    db, client = rest["db"], rest["client"]
    await _spend_the_month(db, rest["person"], "application", 1)
    r = await _cv(client, rest["job_a"])
    _refused(r, "allowance_exhausted")
    assert r.json()["detail"]["action"] == "application" and r.json()["detail"]["renews_on"]
    await db.rollback()  # what closing the refused request's session does
    assert await _count(db, GeneratedCV, job_analysis_id=rest["job_a"]) == 0
    assert rest["spy"].mock_calls == [] and rest["work"]["cv"].await_count == 0
    assert len(await _debits(db, "application")) == 1  # only the one spent elsewhere


@pytest.mark.asyncio
async def test_cv_door_without_a_plan_is_plan_required(rest, metered):
    from applire.models.cv import GeneratedCV

    db, client = rest["db"], rest["client"]
    allowance_seam.register_plan_provider(NoPlan)
    _refused(await _cv(client, rest["job_a"]), "plan_required")
    await db.rollback()
    assert await _count(db, GeneratedCV) == 0 and await _ledger_rows(db) == 0
    assert rest["spy"].mock_calls == [] and rest["work"]["cv"].await_count == 0


@pytest.mark.asyncio
async def test_cv_door_under_policy_none_writes_no_ledger_row(rest, community):
    db, client = rest["db"], rest["client"]
    for _ in range(3):
        assert (await _cv(client, rest["job_a"])).status_code == 201
    assert (await _cv(client, rest["job_b"])).status_code == 201
    assert await _ledger_rows(db) == 0


# ── door 2: POST /api/cover-letter/generate ───────────────────────────────────


@pytest.mark.asyncio
async def test_letter_door_debits_one_application_and_a_regeneration_is_free(rest, metered):
    db, client = rest["db"], rest["client"]
    assert (await _letter(client, rest["job_a"])).status_code == 201
    assert [d.ref for d in await _debits(db, "application")] == [str(rest["job_a"])]
    assert (await _letter(client, rest["job_a"])).status_code == 201
    assert len(await _debits(db, "application")) == 1


@pytest.mark.asyncio
async def test_a_cv_then_a_letter_of_one_job_is_one_application(rest, metered):
    db, client = rest["db"], rest["client"]
    assert (await _cv(client, rest["job_a"])).status_code == 201
    # The month (one application) is used up — the letter of the SAME job still starts.
    r = await _letter(client, rest["job_a"])
    assert r.status_code == 201, r.text
    assert [d.ref for d in await _debits(db, "application")] == [str(rest["job_a"])]
    # A second job is a second application: refused.
    _refused(await _letter(client, rest["job_b"]), "allowance_exhausted")


@pytest.mark.asyncio
async def test_letter_door_refuses_at_zero_before_any_model_work_or_row(rest, metered):
    from applire.models.cover_letter import GeneratedCoverLetter

    db, client = rest["db"], rest["client"]
    await _spend_the_month(db, rest["person"], "application", 1)
    _refused(await _letter(client, rest["job_a"]), "allowance_exhausted")
    await db.rollback()
    assert await _count(db, GeneratedCoverLetter) == 0
    assert rest["spy"].mock_calls == [] and rest["work"]["letter"].await_count == 0
    assert len(await _debits(db, "application")) == 1


@pytest.mark.asyncio
async def test_letter_door_without_a_plan_is_plan_required(rest, metered):
    from applire.models.cover_letter import GeneratedCoverLetter

    db, client = rest["db"], rest["client"]
    allowance_seam.register_plan_provider(NoPlan)
    _refused(await _letter(client, rest["job_a"]), "plan_required")
    await db.rollback()
    assert await _count(db, GeneratedCoverLetter) == 0 and await _ledger_rows(db) == 0
    assert rest["spy"].mock_calls == [] and rest["work"]["letter"].await_count == 0


@pytest.mark.asyncio
async def test_letter_door_under_policy_none_writes_no_ledger_row(rest, community):
    db, client = rest["db"], rest["client"]
    for _ in range(2):
        assert (await _letter(client, rest["job_a"])).status_code == 201
    assert await _ledger_rows(db) == 0


# ── door 3: POST /api/profile/import-jobs ─────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_door_debits_one_import_per_job(rest, metered):
    from applire.models.import_job import CVImportJob

    db, client = rest["db"], rest["client"]
    first, second = await _upload(client), await _upload(client)
    assert first.status_code == second.status_code == 202
    refs = sorted(d.ref for d in await _debits(db, "profile_import"))
    assert refs == sorted([first.json()["import_id"], second.json()["import_id"]])
    assert await _count(db, CVImportJob) == 2
    assert await _debits(db, "application") == []  # an import is not an application


@pytest.mark.asyncio
async def test_upload_door_refuses_at_zero_before_the_job_row_or_extraction(rest, metered):
    from applire.models.import_job import CVImportJob

    db, client = rest["db"], rest["client"]
    await _spend_the_month(db, rest["person"], "profile_import", 2)
    r = await _upload(client)
    _refused(r, "allowance_exhausted")
    assert r.json()["detail"]["action"] == "profile_import"
    # adv/plans F7: the import's message speaks of imports, not applications.
    message = r.json()["detail"]["message"]
    assert "CV imports are used up" in message and "pplication" not in message
    await db.rollback()
    assert await _count(db, CVImportJob) == 0  # the flushed job row was never committed
    assert rest["work"]["import"].await_count == 0 and rest["work"]["import"].call_count == 0
    assert len(await _debits(db, "profile_import")) == 2


@pytest.mark.asyncio
async def test_upload_door_needs_no_plan(rest, metered):
    """Imports are Core's grant on every install (amendment cl. 3): a plan
    provider without a plan for the person refuses applications, not imports."""
    db, client = rest["db"], rest["client"]
    allowance_seam.register_plan_provider(NoPlan)
    assert (await _upload(client)).status_code == 202
    assert len(await _debits(db, "profile_import")) == 1


@pytest.mark.asyncio
async def test_upload_door_under_policy_none_writes_no_ledger_row(rest, community):
    db, client = rest["db"], rest["client"]
    for _ in range(3):
        assert (await _upload(client)).status_code == 202
    assert await _ledger_rows(db) == 0


# ── doors 4 + 5: the agent's import_cv (PDF, text) through mcp.call_tool ──────


@contextlib.contextmanager
def _agent(db, person):
    """``mcp.call_tool`` as ``person``: the real identity wrapper and admission
    on the test's session; the extraction is a recorder that also says whether
    the debit was already committed when the model work began."""
    from applire.mcp import server

    @contextlib.asynccontextmanager
    async def _db():
        yield db

    seen: dict = {}

    async def _extract(*args, **kwargs):
        seen["in_transaction"] = db.in_transaction()
        await db.commit()  # the real ingest commits its merge (or its held record)
        return MagicMock(name="import_result", status="MERGED")

    pdf = AsyncMock(name="import_from_pdf", side_effect=_extract)
    text = AsyncMock(name="import_from_text", side_effect=_extract)
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server, "get_db", _db), patch(
        "applire.services.instance_settings.refresh", AsyncMock(return_value={})
    ), patch.object(server, "_current_user_id", AsyncMock(return_value=person.id)), patch.object(
        server, "get_provider", MagicMock(name="factory")
    ) as factory, patch.object(server, "get_storage", MagicMock(name="storage")), patch.object(
        server.profile_svc, "import_from_pdf", pdf
    ), patch.object(server.profile_svc, "import_from_text", text), patch.object(
        server, "_profile_summary", lambda result: {"imported": True}
    ):
        yield server, {"pdf": pdf, "text": text, "factory": factory, "seen": seen}


AGENT_DOORS = {
    "pdf": {"file_base64": base64.b64encode(b"%PDF-1.4 synthetic").decode(), "filename": "emma.pdf"},
    "text": {"text": CV_TEXT},
}


def _agent_refusal_text(exc: BaseException) -> str:
    """What the agent reads: FastMCP answers ``Error executing tool <name>: <msg>``
    (it drops ``McpError.data`` and the code number); the code must lead <msg>."""
    message = str(exc)
    prefix = "Error executing tool import_cv: "
    assert message.startswith(prefix), message
    return message[len(prefix):]


@pytest_asyncio.fixture
async def agent_world(async_db):
    person, *_ = await _seed(async_db)
    return async_db, person


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(AGENT_DOORS))
async def test_agent_import_door_debits_one_import_committed_before_the_model_work(agent_world, metered, door):
    db, person = agent_world
    with _agent(db, person) as (server, rec):
        result = await server.mcp.call_tool("import_cv", AGENT_DOORS[door])
        assert result is not None
        await server.mcp.call_tool("import_cv", AGENT_DOORS[door])
    assert rec[door].await_count == 2
    debits = await _debits(db, "profile_import")
    assert len(debits) == 2 and all(d.ref.startswith("mcp-") for d in debits)
    assert len({d.ref for d in debits}) == 2  # each call is one import, like each upload job
    # Counted like the upload door's job row: committed BEFORE the extraction,
    # so the subject lock is not held across minutes of model work.
    assert rec["seen"]["in_transaction"] is False
    assert await _debits(db, "application") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(AGENT_DOORS))
async def test_agent_import_door_refuses_at_zero_before_the_model_work(agent_world, metered, door):
    db, person = agent_world
    await _spend_the_month(db, person, "profile_import", 2)
    with _agent(db, person) as (server, rec):
        with pytest.raises(Exception) as exc:
            await server.mcp.call_tool("import_cv", AGENT_DOORS[door])
    text = _agent_refusal_text(exc.value)
    assert text.startswith("allowance_exhausted: "), text
    assert "CV imports are used up" in text and "pplication" not in text
    # Below FastMCP's text: the door's own answer is the admission refusal
    # (-32004, the code first), the same shape as the wrapper's refusals.
    from mcp.shared.exceptions import McpError

    with _agent(db, person) as (server, _):
        with pytest.raises(McpError) as direct:
            await server.import_cv(**AGENT_DOORS[door])
    assert direct.value.error.code == -32004
    assert direct.value.error.message.startswith("allowance_exhausted: ")
    assert rec["pdf"].await_count == 0 and rec["text"].await_count == 0
    assert rec["factory"].return_value.mock_calls == []
    await db.rollback()
    assert len(await _debits(db, "profile_import")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(AGENT_DOORS))
async def test_agent_import_door_needs_no_plan(agent_world, metered, door):
    db, person = agent_world
    allowance_seam.register_plan_provider(NoPlan)
    with _agent(db, person) as (server, rec):
        await server.mcp.call_tool("import_cv", AGENT_DOORS[door])
    assert rec[door].await_count == 1 and len(await _debits(db, "profile_import")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(AGENT_DOORS))
async def test_agent_import_door_under_policy_none_writes_no_ledger_row(agent_world, community, door):
    db, person = agent_world
    with _agent(db, person) as (server, rec):
        for _ in range(3):
            await server.mcp.call_tool("import_cv", AGENT_DOORS[door])
    assert rec[door].await_count == 3 and await _ledger_rows(db) == 0


@pytest.mark.asyncio
async def test_agent_application_doors_answer_the_refusal_code_first(agent_world, metered):
    """The agent's CV and letter tools reach the same service hooks; at zero the
    agent reads the code first, not an internal error."""
    db, person = agent_world
    from applire.models.job import JobAnalysis

    job_id = (await db.execute(select(JobAnalysis.id).limit(1))).scalar_one()
    await _spend_the_month(db, person, "application", 1)
    with _agent(db, person) as (server, _rec):
        for tool, args in (
            ("generate_cv", {"job_id": str(job_id)}),
            ("generate_cover_letter", {"job_id": str(job_id)}),
        ):
            with pytest.raises(Exception) as exc:
                await server.mcp.call_tool(tool, args)
            message = str(exc.value)
            assert message.startswith(f"Error executing tool {tool}: allowance_exhausted: "), message


# ── door 6: the agent's render_document (adv/plans F1) through mcp.call_tool ──


@contextlib.contextmanager
def _agent_render(db, person):
    """``mcp.call_tool("render_document")`` as ``person``; the render (persist +
    audit, the model work) is a recorder that says whether the debit was already
    committed when it began, and returns a plain ready row."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from applire.mcp import server

    @contextlib.asynccontextmanager
    async def _db():
        yield db

    seen: dict = {}

    async def _render(content, job_id, _db, **kwargs):
        seen["in_transaction"] = db.in_transaction()
        return SimpleNamespace(
            id=uuid.uuid4(), status="ready", origin="agent", ats_report=None,
            truthfulness_report=None, review_state=None, audit_state=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=90),
        )

    render = AsyncMock(name="render_agent_cv", side_effect=_render)
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server, "get_db", _db), patch(
        "applire.services.instance_settings.refresh", AsyncMock(return_value={})
    ), patch.object(server, "_current_user_id", AsyncMock(return_value=person.id)), patch.object(
        server.cv_svc, "render_agent_cv", render
    ):
        yield server, {"render": render, "seen": seen}


def _render_args(job_id) -> dict:
    return {"document_kind": "cv", "content": {"contact": {"name": "Emma Beispiel"}}, "job_id": str(job_id)}


async def _first_job(db):
    from applire.models.job import JobAnalysis

    return (await db.execute(select(JobAnalysis.id).order_by(JobAnalysis.company_name).limit(1))).scalar_one()


@pytest.mark.asyncio
async def test_agent_render_counts_the_jobs_application_committed_before_the_render(agent_world, metered):
    db, person = agent_world
    job_id = await _first_job(db)
    with _agent_render(db, person) as (server, rec):
        await server.mcp.call_tool("render_document", _render_args(job_id))
        assert rec["seen"]["in_transaction"] is False  # committed before the model work
        await server.mcp.call_tool("render_document", _render_args(job_id))  # a re-render
    assert rec["render"].await_count == 2
    debits = await _debits(db, "application")
    assert [(d.ref, d.amount) for d in debits] == [(str(job_id), 1)]


@pytest.mark.asyncio
async def test_agent_render_of_a_job_a_generation_already_counted_is_free(agent_world, metered):
    """The same ref as generate_cv / generate_cover_letter: one job, one application."""
    db, person = agent_world
    job_id = await _first_job(db)
    await allowance.debit_for_door(db, person.id, "application", ref=str(job_id))  # generate_cv's debit
    await db.commit()
    with _agent_render(db, person) as (server, rec):
        await server.mcp.call_tool("render_document", _render_args(job_id))
    assert rec["render"].await_count == 1 and len(await _debits(db, "application")) == 1


@pytest.mark.asyncio
async def test_agent_render_refuses_at_zero_before_the_render(agent_world, metered):
    db, person = agent_world
    job_id = await _first_job(db)
    await _spend_the_month(db, person, "application", 1)
    with _agent_render(db, person) as (server, rec):
        with pytest.raises(Exception) as exc:
            await server.mcp.call_tool("render_document", _render_args(job_id))
    message = str(exc.value)
    assert message.startswith("Error executing tool render_document: allowance_exhausted: "), message
    assert rec["render"].await_count == 0
    await db.rollback()
    assert len(await _debits(db, "application")) == 1  # only the one spent elsewhere


@pytest.mark.asyncio
async def test_agent_render_without_a_plan_is_plan_required(agent_world, metered):
    db, person = agent_world
    job_id = await _first_job(db)
    allowance_seam.register_plan_provider(NoPlan)
    with _agent_render(db, person) as (server, rec):
        with pytest.raises(Exception) as exc:
            await server.mcp.call_tool("render_document", _render_args(job_id))
    assert str(exc.value).startswith("Error executing tool render_document: plan_required"), str(exc.value)
    assert rec["render"].await_count == 0
    await db.rollback()
    assert await _ledger_rows(db) == 0


@pytest.mark.asyncio
async def test_agent_render_under_policy_none_writes_no_ledger_row(agent_world, community):
    db, person = agent_world
    job_id = await _first_job(db)
    with _agent_render(db, person) as (server, rec):
        for _ in range(3):
            await server.mcp.call_tool("render_document", _render_args(job_id))
    assert rec["render"].await_count == 3 and await _ledger_rows(db) == 0
