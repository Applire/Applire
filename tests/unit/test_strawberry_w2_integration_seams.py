# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Strawberry W2 integration — call sites that met only when 3c, 3d and 4b were
merged (ADR-092 cl. 6/14, ruling 3d-1). Each caller below used to rely on the
owner-context fallback (``OWNER_FALLBACK_STATS``) or a signature-probing shim;
each now names the row's owner explicitly. One seam test per call site:

* ``services/session.py::_record_cluster_turn`` -> ``gap_coverage.record_turn_outcome(user_id=record.user_id)``
* ``routers/cover_letter.py::get_pdf`` -> ``cover_letter_pdf.render_pdf(user_id=user.id)``
* ``mcp/server.py::_audit_stored_document`` -> ``cv._latest_keyword_ledger(user_id=record.user_id)``

(The three letter-service ``render_pdf`` sites are pinned in
``test_docx_ats_report_persistence_letter.py``; the letter's two
``non_claim_names_for_job`` sites in ``test_non_claim_names_call_sites.py``.)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire import ownership


@pytest.mark.asyncio
async def test_record_cluster_turn_names_the_session_owner():
    from applire.services import session as session_svc

    owner = uuid.uuid4()
    record = SimpleNamespace(id=uuid.uuid4(), user_id=owner, job_analysis_id=uuid.uuid4())
    gap_id = uuid.uuid4()
    state = {
        "gap_analysis_id": str(gap_id),
        "mode": "targeted",
        "gap_clusters_by_id": {"c1": {"id": "c1", "gaps": ["Kubernetes"]}},
        "messages": [{"role": "assistant", "content": "q"}, {"role": "user", "content": "a"}],
    }
    row = SimpleNamespace(id=gap_id, gap_clusters=[{"id": "c1", "gaps": ["Kubernetes"]}], keyword_ledger=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    spy = AsyncMock(return_value=None)
    # A different ambient owner: a dropped kwarg would resolve to THIS id.
    with patch.object(session_svc.gap_coverage, "record_turn_outcome", new=spy), \
         ownership.owner_context(uuid.uuid4()):
        await session_svc._record_cluster_turn(
            record, state, db, current_gap="c1", answer="a", updated_profile={}
        )

    assert spy.await_count == 1
    assert spy.await_args.kwargs.get("user_id") == owner


@pytest.mark.asyncio
async def test_letter_pdf_route_names_the_caller():
    from applire.routers import cover_letter as router

    user = SimpleNamespace(id=uuid.uuid4())
    cl_id = uuid.uuid4()
    spy = AsyncMock(return_value=b"%PDF")
    with patch("applire.services.cover_letter.get_cover_letter_pdf_filename",
               new=AsyncMock(return_value="x.pdf")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=spy):
        response = await router.get_pdf(cl_id, db=MagicMock(), user=user)

    assert response.body == b"%PDF"
    assert spy.await_args.args[0] == cl_id
    assert spy.await_args.kwargs.get("user_id") == user.id


@pytest.mark.asyncio
async def test_agent_door_audit_reads_the_ledger_of_the_cv_owner():
    import applire.mcp.server as server

    owner = uuid.uuid4()
    record = SimpleNamespace(
        id=uuid.uuid4(), user_id=owner, job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        truthfulness_report=None, tailored_data={},
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(profile_json={"skills": []}))
    db.commit = AsyncMock()
    ledger = AsyncMock(return_value=None)
    report = MagicMock()
    report.model_dump.return_value = {"claims": []}
    with patch.object(server.cv_svc, "_latest_keyword_ledger", new=ledger), \
         patch.object(server.oracle_svc, "audit_document", new=AsyncMock(return_value=report)), \
         patch.object(server, "get_provider", return_value=MagicMock()):
        out = await server._audit_stored_document(record, "cv", db)

    assert out["document_id"] == str(record.id)
    assert ledger.await_args.kwargs.get("user_id") == owner


# ---------------------------------------------------------------------------
# get_ui_language(user_id=…) — found by the W2-integration report-mode run:
# the only owner fallbacks reached over a real door (HTTP / MCP wrapper) were
# these five ``get_ui_language(db)`` calls. Each test runs under a DIFFERENT
# ambient owner and stops the path right after the language read (a sentinel),
# so a dropped kwarg would read the ambient user's language.
# ---------------------------------------------------------------------------


class _Stop(Exception):
    pass


def _lang_spy():
    seen: dict = {}

    async def _spy(db, *, user_id=None):
        seen["user_id"] = user_id
        raise _Stop

    return seen, _spy


@pytest.mark.asyncio
async def test_enrich_next_question_reads_the_session_owners_language():
    from applire.routers import profile_enrich as r

    owner = uuid.uuid4()
    session = SimpleNamespace(
        user_id=owner, status="active",
        state={"critical_gaps": ["a", "b"], "addressed_gaps": [], "na_gaps": [], "skipped_gaps": [], "current_gap_index": 0},
    )
    seen, spy = _lang_spy()
    with patch.object(r, "get_ui_language", new=spy), ownership.owner_context(uuid.uuid4()):
        with pytest.raises(_Stop):
            await r._next_question_or_done(session, {}, MagicMock(), MagicMock())
    assert seen["user_id"] == owner


@pytest.mark.asyncio
async def test_enrich_start_reads_the_profile_owners_language():
    from applire.routers import profile_enrich as r
    from applire.schemas.enrich import EnrichStartRequest

    owner = uuid.uuid4()
    record = SimpleNamespace(id=uuid.uuid4(), user_id=owner, profile_json={})
    seen, spy = _lang_spy()
    with patch.object(r, "_load_profile", new=AsyncMock(return_value=record)), \
         patch.object(r, "_active_enrich_session", new=AsyncMock(return_value=None)), \
         patch.object(r, "gap_detector_mode_c", return_value=["Zertifikate"]), \
         patch.object(r, "get_ui_language", new=spy), ownership.owner_context(uuid.uuid4()):
        with pytest.raises(_Stop):
            await r.start_enrich_session(
                EnrichStartRequest(), db=MagicMock(), provider=MagicMock(),
                current_user=SimpleNamespace(id=owner),
            )
    assert seen["user_id"] == owner


@pytest.mark.asyncio
async def test_enrich_respond_reads_the_profile_owners_language():
    from applire.routers import profile_enrich as r
    from applire.schemas.enrich import EnrichRespondRequest

    owner = uuid.uuid4()
    session = SimpleNamespace(
        user_id=owner,
        state={"critical_gaps": ["a"], "current_gap_index": 0, "current_question": "q?", "messages": [], "addressed_gaps": []},
    )
    record = SimpleNamespace(id=uuid.uuid4(), user_id=owner, profile_json={})
    seen, spy = _lang_spy()
    with patch.object(r, "_load_session", new=AsyncMock(return_value=session)), \
         patch.object(r, "_load_profile", new=AsyncMock(return_value=record)), \
         patch.object(r, "get_ui_language", new=spy), ownership.owner_context(uuid.uuid4()):
        with pytest.raises(_Stop):
            await r.respond_to_enrich(
                uuid.uuid4(), EnrichRespondRequest(answer="Ich habe ein PMP-Zertifikat."),
                db=MagicMock(), provider=MagicMock(), current_user=SimpleNamespace(id=owner),
            )
    assert seen["user_id"] == owner


@pytest.mark.asyncio
async def test_apply_merge_reads_the_vault_owners_language():
    import applire.services.profile as profile_svc
    from applire.schemas.profile import MasterProfileData
    from applire.services import session as session_svc

    owner = uuid.uuid4()
    existing = SimpleNamespace(id=uuid.uuid4(), user_id=owner, profile_json={})
    seen, spy = _lang_spy()
    with patch.object(profile_svc, "_get_latest", new=AsyncMock(return_value=existing)), \
         patch.object(session_svc, "get_ui_language", new=spy), ownership.owner_context(uuid.uuid4()):
        with pytest.raises(_Stop):
            await profile_svc._apply_merge(
                MagicMock(), MasterProfileData(), source="cv_upload", emb_provider=MagicMock(),
                provider=MagicMock(), user_id=owner,
            )
    assert seen["user_id"] == owner


@pytest.mark.asyncio
async def test_testimony_reads_the_vault_owners_language():
    import applire.services.profile as profile_svc
    from applire.services import session as session_svc
    from applire.services.profile.reconcile import testimony_bridge

    owner = uuid.uuid4()
    record = SimpleNamespace(id=uuid.uuid4(), user_id=owner, profile_json={})
    seen, spy = _lang_spy()
    with patch.object(profile_svc, "_get_latest", new=AsyncMock(return_value=record)), \
         patch.object(session_svc, "get_ui_language", new=spy), ownership.owner_context(uuid.uuid4()):
        with pytest.raises(_Stop):
            await testimony_bridge.submit_testimony(
                "Ich leite seit 2021 ein Team.", MagicMock(), MagicMock(), user_id=owner,
            )
    assert seen["user_id"] == owner
