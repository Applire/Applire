# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Unit tests for the developer-only LLM debug-logging wrapper."""

import json
import logging

import pytest

from applire.config import settings
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import (
    llm_log_stage,
    log_review_call_failed,
    log_review_exhausted,
    log_review_verdict,
    set_review_call_meta,
    wrap_provider,
)


class _StubProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(timeout=5)
        self._model = "stub-model"

    async def acomplete(self, prompt, **kwargs):
        return "completion-text"

    async def aparse_json(self, prompt, **kwargs):
        return {"echo": prompt}


def _read_records(tmp_path):
    files = list(tmp_path.glob("*.jsonl"))
    assert files, "no debug log file written"
    return [json.loads(line) for line in files[0].read_text().splitlines()]


def test_wrap_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "llm_debug_log", False)
    inner = _StubProvider()
    assert wrap_provider(inner) is inner


@pytest.mark.asyncio
async def test_logs_full_input_and_output(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))

    provider = wrap_provider(_StubProvider())
    with llm_log_stage("cv_extraction"):
        result = await provider.aparse_json(
            "hello cv", system="be strict", max_tokens=2048, temperature=0.1
        )

    assert result == {"echo": "hello cv"}
    rec = _read_records(tmp_path)[0]
    assert rec["stage"] == "cv_extraction"
    assert rec["method"] == "aparse_json"
    assert rec["prompt"] == "hello cv"
    assert rec["system"] == "be strict"
    assert rec["response"] == {"echo": "hello cv"}
    assert rec["params"]["max_tokens"] == 2048
    assert rec["model"] == "stub-model"
    assert rec["ok"] is True
    assert rec["error"] is None


@pytest.mark.asyncio
async def test_logs_errors_and_reraises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))

    class _Boom(_StubProvider):
        async def acomplete(self, prompt, **kwargs):
            raise RuntimeError("kaboom")

    provider = wrap_provider(_Boom())
    with pytest.raises(RuntimeError, match="kaboom"):
        await provider.acomplete("x")

    rec = _read_records(tmp_path)[0]
    assert rec["ok"] is False
    assert "kaboom" in rec["error"]
    assert rec["stage"] is None  # unlabelled call still logged


# ---------------------------------------------------------------------------
# #264 — review-loop observability: per-call role/attempt on the debug-log
# record, and always-on structured application-log signals for verdict /
# exhaustion / call-failure (so an exhausted review is visible even when the
# PII-bearing debug log is off, e.g. production).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_call_meta_labels_the_debug_log_record(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))

    provider = wrap_provider(_StubProvider())
    with llm_log_stage("cover_letter"):
        set_review_call_meta("reviewer", 1)
        await provider.aparse_json("review this", system="be strict")
        set_review_call_meta("generator", 1)
        await provider.aparse_json("fix this", system="be strict")
        set_review_call_meta(None, None)
        await provider.aparse_json("unrelated call outside any loop")

    rec1, rec2, rec3 = _read_records(tmp_path)
    assert rec1["stage"] == "cover_letter"
    assert rec1["review_role"] == "reviewer"
    assert rec1["review_attempt"] == 1
    assert rec2["review_role"] == "generator"
    assert rec2["review_attempt"] == 1
    # Cleared after the loop — an unrelated call carries no stale role/attempt.
    assert rec3["review_role"] is None
    assert rec3["review_attempt"] is None


def test_log_review_verdict_is_structured_and_greppable(caplog):
    with caplog.at_level(logging.INFO, logger="applire.llm.review"):
        log_review_verdict("cover_letter", 1, 5, approved=False, issues_count=3)

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "REVIEW_VERDICT" in msg
    assert "chain=cover_letter" in msg
    assert "attempt=1/5" in msg
    assert "approved=False" in msg
    assert "issues=3" in msg
    assert caplog.records[0].levelname == "INFO"


def test_log_review_exhausted_is_a_warning_with_the_chain_id(caplog):
    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        log_review_exhausted("cv_tailoring", 5, 2)

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelname == "WARNING"
    msg = rec.getMessage()
    assert "REVIEW_EXHAUSTED" in msg
    assert "chain=cv_tailoring" in msg
    assert "max_retries=5" in msg


def test_log_review_call_failed_names_role_and_error(caplog):
    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        log_review_call_failed("profile_extraction", "reviewer", 2, "LLMTruncatedError")

    msg = caplog.records[0].getMessage()
    assert "REVIEW_CALL_FAILED" in msg
    assert "chain=profile_extraction" in msg
    assert "role=reviewer" in msg
    assert "attempt=2" in msg
    assert "error=LLMTruncatedError" in msg
