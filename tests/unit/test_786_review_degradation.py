# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#786 (ADR-081 cl. 9 amended 2026-10-22, Hazelnut build 3, WP-T): a review step of
the writing loop that did not run is RECORDED on the document.

Layers:
1. ``review_and_refine`` — every fail-open exit reports to an open sink (five exits);
   nothing is reported on a clean settle or on exhaustion; no sink → no-op.
2. Per chain, the REAL chain function with a provider that times out on that chain's
   reviewer: ``cv_language`` (``_review_cv_language``).
3. Persistence + door: ``_render_cv_background`` / the letter body persist the record
   in the ready-commit (``[]`` when every step answered), and the status doors read it.
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from applire.exceptions import LLMTimeoutError, LLMTruncatedError
from applire.services.review_degradation import (
    DegradationRecord,
    normalize_stored,
    reason_for,
    record_review_degradation,
    review_degradation_record,
)
from applire.services.reviewer import review_and_refine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_BLOCKING = {"approved": False, "issues": [{"severity": "blocking", "issue": "the draft says X twice"}], "feedback": "fix"}


async def _loop(provider, *, chain_id="chain_x", max_retries=2, **kw):
    return await review_and_refine(
        source="src",
        draft={"d": 1},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=provider,
        max_retries=max_retries,
        chain_id=chain_id,
        **kw,
    )


# ---------------------------------------------------------------- 1. the record


def test_record_folds_identical_steps_and_keeps_first_attempt():
    rec = DegradationRecord()
    rec.add("cv_language", "review", "timeout", 2)
    rec.add("cv_language", "review", "timeout", 1)
    rec.add("cv_language", "correction", "timeout", 1)
    rec.add("cv_language", "review", "malformed", 2)  # same step, another reason: its own line
    assert rec.as_json() == [
        {"check": "cv_language", "stage": "review", "reason": "timeout", "attempt": 1, "count": 2},
        {"check": "cv_language", "stage": "correction", "reason": "timeout", "attempt": 1, "count": 1},
        {"check": "cv_language", "stage": "review", "reason": "malformed", "attempt": 2, "count": 1},
    ]


def test_reason_mapping_never_drops_an_unknown_class():
    assert reason_for("LLMTimeoutError") == "timeout"
    assert reason_for("LLMTruncatedError") == "truncated"
    assert reason_for("JSONDecodeError") == "malformed"
    assert reason_for("WrongShape") == "shape"
    assert reason_for("LLMProviderUnavailableError") == "error"


def test_no_open_sink_is_a_noop():
    record_review_degradation("cv_language", "review", "LLMTimeoutError", 1)  # must not raise
    with review_degradation_record() as rec:
        pass
    assert rec.as_json() == []


def test_normalize_stored_keeps_the_three_states_apart():
    assert normalize_stored(None) is None
    assert normalize_stored([]) == []
    assert normalize_stored(json.dumps([{"check": "cv_language", "stage": "review", "reason": "timeout"}])) == [
        {"check": "cv_language", "stage": "review", "reason": "timeout", "attempt": 1, "count": 1}
    ]
    assert normalize_stored([{"nope": 1}, "x"]) == []


# ------------------------------------------------- 1b. the five fail-open exits


@pytest.mark.asyncio
async def test_reviewer_timeout_is_recorded_as_review_timeout():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [LLMTimeoutError("slow")]
    with review_degradation_record() as rec:
        out = await _loop(provider, chain_id="cv_language")
    assert out == {"d": 1}
    assert rec.as_json() == [
        {"check": "cv_language", "stage": "review", "reason": "timeout", "attempt": 1, "count": 1}
    ]


@pytest.mark.asyncio
async def test_reviewer_malformed_json_is_recorded_as_review_malformed():
    provider = AsyncMock()
    # the prompt-context layer retries a malformed reply once (preamble.py)
    provider.aparse_json.side_effect = [json.JSONDecodeError("bad", "x", 0)] * 2
    with review_degradation_record() as rec:
        await _loop(provider, chain_id="cover_letter")
    assert [(s.check, s.stage, s.reason) for s in rec.steps] == [("cover_letter", "review", "malformed")]


@pytest.mark.asyncio
async def test_corrector_timeout_is_recorded_as_correction_timeout():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [_BLOCKING, LLMTruncatedError("cap")]
    with review_degradation_record() as rec:
        out = await _loop(provider, chain_id="cv_tailoring")
    assert out == {"d": 1}  # last known-good
    assert [(s.check, s.stage, s.reason, s.attempt) for s in rec.steps] == [
        ("cv_tailoring", "correction", "truncated", 1)
    ]


@pytest.mark.asyncio
async def test_corrector_malformed_json_is_recorded_as_correction_malformed():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [_BLOCKING] + [json.JSONDecodeError("bad", "x", 0)] * 2
    with review_degradation_record() as rec:
        await _loop(provider, chain_id="letter_terminal_review")
    assert [(s.check, s.stage, s.reason) for s in rec.steps] == [
        ("letter_terminal_review", "correction", "malformed")
    ]


@pytest.mark.asyncio
async def test_corrector_wrong_shape_is_recorded_as_correction_shape():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [_BLOCKING, {"body": ["a list"]}]
    with review_degradation_record() as rec:
        await _loop(provider, chain_id="cover_letter", draft_shape_ok=lambda d: not isinstance(d.get("body"), list))
    assert [(s.check, s.stage, s.reason) for s in rec.steps] == [("cover_letter", "correction", "shape")]


@pytest.mark.asyncio
async def test_clean_and_exhausted_settles_record_nothing():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [{"approved": True, "issues": []}]
    with review_degradation_record() as rec:
        await _loop(provider)
    assert rec.as_json() == []

    provider = AsyncMock()
    provider.aparse_json.side_effect = [_BLOCKING, {"d": 2}, _BLOCKING, {"d": 3}]
    with review_degradation_record() as rec:
        await _loop(provider, max_retries=2)
    assert rec.as_json() == []  # exhaustion: the loop ran — not a degraded step


# ---------------------------------------------------- 2. per chain (cv_language)


@pytest.mark.asyncio
async def test_seam_cv_language_chain_reviewer_timeout_is_recorded():
    """The real ``_review_cv_language`` with a provider that times out on its reviewer."""
    from applire.services import cv as cv_mod

    provider = AsyncMock()
    provider.aparse_json.side_effect = LLMTimeoutError("Requesty SDK reported timeout")
    draft = {"summary": "Product manager", "work_history": [], "skills": ["Scrum"]}
    with patch.object(cv_mod, "CV_LANGUAGE_REVIEW_MAX_RETRIES", 2), review_degradation_record() as rec:
        out = await cv_mod._review_cv_language(draft, "en", provider)
    assert out["summary"] == "Product manager"
    assert [(s.check, s.stage, s.reason) for s in rec.steps] == [("cv_language", "review", "timeout")]


@pytest.mark.asyncio
async def test_seam_cv_language_recheck_pass_failure_is_recorded():
    """``_terminal_language_recheck`` swallows a failed pass and ships the corrector's
    draft — that, too, is a step that did not run."""
    from applire.services import cv as cv_mod

    class _Trig:
        fired = True
        foreign_prose = ["x"]
        new_skills = []
        cross_language = False

    async def boom(*a, **k):
        raise RuntimeError("provider 502")

    after = {"summary": "Produktmanagerin", "work_history": [], "skills": []}
    with patch.object(cv_mod, "CV_LANGUAGE_REVIEW_MAX_RETRIES", 2), \
         patch.object(cv_mod, "_terminal_language_trigger", return_value=_Trig()), \
         patch.object(cv_mod, "_review_cv_language", side_effect=boom), \
         review_degradation_record() as rec:
        out = await cv_mod._terminal_language_recheck(
            {"summary": "x"}, after, profile_json={}, document_language="de",
            provider=AsyncMock(), keyword_ledger=None, budget=None, cv_id=uuid.uuid4(), round_no=1,
        )
    assert out is after
    assert [(s.check, s.stage, s.reason) for s in rec.steps] == [("cv_language_recheck", "review", "error")]


# ------------------------------------- 3. persistence in the ready-commit + doors

import importlib.util as _ilu  # noqa: E402

# tests/unit is not a package: load the ADR-039 persistence harness by path and reuse
# its fixtures (one DB shape for both suites).
_spec = _ilu.spec_from_file_location(
    "_ats_persistence_harness", Path(__file__).parent / "test_ats_report_persistence.py"
)
_harness = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
_make_ats_report = _harness._make_ats_report
_stub_tailored_data = _harness._stub_tailored_data
db = _harness.db
db_with_cv = _harness.db_with_cv


def _timing_out_target(target_chain: str, seen: list):
    """A ``review_and_refine`` stand-in: the TARGET chain runs the REAL loop against
    a provider whose reviewer times out; every other chain returns its draft."""

    async def fake(**kwargs):
        seen.append(kwargs.get("chain_id"))
        if kwargs.get("chain_id") != target_chain:
            return kwargs["draft"]
        provider = AsyncMock()
        provider.aparse_json.side_effect = LLMTimeoutError("Requesty SDK reported timeout")
        return await review_and_refine(**{**kwargs, "provider": provider, "max_retries": 2})

    return fake


async def _render_cv(ctx, fake_review, pipeline, monkeypatch):
    from applire.models.cv import GeneratedCV

    monkeypatch.setenv("CV_PIPELINE", pipeline)
    session = ctx["db"]
    provider = AsyncMock()
    provider.aparse_json.return_value = _stub_tailored_data()
    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=provider), \
         patch("applire.services.cv.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 2), \
         patch("applire.services.cv.CV_LANGUAGE_REVIEW_MAX_RETRIES", 2), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_ats_report("cv")):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background

        await _render_cv_background(ctx["cv_id"], ctx["job_id"], ctx["profile_id"], "classic_german")
    record = await session.get(GeneratedCV, ctx["cv_id"])
    await session.refresh(record)
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_seam_cv_render_clean_run_persists_an_empty_record(db_with_cv, monkeypatch, pipeline):
    seen: list = []

    async def fake(**kwargs):
        seen.append(kwargs.get("chain_id"))
        return kwargs["draft"]

    record = await _render_cv(db_with_cv, fake, pipeline, monkeypatch)
    assert record.status == "ready"
    assert record.review_degradation == []  # recorded, nothing degraded — never None


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
@pytest.mark.parametrize("chain", ["cv_tailoring", "cv_language", "cv_terminal_review"])
async def test_seam_cv_render_persists_the_degraded_chain(db_with_cv, monkeypatch, pipeline, chain):
    seen: list = []
    record = await _render_cv(db_with_cv, _timing_out_target(chain, seen), pipeline, monkeypatch)
    assert chain in seen, f"{chain} did not run on {pipeline}: {seen}"
    assert record.status == "ready"
    assert {"check": chain, "stage": "review", "reason": "timeout", "attempt": 1, "count": 1} in record.review_degradation


@pytest.mark.asyncio
async def test_seam_cv_status_door_reads_the_record(db_with_cv):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_cv_status

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])
    assert (await get_cv_status(ctx["cv_id"], session, "http://x")).review_degradation is None
    record.review_degradation = [{"check": "cv_language", "stage": "review", "reason": "timeout", "attempt": 1, "count": 3}]
    await session.commit()
    status = await get_cv_status(ctx["cv_id"], session, "http://x")
    assert [s.model_dump() for s in status.review_degradation] == [
        {"check": "cv_language", "stage": "review", "reason": "timeout", "attempt": 1, "count": 3}
    ]


# ------------------------------------------------- 3b. the letter render + door

_lspec = _ilu.spec_from_file_location(
    "_letter_persistence_harness", Path(__file__).parent / "test_docx_ats_report_persistence_letter.py"
)
_lharness = _ilu.module_from_spec(_lspec)
_lspec.loader.exec_module(_lharness)
db_with_cover_letter = _lharness.db_with_cover_letter


async def _render_letter(ctx, fake_review, pipeline, monkeypatch):
    from applire.models.cover_letter import GeneratedCoverLetter

    monkeypatch.setenv("LETTER_PIPELINE", pipeline)
    session = ctx["db"]
    provider = AsyncMock()
    provider.aparse_json.return_value = _lharness._stub_letter_data()
    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_lharness._make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_lharness._make_docx_ats_report()):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background

        await _render_cover_letter_background(ctx["cl_id"], None, ctx["job_id"])
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])
    await session.refresh(cl)
    return cl


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_seam_letter_render_clean_run_persists_an_empty_record(db_with_cover_letter, monkeypatch, pipeline):
    async def fake(**kwargs):
        return kwargs["draft"]

    cl = await _render_letter(db_with_cover_letter, fake, pipeline, monkeypatch)
    assert cl.status == "ready"
    assert cl.review_degradation == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
@pytest.mark.parametrize("chain", ["cover_letter", "letter_terminal_review"])
async def test_seam_letter_render_persists_the_degraded_chain(db_with_cover_letter, monkeypatch, pipeline, chain):
    seen: list = []
    cl = await _render_letter(db_with_cover_letter, _timing_out_target(chain, seen), pipeline, monkeypatch)
    assert chain in seen, seen
    assert cl.status == "ready"
    assert {"check": chain, "stage": "review", "reason": "timeout", "attempt": 1, "count": 1} in cl.review_degradation


@pytest.mark.asyncio
async def test_seam_letter_status_door_reads_the_record(db_with_cover_letter):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_status

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])
    cl.review_degradation = [{"check": "letter_terminal_review", "stage": "correction", "reason": "shape"}]
    await session.commit()
    status = await get_cover_letter_status(ctx["cl_id"], session, "http://x")
    assert [s.model_dump() for s in status.review_degradation] == [
        {"check": "letter_terminal_review", "stage": "correction", "reason": "shape", "attempt": 1, "count": 1}
    ]
