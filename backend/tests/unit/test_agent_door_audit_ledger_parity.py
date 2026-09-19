# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Door parity for the truthfulness audit — the agent door's FRESH branch must
hand the Oracle the same job Keyword Ledger the generation path does.

Measured on the dev stack, 2026-09-19, with the skill-chip fix already live
(``1582ca4f``): nulling ``truthfulness_report`` on ``generated_cvs
1b581b82…`` and re-auditing over the MCP ``audit_document`` tool returned
"Produktionscontrolling" ``unbacked`` — "Skill … has no vault evidence." —
while the generation-time self-audit of the identical document grades it
``grounded`` citing the vault's own "Werkscontrolling". Same input, same
vault, two doors, two verdicts, and the agent's copy is the one that PERSISTS
(``_audit_stored_document`` writes the report it just computed).

The fix is the argument, not a second mechanism: ``_audit_stored_document``
loads the ledger through ``cv._latest_keyword_ledger`` — THE ledger read of
the whole CV chain (ADR-048/US203) — and passes it for the CV kind. Load
failure audits against the vault alone and never fails the audit.

One named seam test per call site: this file is the agent door's; the
generation path's lives in
``backend/tests/unit/services/test_oracle_skill_chip_whole_token_grounding.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

PROFILE_JSON = {
    "personal_info": {"name": "Katrin Hoffmann"},
    "skills": [{"name": "Werkscontrolling"}],
    "work_experience": [
        {
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Senior Controllerin",
            "responsibilities": [
                "Acht Jahre Werkscontrolling an zwei Produktionsstandorten."
            ],
        }
    ],
}

TAILORED = {
    "contact": {"name": "Katrin Hoffmann"},
    "skills": ["Werkscontrolling", "Produktionscontrolling"],
    "work_history": [],
}

LEDGER = [
    {
        "concept": "Produktionscontrolling",
        "status": "direct",
        "claimable": True,
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Acht Jahre Werkscontrolling an zwei Produktionsstandorten.",
        "surface_forms": ["Produktionscontrolling", "Werkscontrolling"],
    }
]


class _FakeDB:
    """Just enough session: ``get`` returns the profile row, ``commit`` is a
    no-op. The door's own persistence is exercised by asserting the record
    carries the report afterwards."""

    def __init__(self, profile_json):
        self._profile = SimpleNamespace(profile_json=profile_json)
        self.commits = 0

    async def get(self, model, pk):  # noqa: ARG002 — signature parity only
        return self._profile

    async def commit(self):
        self.commits += 1


def _record():
    return SimpleNamespace(
        id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        job_analysis_id=uuid.uuid4(),
        tailored_data=dict(TAILORED),
        truthfulness_report=None,
    )


async def _audit(monkeypatch, ledger):
    import applire.mcp.server as server

    seen: dict = {}

    async def _fake_latest(db, job_id, *, profile_json=None):
        seen["job_id"] = job_id
        seen["profile_json"] = profile_json
        return ledger

    monkeypatch.setattr(server.cv_svc, "_latest_keyword_ledger", _fake_latest)
    monkeypatch.setattr(server, "get_provider", lambda: None)

    record = _record()
    db = _FakeDB(PROFILE_JSON)
    out = await server._audit_stored_document(record, "cv", db)
    verdicts = {c["claim"]["text"]: c["verdict"]["verdict"] for c in out["claims"]}
    return out, verdicts, record, db, seen


@pytest.mark.asyncio
async def test_agent_door_fresh_audit_grounds_the_ledger_synonym(monkeypatch):
    """The defect: on this door "Produktionscontrolling" came back unbacked."""
    _out, verdicts, record, db, seen = await _audit(monkeypatch, LEDGER)
    assert verdicts["Werkscontrolling"] == "grounded"
    assert verdicts["Produktionscontrolling"] == "grounded"
    # The ledger was read for THIS record's job, with the vault already loaded
    # (no second profile query), exactly as the generation path reads it.
    assert seen["job_id"] == record.job_analysis_id
    assert seen["profile_json"] == PROFILE_JSON
    # The door persists what it computed — which is why a divergence here is
    # not a display bug but a stored one.
    assert record.truthfulness_report is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_without_the_ledger_the_door_reproduces_the_defect(monkeypatch):
    """The baseline this guard rests on, asserted rather than assumed: with no
    ledger the synonym is graded unbacked — so the test above can only pass
    because the argument is actually threaded."""
    _out, verdicts, _record, _db, _seen = await _audit(monkeypatch, None)
    assert verdicts["Werkscontrolling"] == "grounded"
    assert verdicts["Produktionscontrolling"] == "unbacked"


@pytest.mark.asyncio
async def test_a_ledger_load_failure_audits_against_the_vault_alone(monkeypatch):
    """Fail-safe: the ledger is an improvement to the audit, never a
    precondition for it."""
    import applire.mcp.server as server

    async def _boom(db, job_id, *, profile_json=None):
        raise RuntimeError("gap analysis unreachable")

    monkeypatch.setattr(server.cv_svc, "_latest_keyword_ledger", _boom)
    monkeypatch.setattr(server, "get_provider", lambda: None)

    record = _record()
    out = await server._audit_stored_document(record, "cv", _FakeDB(PROFILE_JSON))
    verdicts = {c["claim"]["text"]: c["verdict"]["verdict"] for c in out["claims"]}
    assert verdicts["Werkscontrolling"] == "grounded"
    assert verdicts["Produktionscontrolling"] == "unbacked"


@pytest.mark.asyncio
async def test_a_persisted_report_is_still_served_untouched(monkeypatch):
    """The persisted branch is unchanged: no ledger read, no audit, no write."""
    import applire.mcp.server as server

    async def _must_not_run(*a, **kw):  # pragma: no cover - the assertion IS the test
        raise AssertionError("the persisted branch must not read the ledger")

    monkeypatch.setattr(server.cv_svc, "_latest_keyword_ledger", _must_not_run)
    record = _record()
    record.truthfulness_report = {"version": "1.4", "claims": []}
    db = _FakeDB(PROFILE_JSON)
    out = await server._audit_stored_document(record, "cv", db)
    assert out["version"] == "1.4"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_the_letter_branch_takes_no_ledger(monkeypatch):
    """Scoped to the CV kind, matching the generation path: a letter claim is
    never ``kind == "skill"``, so the ledger arm is unreachable there and the
    letter branch must not pay for a ledger read."""
    import applire.mcp.server as server

    async def _must_not_run(*a, **kw):  # pragma: no cover
        raise AssertionError("the letter branch must not read the ledger")

    monkeypatch.setattr(server.cv_svc, "_latest_keyword_ledger", _must_not_run)
    monkeypatch.setattr(server, "get_provider", lambda: None)

    record = _record()
    record.tailored_data = None
    record.letter_data = {
        "body": {
            "paragraphs": [
                "Bei Schwarzwald Präzision habe ich acht Jahre "
                "Werkscontrolling an zwei Produktionsstandorten verantwortet."
            ]
        }
    }
    out = await server._audit_stored_document(record, "cover_letter", _FakeDB(PROFILE_JSON))
    assert out["document_kind"] == "cover_letter"
