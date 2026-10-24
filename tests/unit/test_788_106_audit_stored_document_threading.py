# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""788:106 - the agent door's FRESH audit must hand the Oracle what the
generation-time self-audit hands it: the record's pinned ``document_language``
(else the JD's detected language) and, for a letter, the text with its
``section_overrides`` applied.

One seam test per branch: ``oracle_svc.audit_document`` is replaced by a
capture and the kwargs it receives are asserted.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


class _FakeDB:
    def __init__(self, profile_json, job=None):
        self._profile = SimpleNamespace(profile_json=profile_json)
        self._job = job

    async def get(self, model, pk):  # noqa: ARG002
        from applire.models.job import JobAnalysis

        return self._job if model is JobAnalysis else self._profile

    async def commit(self):
        pass


def _record(**kw):
    base = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        job_analysis_id=uuid.uuid4(),
        tailored_data=None,
        letter_data=None,
        truthfulness_report=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _capture(monkeypatch):
    import applire.mcp.server as server

    seen: dict = {}

    async def _fake_audit(kind, profile_json, **kwargs):
        seen["kind"] = kind
        seen["kwargs"] = kwargs
        return SimpleNamespace(model_dump=lambda mode=None: {"version": "x", "claims": []})

    async def _no_ledger(*a, **kw):
        return None

    monkeypatch.setattr(server.oracle_svc, "audit_document", _fake_audit)
    monkeypatch.setattr(server.cv_svc, "_latest_keyword_ledger", _no_ledger)
    monkeypatch.setattr(server, "get_provider", lambda: None)
    return server, seen


@pytest.mark.asyncio
async def test_cv_branch_threads_the_pinned_document_language(monkeypatch):
    server, seen = _capture(monkeypatch)
    record = _record(tailored_data={"skills": []}, document_language="en")
    await server._audit_stored_document(record, "cv", _FakeDB({}))
    assert seen["kwargs"]["document_language"] == "en"


@pytest.mark.asyncio
async def test_cv_branch_falls_back_to_the_jd_language_when_unpinned(monkeypatch):
    server, seen = _capture(monkeypatch)
    job = SimpleNamespace(jd_language="de", raw_text="")
    record = _record(tailored_data={"skills": []}, document_language=None)
    await server._audit_stored_document(record, "cv", _FakeDB({}, job=job))
    assert seen["kwargs"]["document_language"] == "de"


@pytest.mark.asyncio
async def test_letter_branch_threads_language_and_applies_section_overrides(monkeypatch):
    server, seen = _capture(monkeypatch)
    record = _record(
        letter_data={"body": {"paragraphs": ["original text"]}},
        section_overrides={"body": "edited one\n\nedited two"},
        document_language="en",
    )
    await server._audit_stored_document(record, "cover_letter", _FakeDB({}))
    kw = seen["kwargs"]
    assert kw["document_language"] == "en"
    assert kw["letter_data"]["body"]["paragraphs"] == ["edited one", "edited two"]
    # the stored row itself is not rewritten by the audit
    assert record.letter_data["body"]["paragraphs"] == ["original text"]


@pytest.mark.asyncio
async def test_letter_branch_job_less_unpinned_leaves_the_language_none(monkeypatch):
    server, seen = _capture(monkeypatch)
    record = _record(letter_data={"body": {"paragraphs": ["x"]}}, document_language=None)
    await server._audit_stored_document(record, "cover_letter", _FakeDB({}, job=None))
    assert seen["kwargs"]["document_language"] is None
