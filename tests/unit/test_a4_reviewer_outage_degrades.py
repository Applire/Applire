# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 3, WP-FR (adversarial A-4, ruling MD3-2): a reviewer provider
outage degrades like a timeout, and a failed generation always carries an
``error_message``.

The adversarial pass injected an HTTP 503 (``openai.InternalServerError`` →
``LLMProviderUnavailableError``) into the ``cv_tailoring`` reviewer: the CV
generation FAILED (``status=failed``, ``error_code=generation_failed``,
``error_message=null``) while a timeout on the same seat degraded correctly
(``review_degradation`` + "Neue Fassung erstellen"). ``review_and_refine`` caught
only ``LLMTimeoutError``/``LLMTruncatedError``/``JSONDecodeError``.

Now an outage — ``LLMProviderUnavailableError``, or an unclassified provider error
with a 5xx status or a connection-class SDK error — takes the timeout's exit on both
seats (reviewer: ``reviewer_call_failed``; corrector: ``generator_call_failed``) and is
recorded with reason ``error`` ("the AI provider could not be reached"). A
configuration error (401/402/404) still fails loudly: the next call would fail too.
"""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.exceptions import (  # noqa: E402
    LLMProviderError,
    LLMProviderUnavailableError,
    is_provider_outage,
)
from applire.services.review_degradation import review_degradation_record  # noqa: E402
from applire.services.reviewer import review_and_refine  # noqa: E402

_BLOCKING = {"approved": False, "issues": [{"severity": "blocking", "issue": "the draft says X twice"}], "feedback": "fix"}
_UNAVAILABLE = LLMProviderUnavailableError("Requesty is temporarily unavailable (HTTP 503). Retry the same request.")


async def _loop(provider, *, chain_id="cv_tailoring", max_retries=2, settles=None):
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
        on_settle=(settles.append if settles is not None else None),
    )


def test_the_outage_predicate():
    assert is_provider_outage(_UNAVAILABLE)
    assert is_provider_outage(LLMProviderError("Requesty", sdk_type="InternalServerError", status_code=502))
    assert is_provider_outage(LLMProviderError("Requesty", sdk_type="APIConnectionError"))
    assert not is_provider_outage(LLMProviderError("Requesty", sdk_type="AuthenticationError", status_code=401))
    assert not is_provider_outage(LLMProviderError("Requesty", sdk_type="APIStatusError", status_code=402))
    assert not is_provider_outage(ValueError("not a provider error"))


@pytest.mark.asyncio
async def test_a_reviewer_503_ships_the_draft_and_is_recorded():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [_UNAVAILABLE]
    settles: list = []
    with review_degradation_record() as rec:
        out = await _loop(provider, settles=settles)
    assert out == {"d": 1}
    assert [s.path for s in settles] == ["reviewer_call_failed"]
    assert rec.as_json() == [
        {"check": "cv_tailoring", "stage": "review", "reason": "error", "attempt": 1, "count": 1}
    ]


@pytest.mark.asyncio
async def test_a_corrector_503_keeps_the_last_good_draft_and_is_recorded():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [_BLOCKING, _UNAVAILABLE]
    settles: list = []
    with review_degradation_record() as rec:
        out = await _loop(provider, settles=settles)
    assert out == {"d": 1}
    assert [s.path for s in settles] == ["generator_call_failed"]
    assert [(s.check, s.stage, s.reason) for s in rec.steps] == [("cv_tailoring", "correction", "error")]


@pytest.mark.asyncio
async def test_an_unclassified_5xx_degrades_too():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [
        LLMProviderError("Requesty", sdk_type="InternalServerError", status_code=502)
    ]
    with review_degradation_record() as rec:
        out = await _loop(provider)
    assert out == {"d": 1}
    assert [(s.stage, s.reason) for s in rec.steps] == [("review", "error")]


@pytest.mark.asyncio
async def test_a_configuration_error_still_fails_loudly():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [
        LLMProviderError("Requesty", sdk_type="AuthenticationError", status_code=401)
    ]
    with review_degradation_record() as rec:
        with pytest.raises(LLMProviderError):
            await _loop(provider)
    assert rec.as_json() == []


@pytest.mark.asyncio
async def test_a_corrector_configuration_error_still_fails_loudly():
    provider = AsyncMock()
    provider.aparse_json.side_effect = [
        _BLOCKING, LLMProviderError("Requesty", sdk_type="NotFoundError", status_code=404)
    ]
    with review_degradation_record() as rec:
        with pytest.raises(LLMProviderError):
            await _loop(provider)
    assert rec.as_json() == []


# ------------------------------------------- the delivery point: the CV render

_spec = _ilu.spec_from_file_location(
    "_degradation_harness_a4", Path(__file__).parent / "test_786_review_degradation.py"
)
_h = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_h)
db = _h.db
db_with_cv = _h.db_with_cv
db_with_cover_letter = _h.db_with_cover_letter


def _outage_on(target_chain: str, seen: list):
    """The REAL loop for the target chain, against a provider whose reviewer seat
    answers 503 (the adversarial pass's injection); other chains pass through."""

    async def fake(**kwargs):
        seen.append(kwargs.get("chain_id"))
        if kwargs.get("chain_id") != target_chain:
            return kwargs["draft"]
        provider = AsyncMock()
        provider.aparse_json.side_effect = _UNAVAILABLE
        return await review_and_refine(**{**kwargs, "provider": provider, "max_retries": 2})

    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
@pytest.mark.parametrize("chain", ["cv_tailoring", "cv_terminal_review"])
async def test_seam_cv_render_with_a_reviewer_outage_is_delivered_with_the_note(
    db_with_cv, monkeypatch, pipeline, chain
):
    seen: list = []
    record = await _h._render_cv(db_with_cv, _outage_on(chain, seen), pipeline, monkeypatch)
    assert chain in seen, f"{chain} did not run on {pipeline}: {seen}"
    assert record.status == "ready", (record.error_code, record.error_message)
    assert {"check": chain, "stage": "review", "reason": "error", "attempt": 1, "count": 1} in record.review_degradation


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
@pytest.mark.parametrize("chain", ["cover_letter", "letter_terminal_review"])
async def test_seam_letter_render_with_a_reviewer_outage_is_delivered_with_the_note(
    db_with_cover_letter, monkeypatch, pipeline, chain
):
    seen: list = []
    cl = await _h._render_letter(db_with_cover_letter, _outage_on(chain, seen), pipeline, monkeypatch)
    assert chain in seen, seen
    assert cl.status == "ready", cl.error_message
    assert {"check": chain, "stage": "review", "reason": "error", "attempt": 1, "count": 1} in cl.review_degradation


# --------------------------------------- a failed generation names what failed

def test_an_outage_is_classified_as_its_own_code():
    from applire.services.cv import classify_generation_error

    assert classify_generation_error(_UNAVAILABLE) == "provider_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", [None, "generation_failed", "llm_timeout", "llm_truncated", "provider_unavailable"]
)
async def test_seam_cv_status_door_names_a_failure(db_with_cv, code):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_cv_status, list_cvs_for_job

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])
    record.status = "failed"
    record.error_code = code
    record.error_message = "raw provider text that must stay internal"
    await session.commit()
    status = await get_cv_status(ctx["cv_id"], session, "http://x")
    assert status.error_message, "a failed generation must carry an error_message"
    assert "raw provider text" not in status.error_message  # ADR-047 §4
    listed = await list_cvs_for_job(ctx["job_id"], session, "http://x")
    row = next(r for r in listed if r.cv_id == ctx["cv_id"])
    assert row.error_message == status.error_message


@pytest.mark.asyncio
async def test_seam_cv_status_door_ready_row_has_no_error_message(db_with_cv):
    from applire.services.cv import get_cv_status

    status = await get_cv_status(db_with_cv["cv_id"], db_with_cv["db"], "http://x")
    assert status.status != "failed"
    assert status.error_message is None


@pytest.mark.asyncio
async def test_seam_letter_status_door_names_a_failure_without_a_stored_message(db_with_cover_letter):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_status

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])
    cl.status = "failed"
    cl.error_message = None
    await session.commit()
    status = await get_cover_letter_status(ctx["cl_id"], session, "http://x")
    assert status.error_message
