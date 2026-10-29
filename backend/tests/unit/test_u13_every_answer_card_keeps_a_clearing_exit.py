# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""U-13 (Hazelnut build 5, delivery run 4) — the KEYWORD-LEDGER card „AI
engineering“ (``keywords.present_unsupported``): "Ja, und zwar bei …" changed
nothing in the vault, the re-audit of its own producer still listed it, and the
card's only exit was "Entfernen". D-10 had closed that only for the Oracle
skill card.

The rule for EVERY class-A card that takes an answer: after a ``no_change``
answer the card's own producer runs again (``reaudit``), and when it still
cannot clear, the card offers the profile + "Jetzt prüfen" + remove — never
remove alone. The kinds come from the registry, never from a hand list: the
surface fixture ``frontend/lib/__tests__/fixtures/adr099-answer-kinds.json`` is
written from it (``APPLIRE_REGEN_ANSWER_KINDS=1``) and the surface test renders
the no-change end state of each listed kind. Synthetic vault; no Docker, no LLM."""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from unittest.mock import patch

import pytest

import applire.services.review_actions as ra
import applire.services.review_state as rs
from applire.services.ats_audit import _keyword_coverage, _norm, grounding_vault_index
from applire.services.finding_registry import _DOC, REGISTRY, RESOLVERS, rendered_class
from tests.support.profile_factory import make_master_profile, retire_live_profiles
from tests.unit.test_review_endpoints import (  # noqa: F401 — `db` is a fixture
    _ats_report,
    _canned_testimony,
    _client,
    db,
    seed_cv,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[3] / "frontend/lib/__tests__/fixtures/adr099-answer-kinds.json"

#: The resolvers whose card takes a free-text answer ("Ja, und zwar …" →
#: add-evidence → testimony). Only these can end in ``no_change``.
ANSWER_RESOLVERS = ("ask_true", "ask_achieved")


def _answer_kinds() -> list[dict]:
    """Every DOCUMENT review-surface kind (the hub's rows render elsewhere)."""
    out = []
    for kind in sorted(_DOC):
        assert REGISTRY[kind] is _DOC[kind]
        cls, resolver = rendered_class(REGISTRY[kind])
        if cls == "A" and resolver in ANSWER_RESOLVERS:
            out.append({"kind": kind, "resolver": resolver})
    return out


def test_answer_resolvers_are_registry_vocabulary():
    assert set(ANSWER_RESOLVERS) <= RESOLVERS


def test_the_answer_card_kinds_the_registry_stamps_are_the_surface_fixture():
    produced = _answer_kinds()
    if os.environ.get("APPLIRE_REGEN_ANSWER_KINDS"):
        FIXTURE.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == produced
    kinds = {r["kind"] for r in produced}
    # the delivery-run card and the D-10 card are both in the enumerated set
    assert {"keyword:present_unsupported", "oracle:skillNoEvidence"} <= kinds


# ── the keyword-ledger card, end to end with its REAL producer ───────────────

TERM = "AI engineering"
SENTENCE = "Combines system architecture and validation experience with current AI engineering, including LangChain, RAG and MCP."
WORK = [{"id": "w1", "company": "Nordlicht Software GmbH", "role": "Lead Engineer", "start_date": "2021-05",
         "achievements": ["Built an agentic document-review system with LangChain, RAG and MCP for validation teams."]}]
SKILLS = [{"id": "s1", "name": "LangChain", "status": "confirmed"}, {"id": "s2", "name": "RAG", "status": "confirmed"}]
LEDGER = [{"concept": TERM, "surface_forms": [TERM], "claimable": False, "status": "gap",
           "sources": ["keyword"], "fit_weight": 0.5, "evidence": ""}]


def _coverage(profile_json: dict):
    """The Keyword Ledger's audit decision — the producer of this card."""
    return _keyword_coverage(_norm(SENTENCE), [TERM, "LangChain"], LEDGER,
                             vault_index=grounding_vault_index(profile_json))


def _report_from(cov) -> dict:
    report = _ats_report("cv", [])
    report["keywords"].update(json.loads(cov.model_dump_json()))
    return report


async def _new_vault(db, record, profile_json: dict) -> None:
    pid = uuid.uuid4()
    await retire_live_profiles(db)
    db.add(make_master_profile(id=pid, profile_json=profile_json))
    record.profile_id = pid
    await db.commit()


async def _ledger_reaudit(kind, rec, session):
    """``reaudit`` with the card's own producer: the keyword decision recomputed
    over the unchanged document against the LIVE vault."""
    from applire.models.profile import MasterProfile

    profile = await session.get(MasterProfile, rec.profile_id)
    rec.ats_report = _report_from(_coverage(profile.profile_json or {}))
    await session.commit()
    await session.refresh(rec)


def test_the_producer_flags_the_term_and_a_profile_skill_clears_it():
    assert _coverage({"work_experience": WORK, "skills": SKILLS}).present_unsupported == [TERM]
    assert _coverage({"work_experience": WORK, "skills": SKILLS + [{"name": TERM}]}).present_unsupported == []


@pytest.mark.asyncio
async def test_no_change_answer_reaudits_the_ledger_then_profile_and_recheck_clear_it(db):
    vault = {"work_experience": WORK, "skills": SKILLS}
    cv_id = await seed_cv(db, introduction=SENTENCE, ats_report=_report_from(_coverage(vault)))
    record = await ra.load_document("cv", cv_id, db)
    await _new_vault(db, record, vault)
    key = rs.finding_key("ats", TERM)
    assert rs.find_listed(ra.findings_of(record), key) is not None, "the card is listed before the answer"

    calls: list[str] = []

    async def counted(kind, rec, session):
        calls.append(kind)
        await _ledger_reaudit(kind, rec, session)

    client = _client(db)
    with patch.object(ra, "reaudit", new=counted), \
            patch("applire.services.profile.reconcile.testimony_bridge.submit_testimony") as submit:
        submit.return_value = _canned_testimony("no_change")
        r = client.post(f"/api/cv/{cv_id}/review/add-evidence",
                        json={"finding_key": key, "text": "Bei Nordlicht: agentisches Review-System mit LangChain gebaut."})
    assert r.status_code == 200, r.text
    # the card's OWN producer ran again, and it still cannot clear the term
    assert calls == ["cv"]
    body = r.json()
    assert body["still_listed"] is True and body["remove_only"] is True
    [marker] = body["review_state"]["decisions"]
    assert marker["action"] == "no_change" and marker["finding_key"] == key
    record = await ra.load_document("cv", cv_id, db)

    # the exit the card now offers: the profile, in the person's words → recheck
    await _new_vault(db, record, {"work_experience": WORK, "skills": SKILLS + [{"id": "s3", "name": TERM, "status": "confirmed"}]})
    with patch.object(ra, "reaudit", new=_ledger_reaudit):
        r = client.post(f"/api/cv/{cv_id}/review/recheck", json={})
    assert r.status_code == 200, r.text
    record = await ra.load_document("cv", cv_id, db)
    assert rs.find_listed(ra.findings_of(record), key) is None
