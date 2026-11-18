# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""At the default level the logs carry ids, counts and check names only
(ADR-087 cl. 9 amended 2026-11-18).

A synthetic person's text (the canary) is pushed through real service paths —
the REST catch-all, the MCP catch-all, the posting analysis with its floors and
its embedding step, the CV writer's skill-spelling guard, a ticked posting term,
and real HTTP requests through uvicorn's access log with the canary in every
query parameter that can carry a sign-in value, a page address or typed text —
and through the
MODEL-REPLY side: replies carrying the canary in every free field and in every
enum position (status, kind, op, cluster id, category) handed to the code that
parses them — once with the
content tree captured (positive control: every path really reaches a content
line), and once at ``LOG_LEVEL=INFO`` with the process configured the way
``main.py`` configures it. At INFO no record at INFO or above, formatted with
its traceback, holds the canary, and neither content directory gets a file.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import logging
import socket
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
import uvicorn
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire import content_log
from applire.config import settings

CANARY = "Kanarien-Weberit-Schichtleitung-4711"
#: The same canary in the shape of a code label (a model can answer an enum
#: slot with one lower-case word of the profile).
WORD = "kanarienweberit_schicht"
JD_TEXT = f"Leiter Operations bei Rheinwerk. {CANARY}. Lean, MES, SAP."


@pytest.fixture(autouse=True)
def _no_review(monkeypatch):
    monkeypatch.setattr("applire.services.job.LLM_REVIEW_MAX_RETRIES", 0)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _payload():
    return {
        "company_name": "Rheinwerk Verpackungen GmbH",
        "role_title": "Leiter Operations (m/w/d)",
        "required_skills": ["5S", "MES"],
        "nice_to_have_skills": [],
        "keywords": ["Mehrschichtbetrieb"],
        "seniority_level": "Leitung",
        "company_culture_signals": [],
        "language_requirement": "Deutsch",
        "berufsbild_code": None,
        "berufsbild_label": None,
        "leadership_emphasis": {"emphasis": "unclear", "quote": CANARY},
        "scope_requirements": [{"kind": "team_size", "value": "viele", "quote": CANARY}],
    }


class _Embed:
    async def embed(self, text):
        raise RuntimeError(f"embedding refused: {text}")


async def _posting_analysis(db):
    from applire.services.job import analyze_jd

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_payload())
    await analyze_jd(JD_TEXT, db, provider, embedding_provider=_Embed())


async def _rest_catch_all(db):
    from applire.auth.deps import require_user
    from applire.db.session import get_db
    from applire.routers import job as job_router

    app = FastAPI()
    app.include_router(job_router.router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_user] = lambda: MagicMock(id=uuid.uuid4())
    app.dependency_overrides[job_router._get_provider] = lambda: MagicMock()
    with patch.object(job_router, "analyze_jd", AsyncMock(side_effect=RuntimeError(f"boom {CANARY}"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post("/api/job/analyze", json={"text": JD_TEXT})
    assert r.status_code == 500 and CANARY not in r.text


async def _mcp_catch_all():
    from applire.mcp import server
    from applire.models.user import User

    person = User(email="tester-01@example.org")
    person.id = uuid.uuid4()
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server.job_svc, "analyze_jd", AsyncMock(side_effect=RuntimeError(f"boom {CANARY}"))), patch.object(
        server, "get_db", MagicMock()
    ), patch.object(server, "get_provider", MagicMock()), patch(
        "applire.services.instance_settings.refresh", AsyncMock(return_value={})
    ):
        with pytest.raises(Exception) as exc:
            await server.mcp.call_tool("analyze_jd", {"text": JD_TEXT})
    assert CANARY not in str(exc.value)


def _skill_guard():
    from applire.schemas.cv import TailoredContact, TailoredCVData
    from applire.services.cv import _restore_skill_spelling

    out = TailoredCVData(contact=TailoredContact(name="Test Candidate"), skills=[CANARY])
    _restore_skill_spelling(out, {"skills": [{"name": "Python", "category": "technical"}]})


def _model_replies():
    """Replies a model could send: the canary in free fields and enum slots."""
    from applire.services.gap import _unique_cluster_ids
    from applire.services.keyword_ledger import build_keyword_ledger
    from applire.services.match_score import compute_match_score, compute_match_score_from_ledger
    from applire.services.outcome_critic import _advisories_from_judgement
    from applire.services.profile.reconcile.engine import _parse_ops

    for slot in (CANARY, WORD):
        row = {"concept": CANARY, "requirement": CANARY, "status": slot, "evidence": CANARY,
               "reason": CANARY, "claimable": slot}
        build_keyword_ledger([row], [CANARY], [], [CANARY])
        compute_match_score([row], [CANARY], [])
        compute_match_score_from_ledger([{**row, "fit_weight": 1.0}])
    _advisories_from_judgement(
        {"findings": [{"worth_surfacing": True, "kind": CANARY, "concept": CANARY,
                       "cv_quote": CANARY, "letter_quote": CANARY}]},
        mount="cv", cv_units=[CANARY], letter_units=[],
    )
    _parse_ops([{"op": CANARY, "section": CANARY, "value": CANARY}, {"op": "set_field", "field": CANARY}])
    _unique_cluster_ids([{"id": CANARY, "category": CANARY}], {CANARY})
    _unique_cluster_ids([{"id": WORD, "category": WORD}], {WORD})


async def _posting_term_answer(db):
    """A posting term the person ticks is the posting's wording, i.e. content."""
    from applire.services.posting_term_answer import answer_posting_terms

    spec = importlib.util.spec_from_file_location(
        "_canary_q10", Path(__file__).parent / "test_q10_posting_term_answer.py"
    )
    q10 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(q10)
    ledger = copy.deepcopy(q10.LEDGER)
    ledger[0]["concept"] = CANARY
    job = await q10._seed(db, ledger=ledger)
    state = await answer_posting_terms(db, job.id, q10.HARNESS_USER_ID, confirmed=[CANARY], via="person")
    assert state.confirmed == [CANARY]


#: Every query parameter the access log must not write: the sign-in values, the
#: page address sign-in returns to, typed search text, link and API tokens.
#: Named here, not read from ``auth/logfilter.py``, so a parameter dropped from
#: the redaction lists fails this test.
REQUEST_PARAMS = ("q", "next", "code", "state", "sig", "token", "access_token", "setup_token")


async def _access_log_requests():
    """Real requests through a real uvicorn server: the access line is written by
    uvicorn's protocol, with the redaction ``main.py`` installs on import."""
    import applire.main  # noqa: F401 — installs the access-log redaction
    from applire.auth.logfilter import AccessLogRedactionFilter, ACCESS_LOGGER

    assert any(isinstance(f, AccessLogRedactionFilter) for f in logging.getLogger(ACCESS_LOGGER).filters)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(FastAPI(), log_config=None, lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for name in REQUEST_PARAMS:
                await client.get(f"/api/auth/oidc/callback?{name}={CANARY}&exp=1")
            await client.get(f"/api/auth/oidc/start?next=%2Fdashboard%3Fjd_text%3D{CANARY}")
    finally:
        server.should_exit = True
        await task
        sock.close()


async def _all_paths(db):
    await _posting_analysis(db)
    await _rest_catch_all(db)
    await _mcp_catch_all()
    _skill_guard()
    _model_replies()
    await _posting_term_answer(db)
    await _access_log_requests()


PATHS = {
    "posting analysis": lambda db: _posting_analysis(db),
    "REST catch-all": lambda db: _rest_catch_all(db),
    "MCP catch-all": lambda db: _mcp_catch_all(),
    "posting term answer": lambda db: _posting_term_answer(db),
}


class _Seen(list):
    handler: logging.Handler


@pytest.fixture
def stream():
    """Everything the root and ``applire`` loggers would hand a stream handler,
    formatted the way the stream handler formats it (traceback included). The
    handler is ``seen.handler``: the test hands it to the switch as the
    processes hand theirs."""
    seen = _Seen()
    fmt = logging.Formatter("%(levelname)s %(name)s %(message)s")

    class _H(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.INFO:
                seen.append(fmt.format(record))

    handler = _H(level=logging.NOTSET)
    seen.handler = handler
    targets = [logging.getLogger(), logging.getLogger("applire")]
    old = [t.level for t in targets]
    for t in targets:
        t.addHandler(handler)
        t.setLevel(logging.INFO)
    yield seen
    for t, lvl in zip(targets, old):
        t.removeHandler(handler)
        t.setLevel(lvl)


@pytest.mark.asyncio
async def test_every_path_reaches_a_content_line(db, monkeypatch):
    """Positive control: without it the INFO test below could pass vacuously."""
    reached = {}
    for name, run in {**PATHS, "skill guard": _skill_guard, "model replies": _model_replies}.items():
        with content_log.capturing() as records:
            out = run(db) if name in PATHS else run()
            if out is not None:
                await out
        reached[name] = any(CANARY in logging.Formatter().format(r) for r in records)
    assert reached == {name: True for name in reached}


@pytest.mark.asyncio
async def test_at_info_no_record_and_no_file_holds_the_canary(db, monkeypatch, tmp_path, stream):
    content_dir, llm_dir = tmp_path / "content", tmp_path / "llm"
    monkeypatch.setattr(settings, "content_log_dir", str(content_dir))
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(llm_dir))
    monkeypatch.setattr(settings, "log_level", "INFO")
    assert content_log.configure_content_logging(stream.handler) is False

    await _all_paths(db)

    assert stream, "the paths logged nothing at INFO — the test would be blind"
    access = [line for line in stream if line.startswith("INFO uvicorn.access")]
    assert len(access) == len(REQUEST_PARAMS) + 1, "the request arm wrote no access lines"
    assert all("[redacted]" in line for line in access)
    holding = [line for line in stream if CANARY in line or WORD in line]
    assert holding == []
    assert not content_dir.exists() and not llm_dir.exists()


def test_a_label_passes_only_as_a_known_value_or_a_word_of_the_code():
    label = content_log.label
    assert label("direct") == "direct" and label("set_field") == "set_field"
    assert label(3) == 3 and label(None) is None and label(True) is True
    for value in (CANARY, WORD, "cluster-sap-und-produktionsplanung", "Weberit", "", ["direct"]):
        assert label(value) == content_log.OTHER
    assert label("B", known=("B", "C")) == "B"
    assert label("direct", known=("B", "C")) == content_log.OTHER
    assert label(["B"], known=("B", "C")) == content_log.OTHER
