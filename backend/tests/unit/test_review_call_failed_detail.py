# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""786:231 — REVIEW_CALL_FAILED names the failed call's PII-free message and the
effective LLM_TIMEOUT, so a ~90 s timeout under LLM_TIMEOUT=180 is attributable.

Seam: the real ``review_and_refine`` reviewer and corrector call sites (they log
from inside their ``except`` blocks), plus the logger's own PII rule.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from applire.config import settings
from applire.exceptions import LLMResponseParseError, LLMTimeoutError
from applire.providers.llm.debug_log import log_review_call_failed
from applire.services.reviewer import review_and_refine

_BLOCKING = {"approved": False, "issues": [{"severity": "blocking", "issue": "the draft says X twice"}], "feedback": "fix"}


async def _loop(provider):
    return await review_and_refine(
        source="src", draft={"d": 1},
        generator_prompt_fn=lambda d, f, s: "retry", generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review", reviewer_system="rev",
        provider=provider, max_retries=2, chain_id="cv_tailoring",
    )


def _failed_lines(caplog):
    return [r.getMessage() for r in caplog.records if "REVIEW_CALL_FAILED" in r.getMessage()]


@pytest.mark.asyncio
async def test_seam_reviewer_timeout_logs_message_and_effective_timeout(caplog, monkeypatch):
    monkeypatch.setattr(settings, "llm_timeout", 180)
    provider = AsyncMock()
    provider.aparse_json.side_effect = [LLMTimeoutError("Requesty call timed out after 180s")]
    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        assert await _loop(provider) == {"d": 1}
    (line,) = _failed_lines(caplog)
    assert "role=reviewer" in line and "error=LLMTimeoutError" in line
    assert "detail='Requesty call timed out after 180s'" in line
    assert "llm_timeout=180s" in line


@pytest.mark.asyncio
async def test_seam_corrector_parse_failure_logs_position_not_the_reply(caplog):
    provider = AsyncMock()
    secret_reply = '{"text": "Felix Example, born 1990"}{"x": 1}'
    err = LLMResponseParseError("Requesty reply holds a second JSON value after the first", secret_reply, 33)
    # the preamble's arm-c retry (ADR-094 cl. 9) may re-ask once on a parse error
    provider.aparse_json.side_effect = [_BLOCKING, err, err]
    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        await _loop(provider)
    (line,) = _failed_lines(caplog)
    assert "role=generator" in line and "second JSON value" in line
    assert "Felix" not in line  # the reply stays out of the always-on log


def test_a_foreign_exception_logs_its_class_only(caplog):
    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        try:
            raise RuntimeError("Felix Example's CV text")
        except RuntimeError:
            log_review_call_failed("cv_tailoring", "reviewer", 1, "RuntimeError")
    (line,) = _failed_lines(caplog)
    assert "detail=''" in line and "Felix" not in line


def test_outside_an_except_block_there_is_no_detail(caplog):
    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        log_review_call_failed("cv_tailoring", "generator", 1, "WrongShape")
    (line,) = _failed_lines(caplog)
    assert "error=WrongShape" in line and "detail=''" in line
