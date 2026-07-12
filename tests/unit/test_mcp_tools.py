"""
Iteration 8 — MCP Server: tool tests (unit)

Each tool handler is a plain async function and can be called directly without
going through the MCP protocol.  Services and the DB session are mocked.

Run:
    pytest tests/unit/test_mcp_tools.py -v
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.exceptions import McpError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db():
    """Return a context-manager mock for applire.mcp.deps.get_db."""
    mock_session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_session


def _mock_result(**kwargs) -> MagicMock:
    """Build a Pydantic-like result mock whose model_dump(mode='json') returns kwargs."""
    m = MagicMock()
    m.model_dump.return_value = kwargs
    return m


# ---------------------------------------------------------------------------
# analyze_jd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_jd_happy_path():
    from applire.mcp.server import analyze_jd

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), role_title="Backend Engineer")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.job_svc.analyze_jd", AsyncMock(return_value=mock_result)),
    ):
        result = await analyze_jd(text="Senior Backend Engineer at Acme GmbH")

    assert result["role_title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_analyze_jd_empty_text_raises():
    from applire.mcp.server import analyze_jd

    with pytest.raises(McpError) as exc_info:
        await analyze_jd(text="   ")

    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_analyze_jd_carries_duplicate_of_hint():
    """MCP mirror of the Branch F enrichment (E039/US220) — the agent channel
    must see the same repost hint as the UI."""
    from datetime import datetime, timezone

    from applire.mcp.server import analyze_jd
    from applire.schemas.application import DuplicateOfHint
    from applire.schemas.job import JobAnalysisResponse

    cm, _ = _mock_db()
    job_id = uuid.uuid4()
    analysis = JobAnalysisResponse(
        id=job_id,
        role_title="Backend Engineer",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
        raw_text_hash="abc",
    )
    hint = DuplicateOfHint(
        application_id=uuid.uuid4(),
        job_analysis_id=job_id,
        company_name="Acme GmbH",
        role_title="Backend Engineer",
        analyzed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        matched_on="job",
    )

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.job_svc.analyze_jd", AsyncMock(return_value=analysis)),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch(
            "applire.mcp.server.app_svc.find_duplicate_application",
            AsyncMock(return_value=hint),
        ),
    ):
        result = await analyze_jd(text="Senior Backend Engineer at Acme GmbH")

    assert result["duplicate_of"]["matched_on"] == "job"
    assert result["duplicate_of"]["company_name"] == "Acme GmbH"


@pytest.mark.asyncio
async def test_analyze_jd_without_user_still_succeeds():
    """No user yet (fresh install) — the hint is skipped, analysis still returns."""
    from applire.mcp.server import analyze_jd
    from applire.schemas.job import JobAnalysisResponse

    cm, _ = _mock_db()
    analysis = JobAnalysisResponse(
        id=uuid.uuid4(),
        role_title="Backend Engineer",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
        raw_text_hash="abc",
    )

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.job_svc.analyze_jd", AsyncMock(return_value=analysis)),
        patch(
            "applire.mcp.server._current_user_id",
            AsyncMock(side_effect=Exception("no user")),
        ),
    ):
        result = await analyze_jd(text="Senior Backend Engineer at Acme GmbH")

    assert result["role_title"] == "Backend Engineer"
    assert result["duplicate_of"] is None


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_happy_path():
    from applire.mcp.server import get_profile

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), completeness=80)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.profile_svc.get_profile", AsyncMock(return_value=mock_result)),
    ):
        result = await get_profile()

    assert result["completeness"] == 80


@pytest.mark.asyncio
async def test_get_profile_no_profile_raises():
    from applire.mcp.server import get_profile

    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.profile_svc.get_profile", AsyncMock(return_value=None)),
    ):
        with pytest.raises(McpError) as exc_info:
            await get_profile()

    assert exc_info.value.error.code == -32001


# ---------------------------------------------------------------------------
# update_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_profile_happy_path():
    from applire.mcp.server import update_profile

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), completeness=90)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.profile_svc.patch_profile_section",
            AsyncMock(return_value=mock_result),
        ),
    ):
        result = await update_profile(section="skills", data=["Python", "FastAPI"])

    assert result["completeness"] == 90


@pytest.mark.asyncio
async def test_update_profile_invalid_section_raises():
    from applire.mcp.server import update_profile

    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.profile_svc.patch_profile_section",
            AsyncMock(side_effect=ValueError("Invalid section 'bad_section'")),
        ),
    ):
        with pytest.raises(McpError) as exc_info:
            await update_profile(section="bad_section", data={})

    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_update_profile_no_profile_raises():
    from applire.mcp.server import update_profile

    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.profile_svc.patch_profile_section",
            AsyncMock(side_effect=LookupError("No profile found")),
        ),
    ):
        with pytest.raises(McpError) as exc_info:
            await update_profile(section="skills", data=[])

    assert exc_info.value.error.code == -32001


# ---------------------------------------------------------------------------
# analyze_gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_gaps_happy_path():
    from applire.mcp.server import analyze_gaps

    job_id = str(uuid.uuid4())
    cm, _ = _mock_db()
    mock_result = _mock_result(match_score=72, critical_gaps=["Kubernetes"])

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.gap_svc.analyze_gaps", AsyncMock(return_value=mock_result)),
    ):
        result = await analyze_gaps(job_id=job_id)

    assert result["match_score"] == 72


@pytest.mark.asyncio
async def test_analyze_gaps_invalid_uuid_raises():
    from applire.mcp.server import analyze_gaps

    with pytest.raises(McpError) as exc_info:
        await analyze_gaps(job_id="not-a-uuid")

    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_analyze_gaps_job_not_found_raises():
    from applire.mcp.server import analyze_gaps

    job_id = str(uuid.uuid4())
    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch(
            "applire.mcp.server.gap_svc.analyze_gaps",
            AsyncMock(side_effect=LookupError("Job analysis not found")),
        ),
    ):
        with pytest.raises(McpError) as exc_info:
            await analyze_gaps(job_id=job_id)

    assert exc_info.value.error.code == -32001


# ---------------------------------------------------------------------------
# run_interview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_interview_happy_path():
    from applire.mcp.server import run_interview

    job_id = str(uuid.uuid4())
    session_id = uuid.uuid4()
    cm, _ = _mock_db()
    mock_result = _mock_result(
        session_id=str(session_id),
        question="Describe your experience with Kubernetes.",
        gaps_total=3,
        gaps_remaining=3,
    )

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch(
            "applire.mcp.server.session_svc.create_session",
            AsyncMock(return_value=mock_result),
        ),
    ):
        result = await run_interview(job_id=job_id)

    assert "session_id" in result
    assert "question" in result


@pytest.mark.asyncio
async def test_run_interview_invalid_uuid_raises():
    from applire.mcp.server import run_interview

    with pytest.raises(McpError) as exc_info:
        await run_interview(job_id="bad-uuid")

    assert exc_info.value.error.code == -32602


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_returns_next_question():
    from applire.mcp.server import send_message

    session_id = str(uuid.uuid4())
    cm, _ = _mock_db()
    mock_result = _mock_result(complete=False, question="What was your team size?", gaps_remaining=2)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch(
            "applire.mcp.server.session_svc.send_message",
            AsyncMock(return_value=mock_result),
        ),
    ):
        result = await send_message(session_id=session_id, message="I used Kubernetes for 2 years.")

    assert result["complete"] is False


@pytest.mark.asyncio
async def test_send_message_returns_complete():
    from applire.mcp.server import send_message

    session_id = str(uuid.uuid4())
    cm, _ = _mock_db()
    mock_result = _mock_result(complete=True, question=None, gaps_remaining=None)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch(
            "applire.mcp.server.session_svc.send_message",
            AsyncMock(return_value=mock_result),
        ),
    ):
        result = await send_message(session_id=session_id, message="done")

    assert result["complete"] is True


@pytest.mark.asyncio
async def test_send_message_empty_message_raises():
    from applire.mcp.server import send_message

    with pytest.raises(McpError) as exc_info:
        await send_message(session_id=str(uuid.uuid4()), message="  ")

    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_send_message_already_complete_raises():
    from applire.mcp.server import send_message

    session_id = str(uuid.uuid4())
    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch(
            "applire.mcp.server.session_svc.send_message",
            AsyncMock(side_effect=ValueError("Session is already complete")),
        ),
    ):
        with pytest.raises(McpError) as exc_info:
            await send_message(session_id=session_id, message="hello")

    assert exc_info.value.error.code == -32602


# ---------------------------------------------------------------------------
# generate_cv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_cv_happy_path():
    from applire.mcp.server import generate_cv

    job_id = str(uuid.uuid4())
    cv_id = uuid.uuid4()
    cm, _ = _mock_db()
    mock_result = _mock_result(
        cv_id=str(cv_id),
        html_url=f"http://localhost:8001/api/cv/{cv_id}/html",
        pdf_url=f"http://localhost:8001/api/cv/{cv_id}/pdf",
    )

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.cv_svc.generate_cv", AsyncMock(return_value=mock_result)),
    ):
        result = await generate_cv(job_id=job_id)

    assert "cv_id" in result
    assert "html_url" in result
    assert "pdf_url" in result


@pytest.mark.asyncio
async def test_generate_cv_invalid_uuid_raises():
    from applire.mcp.server import generate_cv

    with pytest.raises(McpError) as exc_info:
        await generate_cv(job_id="not-a-uuid")

    assert exc_info.value.error.code == -32602


# ---------------------------------------------------------------------------
# _current_user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_user_id_returns_uuid():
    import uuid as _uuid
    from applire.mcp.server import _current_user_id

    cm, session = _mock_db()
    uid = _uuid.uuid4()
    user_row = MagicMock(); user_row.id = uid
    res = MagicMock(); res.scalar_one_or_none.return_value = user_row
    session.execute = AsyncMock(return_value=res)

    async with cm as db:
        assert await _current_user_id(db) == uid


@pytest.mark.asyncio
async def test_current_user_id_no_user_raises():
    from applire.mcp.server import _current_user_id

    cm, session = _mock_db()
    res = MagicMock(); res.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=res)

    with pytest.raises(McpError) as exc:
        async with cm as db:
            await _current_user_id(db)
    assert exc.value.error.code == -32001


# ---------------------------------------------------------------------------
# get_cv_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cv_status_happy_path():
    from applire.mcp.server import get_cv_status

    cm, _ = _mock_db()
    cid = str(uuid.uuid4())
    mock_result = _mock_result(cv_id=cid, status="ready", pdf_url="http://x/api/cv/%s/pdf" % cid)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.cv_svc.get_cv_status", AsyncMock(return_value=mock_result)),
    ):
        result = await get_cv_status(cv_id=cid)
    assert result["status"] == "ready"


@pytest.mark.asyncio
async def test_get_cv_status_bad_uuid_raises():
    from applire.mcp.server import get_cv_status
    with pytest.raises(McpError) as exc:
        await get_cv_status(cv_id="not-a-uuid")
    assert exc.value.error.code == -32602


# ---------------------------------------------------------------------------
# get_cv_ats_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cv_ats_report_returns_report():
    from applire.mcp.server import get_cv_ats_report

    cm, _ = _mock_db()
    cid = str(uuid.uuid4())
    mock_result = _mock_result(
        document_id=cid,
        status="ready",
        report={"document": "cv", "checks": [], "keywords": {"present": [], "missing": []}},
    )

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.cv_svc.get_cv_ats_report", AsyncMock(return_value=mock_result)),
    ):
        result = await get_cv_ats_report(cv_id=cid)

    assert result["status"] == "ready"
    assert result["report"]["document"] == "cv"


@pytest.mark.asyncio
async def test_get_cv_ats_report_unknown_id_raises():
    from applire.mcp.server import get_cv_ats_report

    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.cv_svc.get_cv_ats_report",
            AsyncMock(side_effect=LookupError("CV not found")),
        ),
    ):
        with pytest.raises(McpError) as exc:
            await get_cv_ats_report(cv_id=str(uuid.uuid4()))

    assert exc.value.error.code == -32001


@pytest.mark.asyncio
async def test_get_cv_ats_report_bad_uuid_raises():
    from applire.mcp.server import get_cv_ats_report

    with pytest.raises(McpError) as exc:
        await get_cv_ats_report(cv_id="not-a-uuid")
    assert exc.value.error.code == -32602


# ---------------------------------------------------------------------------
# start_flow / advance_flow / get_flow_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_happy_path():
    from applire.mcp.server import start_flow

    cm, session = _mock_db()
    uid = uuid.uuid4()
    user_row = MagicMock(); user_row.id = uid
    ures = MagicMock(); ures.scalar_one_or_none.return_value = user_row
    session.execute = AsyncMock(return_value=ures)
    mock_result = _mock_result(flow_id=str(uuid.uuid4()), user_type="returning")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.create_flow", AsyncMock(return_value=mock_result)),
    ):
        result = await start_flow(job_id=str(uuid.uuid4()))
    assert "flow_id" in result


@pytest.mark.asyncio
async def test_advance_flow_invalid_transition_maps_to_invalid_input():
    from applire.mcp.server import advance_flow
    from applire.services.flow.orchestrator import InvalidTransitionError

    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.advance_flow",
              AsyncMock(side_effect=InvalidTransitionError("jd_analysis", "complete", ["cv_import"]))),
    ):
        with pytest.raises(McpError) as exc:
            await advance_flow(flow_id=str(uuid.uuid4()), step="complete")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_get_flow_state_not_found():
    from applire.mcp.server import get_flow_state

    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.get_flow_state",
              AsyncMock(side_effect=LookupError("Flow x not found"))),
    ):
        with pytest.raises(McpError) as exc:
            await get_flow_state(flow_id=str(uuid.uuid4()))
    assert exc.value.error.code == -32001


@pytest.mark.asyncio
async def test_start_flow_without_job_id():
    from applire.mcp.server import start_flow

    cm, session = _mock_db()
    user_row = MagicMock(); user_row.id = uuid.uuid4()
    ures = MagicMock(); ures.scalar_one_or_none.return_value = user_row
    session.execute = AsyncMock(return_value=ures)
    mock_result = _mock_result(flow_id=str(uuid.uuid4()), user_type="new")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.create_flow", AsyncMock(return_value=mock_result)) as create,
    ):
        result = await start_flow()
    assert "flow_id" in result
    # job_id omitted -> CreateFlowRequest.job_id is None
    assert create.call_args.args[0].job_id is None


@pytest.mark.asyncio
async def test_advance_flow_artifact_required_maps_to_invalid_input():
    from applire.mcp.server import advance_flow
    from applire.services.flow.orchestrator import ArtifactRequiredError

    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.advance_flow",
              AsyncMock(side_effect=ArtifactRequiredError("gap_analysis", "gap_analysis_id"))),
    ):
        with pytest.raises(McpError) as exc:
            await advance_flow(flow_id=str(uuid.uuid4()), step="gap_analysis")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_advance_flow_not_found_maps_to_not_found():
    from applire.mcp.server import advance_flow

    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.advance_flow",
              AsyncMock(side_effect=LookupError("Flow x not found"))),
    ):
        with pytest.raises(McpError) as exc:
            await advance_flow(flow_id=str(uuid.uuid4()), step="complete")
    assert exc.value.error.code == -32001


# ---------------------------------------------------------------------------
# create_application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_application_happy_path():
    from applire.mcp.server import create_application

    cm, session = _mock_db()
    uid = uuid.uuid4()
    user_row = MagicMock(); user_row.id = uid
    ures = MagicMock(); ures.scalar_one_or_none.return_value = user_row
    session.execute = AsyncMock(return_value=ures)
    mock_result = _mock_result(id=str(uuid.uuid4()), company_name="Acme GmbH")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.app_svc.create_application", AsyncMock(return_value=mock_result)),
    ):
        result = await create_application(job_id=str(uuid.uuid4()), start_workflow=True)
    assert result["company_name"] == "Acme GmbH"


@pytest.mark.asyncio
async def test_create_application_duplicate_reuses_existing():
    """Idempotent contract at the MCP seam: re-creating for the same (user, job)
    returns the reused application rather than surfacing a conflict. This is the
    agent session-recovery path — a duplicate create must not error.
    """
    from applire.mcp.server import create_application

    cm, session = _mock_db()
    uid = uuid.uuid4()
    user_row = MagicMock(); user_row.id = uid
    ures = MagicMock(); ures.scalar_one_or_none.return_value = user_row
    session.execute = AsyncMock(return_value=ures)

    existing_id = str(uuid.uuid4())
    # The service is idempotent: the second create returns the existing record.
    reused = _mock_result(id=existing_id, company_name="Acme GmbH")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.app_svc.create_application", AsyncMock(return_value=reused)),
    ):
        result = await create_application(job_id=str(uuid.uuid4()))

    assert result["id"] == existing_id


# ---------------------------------------------------------------------------
# import_from_text (profile service wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_from_text_wrapper_rejects_empty():
    from applire.services.profile import import_from_text
    with pytest.raises(ValueError):
        await import_from_text("   ", db=MagicMock(), provider=MagicMock())


# ---------------------------------------------------------------------------
# import_cv tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_cv_base64_happy_path():
    import base64
    from applire.mcp.server import import_cv

    cm, _ = _mock_db()
    profile = MagicMock()
    profile.model_dump.return_value = {
        "id": str(uuid.uuid4()),
        "profile": {"work_experience": [{"id": "w1"}], "skills": ["python"]},
        "completeness": 0.8,
        "stats": {"positions": 1, "projects": 0, "certifications": 0, "data_points": 5},
        "merge_conflicts": [],
    }
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.profile_svc.import_from_pdf", AsyncMock(return_value=profile)),
    ):
        out = await import_cv(file_base64=base64.b64encode(b"%PDF-1.4 fake").decode())
    assert out["positions"] == 1
    assert out["completeness"] == 0.8
    assert out["skills_count"] == 1
    assert "profile" not in out  # black-box: never the raw profile


@pytest.mark.asyncio
async def test_import_cv_invalid_base64_raises():
    from applire.mcp.server import import_cv
    with pytest.raises(McpError) as exc:
        await import_cv(file_base64="!!!not base64!!!")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_import_cv_oversize_points_to_rest():
    import base64
    from applire.mcp.server import import_cv, MAX_CV_BYTES
    big = base64.b64encode(b"x" * (MAX_CV_BYTES + 1)).decode()
    with pytest.raises(McpError) as exc:
        await import_cv(file_base64=big)
    assert exc.value.error.code == -32602
    assert "profile/upload" in exc.value.error.message


@pytest.mark.asyncio
async def test_import_cv_requires_input():
    from applire.mcp.server import import_cv
    with pytest.raises(McpError) as exc:
        await import_cv()
    assert exc.value.error.code == -32602


# ---------------------------------------------------------------------------
# ProfileMetadata Literal regression — cv_paste must be accepted
# ---------------------------------------------------------------------------


def test_profile_metadata_accepts_cv_paste():
    from applire.schemas.profile import ProfileMetadata
    # Must not raise — import_cv's text fallback relies on this created_via value.
    meta = ProfileMetadata(created_via="cv_paste")
    assert meta.created_via == "cv_paste"


# ---------------------------------------------------------------------------
# import_cv text-path happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_cv_text_happy_path():
    from applire.mcp.server import import_cv
    cm, _ = _mock_db()
    profile = MagicMock()
    profile.model_dump.return_value = {
        "id": str(uuid.uuid4()),
        "profile": {"skills": ["python", "fastapi"]},
        "completeness": 0.6,
        "stats": {"positions": 0, "projects": 0, "certifications": 0, "data_points": 3},
        "merge_conflicts": [],
    }
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.profile_svc.import_from_text", AsyncMock(return_value=profile)),
    ):
        out = await import_cv(text="Senior Python Engineer with 10 years experience")
    assert out["skills_count"] == 2
    assert "profile" not in out


# ---------------------------------------------------------------------------
# add_role_to_profile service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_role_to_profile_persists_and_returns_response():
    from applire.services.profile.role_add import add_role_to_profile
    from applire.schemas.profile_roles import AddRoleRequest

    cm, session = _mock_db()
    record = MagicMock()
    record.id = uuid.uuid4()
    record.profile_json = {"work_experience": [], "skills": []}
    res = MagicMock(); res.scalar_one_or_none.return_value = record
    session.execute = AsyncMock(return_value=res)
    session.commit = AsyncMock()

    req = AddRoleRequest(
        title="QA Director", company="Acme GmbH", start_date="2026-05-01", source="manual",
    )
    with patch("applire.services.profile.role_add.MasterProfileData") as MPD, \
         patch("applire.services.profile.role_add.apply_add_role") as apply_mock:
        prof = MagicMock(); prof.model_dump.return_value = {}; prof.calculate_completeness.return_value = 0.9
        MPD.model_validate.return_value = prof
        outcome = MagicMock(); outcome.profile = prof; outcome.new_role_id = "w-new"; outcome.closed_role_ids = []
        apply_mock.return_value = outcome

        async with cm as db:
            resp = await add_role_to_profile(req, db)
    assert resp.new_role_id == "w-new"
    assert resp.completeness_score == 0.9
    session.commit.assert_called_once()
    assert record.profile_json == {}  # outcome.profile.model_dump() returned {}


# ---------------------------------------------------------------------------
# add_role MCP tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_role_tool_happy_path():
    from applire.mcp.server import add_role

    cm, _ = _mock_db()
    mock_result = _mock_result(profile_id="p1", new_role_id="w-new", closed_role_ids=[], completeness_score=0.9)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.add_role_to_profile", AsyncMock(return_value=mock_result)),
    ):
        out = await add_role(title="QA Director", company="Acme GmbH", start_date="2026-05-01")
    assert out["new_role_id"] == "w-new"


@pytest.mark.asyncio
async def test_add_role_tool_bad_start_date_raises():
    from applire.mcp.server import add_role
    with pytest.raises(McpError) as exc:
        await add_role(title="X", company="Y", start_date="01.05.2026")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_add_role_tool_computes_close_end_date():
    from applire.mcp.server import add_role
    captured = {}

    async def fake_service(req, db):
        captured["req"] = req
        return _mock_result(profile_id="p1", new_role_id="w", closed_role_ids=["w0"], completeness_score=1.0)

    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.add_role_to_profile", fake_service),
    ):
        await add_role(title="X", company="Y", start_date="2026-05-01", close_role_ids=["w0"])
    assert captured["req"].close_roles[0].end_date == "2026-04-30"


@pytest.mark.asyncio
async def test_add_role_tool_no_profile_raises_not_found():
    from applire.mcp.server import add_role
    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.add_role_to_profile",
              AsyncMock(side_effect=LookupError("No master profile found"))),
    ):
        with pytest.raises(McpError) as exc:
            await add_role(title="X", company="Y", start_date="2026-05-01")
    assert exc.value.error.code == -32001  # not_found


@pytest.mark.asyncio
async def test_add_role_tool_validation_error_maps_to_invalid_input():
    from applire.mcp.server import add_role
    from applire.services.profile.role_add import AddRoleValidationError
    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.add_role_to_profile",
              AsyncMock(side_effect=AddRoleValidationError("unknown role_id: w9"))),
    ):
        with pytest.raises(McpError) as exc:
            await add_role(title="X", company="Y", start_date="2026-05-01", close_role_ids=["w9"])
    assert exc.value.error.code == -32602  # invalid_input


# ---------------------------------------------------------------------------
# analyze_jd — URL scraping (US056)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_jd_url_scrapes_then_analyzes():
    from applire.mcp.server import analyze_jd

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), role_title="QA Manager")
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.scrape_job_url", AsyncMock(return_value="scraped JD body")),
        patch("applire.mcp.server.job_svc.analyze_jd", AsyncMock(return_value=mock_result)) as analyze,
    ):
        result = await analyze_jd(url="https://jobs.example.com/123")
    assert result["role_title"] == "QA Manager"
    assert analyze.call_args.kwargs["source_url"] == "https://jobs.example.com/123"


@pytest.mark.asyncio
async def test_analyze_jd_scrape_failure_maps_to_invalid_input():
    from applire.mcp.server import analyze_jd
    from applire.services.scraper import ScraperError

    with (
        patch("applire.mcp.server.scrape_job_url", AsyncMock(side_effect=ScraperError("https://jobs.example.com/x", "403 Forbidden"))),
        patch("applire.mcp.server.get_provider"),
    ):
        with pytest.raises(McpError) as exc:
            await analyze_jd(url="https://jobs.example.com/x")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_analyze_jd_requires_text_or_url():
    from applire.mcp.server import analyze_jd
    with pytest.raises(McpError) as exc:
        await analyze_jd()
    assert exc.value.error.code == -32602


# ---------------------------------------------------------------------------
# Tool registration smoke test
# ---------------------------------------------------------------------------


def test_all_agent_tools_registered():
    from applire.mcp import server as srv
    expected = {
        "analyze_jd", "analyze_gaps", "get_profile", "update_profile",
        "run_interview", "send_message", "generate_cv", "get_cv_status",
        "get_cv_ats_report",
        "start_flow", "advance_flow", "get_flow_state",
        "import_cv", "add_role", "create_application",
        "list_applications", "get_application", "update_application",
    }
    for name in expected:
        assert hasattr(srv, name), f"tool {name} not defined"


# ---------------------------------------------------------------------------
# E039 / US218 — status pipeline at the MCP seam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_applications_accepts_interviewing_filter():
    from applire.mcp.server import list_applications

    cm, session = _mock_db()
    user_row = MagicMock(); user_row.id = uuid.uuid4()
    ures = MagicMock(); ures.scalar_one_or_none.return_value = user_row
    session.execute = AsyncMock(return_value=ures)
    svc_result = MagicMock(); svc_result.items = []

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.app_svc.list_applications", AsyncMock(return_value=svc_result)) as svc,
    ):
        result = await list_applications(status_filter="interviewing")

    assert result == []
    from applire.models.application import UserStatus
    assert svc.call_args.kwargs["user_status"] == UserStatus.interviewing


@pytest.mark.asyncio
async def test_list_applications_invalid_filter_lists_all_enum_values():
    """The error message is derived from the enum, not a hard-coded list
    (the old literal was already stale — it lacked 'hired')."""
    from applire.mcp.server import list_applications

    with pytest.raises(McpError) as exc:
        await list_applications(status_filter="bogus")

    assert exc.value.error.code == -32602
    msg = exc.value.error.message
    for value in ("tracking", "applied", "interviewing", "offer", "rejected", "hired"):
        assert value in msg, f"{value!r} missing from error message: {msg}"


# ---------------------------------------------------------------------------
# E039 — update_application (MCP mirror of PATCH /api/applications/{id})
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_application_sets_status():
    from applire.mcp.server import update_application

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), user_status="interviewing")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch("applire.mcp.server.app_svc.patch_application", AsyncMock(return_value=mock_result)) as svc,
    ):
        result = await update_application(
            application_id=str(uuid.uuid4()), user_status="interviewing"
        )

    assert result["user_status"] == "interviewing"
    from applire.models.application import UserStatus
    req = svc.call_args.args[2]
    assert req.user_status == UserStatus.interviewing
    # Omitted fields must not be marked as provided (clear-semantics seam):
    assert req.model_fields_set == {"user_status"}


@pytest.mark.asyncio
async def test_update_application_dossier_fields_pass_through():
    from applire.mcp.server import update_application

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()))

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch("applire.mcp.server.app_svc.patch_application", AsyncMock(return_value=mock_result)) as svc,
    ):
        await update_application(
            application_id=str(uuid.uuid4()),
            notes="Recruiter called back",
            deadline="2026-08-01T00:00:00",
            source_url="https://jobs.example.com/123",
        )

    req = svc.call_args.args[2]
    assert req.notes == "Recruiter called back"
    assert req.source_url == "https://jobs.example.com/123"
    assert req.deadline.year == 2026 and req.deadline.month == 8
    assert req.model_fields_set == {"notes", "deadline", "source_url"}


@pytest.mark.asyncio
async def test_update_application_submitted_pins_pass_through():
    """E039/US219: the MCP tool forwards the submitted pins as UUIDs, marking
    only the provided pin as set (omitted ≠ explicit null)."""
    from applire.mcp.server import update_application

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()))
    cv_id = uuid.uuid4()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch("applire.mcp.server.app_svc.patch_application", AsyncMock(return_value=mock_result)) as svc,
    ):
        await update_application(
            application_id=str(uuid.uuid4()),
            submitted_cv_id=str(cv_id),
        )

    req = svc.call_args.args[2]
    assert req.submitted_cv_id == cv_id
    assert req.model_fields_set == {"submitted_cv_id"}


@pytest.mark.asyncio
async def test_update_application_dismiss_stale_cv_passes_through():
    """E039/US221: an agent can dismiss the stale-CV nudge — the flag reaches
    PatchApplicationRequest as a provided field."""
    from applire.mcp.server import update_application

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), stale_cv=None)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch("applire.mcp.server.app_svc.patch_application", AsyncMock(return_value=mock_result)) as svc,
    ):
        result = await update_application(
            application_id=str(uuid.uuid4()),
            dismiss_stale_cv=True,
        )

    assert result["stale_cv"] is None
    req = svc.call_args.args[2]
    assert req.dismiss_stale_cv is True
    assert req.model_fields_set == {"dismiss_stale_cv"}


@pytest.mark.asyncio
async def test_update_application_invalid_status_lists_enum_values():
    from applire.mcp.server import update_application

    with pytest.raises(McpError) as exc:
        await update_application(
            application_id=str(uuid.uuid4()), user_status="ghosted"
        )

    assert exc.value.error.code == -32602
    assert "interviewing" in exc.value.error.message


@pytest.mark.asyncio
async def test_update_application_no_fields_is_invalid_input():
    from applire.mcp.server import update_application

    with pytest.raises(McpError) as exc:
        await update_application(application_id=str(uuid.uuid4()))

    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_update_application_bad_deadline_is_invalid_input():
    from applire.mcp.server import update_application

    with pytest.raises(McpError) as exc:
        await update_application(
            application_id=str(uuid.uuid4()), deadline="next Tuesday"
        )

    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_update_application_not_found():
    from applire.mcp.server import update_application

    cm, _ = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch("applire.mcp.server.app_svc.patch_application",
              AsyncMock(side_effect=LookupError("Application x not found"))),
    ):
        with pytest.raises(McpError) as exc:
            await update_application(
                application_id=str(uuid.uuid4()), user_status="applied"
            )
    assert exc.value.error.code == -32001
