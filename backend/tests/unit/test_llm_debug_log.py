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

import pytest

from applire.config import settings
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage, wrap_provider


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
