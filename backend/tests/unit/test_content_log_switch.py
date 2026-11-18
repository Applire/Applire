# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One switch for content logs (ADR-087 cl. 9 amended 2026-11-18).

LOG_LEVEL=DEBUG writes the content lines (their own file, never the stream
handler) and the LLM call log; above DEBUG neither exists. Each line carries
the user id, and a person without a consent is never written.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import pytest

from applire import content_log
from applire.config import settings
from applire.ownership import owner_context, unscoped
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import wrap_provider
from applire.services import allowance

CANARY = "Kanarienvogel-Weberit-Schichtleitung-7731"


class _Stub(LLMProvider):
    def __init__(self) -> None:
        super().__init__(timeout=5)
        self._model = "stub-model"

    async def acomplete(self, prompt, **kwargs):
        return f"reply about {prompt}"

    async def aparse_json(self, prompt, **kwargs):
        return {"echo": prompt}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    content = tmp_path / "content"
    llm = tmp_path / "llm"
    monkeypatch.setattr(settings, "content_log_dir", str(content))
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(llm))
    yield content, llm
    monkeypatch.setattr(settings, "log_level", "INFO")
    content_log.configure_content_logging()


@pytest.fixture
def stream_records():
    """Everything the ``applire`` tree and the root logger receive (the stream path)."""
    seen: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record)

    handler = _H(level=logging.DEBUG)
    loggers = [logging.getLogger("applire"), logging.getLogger()]
    old = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    yield seen
    for lg, lvl in zip(loggers, old):
        lg.removeHandler(handler)
        lg.setLevel(lvl)


def _lines(directory):
    out = []
    for f in sorted(directory.glob("*.jsonl")) if directory.exists() else []:
        out += [json.loads(line) for line in f.read_text("utf-8").splitlines() if line.strip()]
    return out


def _consent(user_id, ok=True):
    return allowance._facts.set(allowance.PayerFacts(owner_id=user_id, mode="instance", log_consent_ok=ok))


def test_above_debug_the_content_tree_is_off_and_nothing_is_written(dirs, monkeypatch, stream_records):
    content, llm = dirs
    monkeypatch.setattr(settings, "log_level", "INFO")
    assert content_log.configure_content_logging() is False
    clog = content_log.content_logger("applire.services.cv")
    clog.debug("skill spelling guard: %r", CANARY)
    assert not content.exists()
    assert all(CANARY not in r.getMessage() for r in stream_records)
    inner = _Stub()
    assert wrap_provider(inner) is inner  # no LLM call log above DEBUG


def test_the_retired_switch_writes_no_llm_log_above_debug(monkeypatch):
    monkeypatch.setattr(settings, "log_level", "WARNING")
    monkeypatch.setattr(settings, "llm_debug_log", True)
    inner = _Stub()
    assert wrap_provider(inner) is inner


def test_at_debug_a_content_line_goes_to_its_file_with_the_user_id_never_to_the_stream(
    dirs, monkeypatch, stream_records
):
    content, _ = dirs
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    assert content_log.configure_content_logging() is True
    person = uuid.uuid4()
    clog = content_log.content_logger("applire.services.cv")
    with owner_context(person):
        token = _consent(person)
        try:
            clog.debug("skill spelling guard: %r has no unambiguous vault original", CANARY)
        finally:
            allowance._facts.reset(token)
    rows = _lines(content)
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == str(person)
    assert row["template"] == "skill spelling guard: %r has no unambiguous vault original"
    assert CANARY in row["message"] and row["logger"] == "applire.content.services.cv"
    assert all(CANARY not in r.getMessage() for r in stream_records)


def test_configure_is_idempotent(dirs, monkeypatch):
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    content_log.configure_content_logging()
    content_log.configure_content_logging()
    root = logging.getLogger(content_log.CONTENT_ROOT)
    assert sum(isinstance(h, content_log.ContentFileHandler) for h in root.handlers) == 1
    assert root.propagate is False


def test_a_person_without_consent_is_never_written(dirs, monkeypatch):
    content, _ = dirs
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    content_log.configure_content_logging()
    clog = content_log.content_logger("applire.services.session")
    person = uuid.uuid4()
    with owner_context(person):
        clog.debug("no facts at all: %s", CANARY)  # no door ran
        token = _consent(person, ok=False)
        try:
            clog.debug("facts say no: %s", CANARY)
        finally:
            allowance._facts.reset(token)
        other = _consent(uuid.uuid4(), ok=True)  # facts of SOMEONE ELSE
        try:
            clog.debug("foreign facts: %s", CANARY)
        finally:
            allowance._facts.reset(other)
    assert _lines(content) == []


def test_ownerless_work_is_written_with_a_null_user(dirs, monkeypatch):
    content, _ = dirs
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    content_log.configure_content_logging()
    clog = content_log.content_logger("applire.services.ops.probes")
    with unscoped("ops-aggregate"):
        clog.debug("probe reply %s", "pong")
    rows = _lines(content)
    assert len(rows) == 1 and rows[0]["user_id"] is None


@pytest.mark.asyncio
async def test_the_llm_call_log_carries_the_user_and_skips_a_person_without_consent(dirs, monkeypatch):
    _, llm = dirs
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    provider = wrap_provider(_Stub())
    yes, no = uuid.uuid4(), uuid.uuid4()
    with owner_context(yes):
        token = _consent(yes)
        try:
            await provider.acomplete(f"profile of {CANARY}")
        finally:
            allowance._facts.reset(token)
    with owner_context(no):
        token = _consent(no, ok=False)
        try:
            await provider.acomplete(f"profile of {CANARY}-no")
        finally:
            allowance._facts.reset(token)
    rows = _lines(llm)
    assert [r["user_id"] for r in rows] == [str(yes)]
    assert CANARY in rows[0]["prompt"]


def test_a_writer_reopens_a_file_the_worker_replaced(tmp_path):
    path = tmp_path / f"{content_log._today()}.jsonl"
    content_log.append_jsonl(str(tmp_path), {"n": 1})
    replacement = tmp_path / "new.tmp"
    replacement.write_text(json.dumps({"n": 0}) + "\n", "utf-8")
    os.replace(replacement, path)
    content_log.append_jsonl(str(tmp_path), {"n": 2})
    assert [json.loads(x)["n"] for x in path.read_text("utf-8").splitlines()] == [0, 2]


def test_a_content_line_scrubs_configured_secrets(dirs, monkeypatch):
    content, _ = dirs
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    content_log.configure_content_logging()
    secret = "sk-content-log-secret-0123456789abcdef"
    monkeypatch.setattr(settings, "mistral_api_key", secret)
    clog = content_log.content_logger("applire.services.job")
    with unscoped("ops-aggregate"):
        clog.debug("reply %s", f"echo {secret}")
    rows = _lines(content)
    assert rows and secret not in rows[0]["message"]


@pytest.fixture
def process_stream():
    """A stream handler set up the way main.py / mcp/__main__.py set theirs up:
    on the root logger at the process level, handed to configure_content_logging."""
    import io

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    root, app = logging.getLogger(), logging.getLogger("applire")
    old = root.level, app.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)  # mcp/__main__.py: the root at LOG_LEVEL
    app.setLevel(logging.DEBUG)  # main.py: the applire logger at LOG_LEVEL
    yield handler, buf
    root.removeHandler(handler)
    root.setLevel(old[0])
    app.setLevel(old[1])


@pytest.mark.parametrize("level", ["DEBUG", "INFO"])
def test_the_stream_carries_info_and_above_only_at_every_level(dirs, monkeypatch, process_stream, level):
    handler, buf = process_stream
    monkeypatch.setattr(settings, "log_level", level)
    content_log.configure_content_logging(handler)
    with unscoped("ops-aggregate"):
        logging.getLogger("openai._base_client").debug("Request options: %s", CANARY)
        logging.getLogger("applire.services.cv").debug("draft: %s", CANARY)
        logging.getLogger("applire.services.cv").info("CV_PIPELINE=v2 for CV %s", "id-1")
    out = buf.getvalue()
    assert CANARY not in out
    assert "CV_PIPELINE=v2" in out


def test_at_debug_an_applire_debug_line_goes_to_the_content_file_with_the_user_id(
    dirs, monkeypatch, process_stream
):
    content, _ = dirs
    handler, buf = process_stream
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    content_log.configure_content_logging(handler)
    person = uuid.uuid4()
    with owner_context(person):
        token = _consent(person)
        try:
            logging.getLogger("applire.services.session").debug("answer: %s", CANARY)
            logging.getLogger("openai._base_client").debug("Request options: %s", CANARY)
        finally:
            allowance._facts.reset(token)
        logging.getLogger("applire.services.session").debug("no consent here: %s", CANARY)
    rows = _lines(content)
    assert [(r["logger"], r["user_id"]) for r in rows] == [("applire.services.session", str(person))]
    assert CANARY not in buf.getvalue()
