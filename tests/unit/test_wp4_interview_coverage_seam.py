# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Seam test (WP4, Interview #787): the coverage re-judge runs INSIDE
``send_message`` between the #188 seam and the per-gap record, so the turn's
``cluster_coverage.open_concepts`` reads the classifier's verified verdict.

Drives the real ``send_message`` with the ``test_gap_followups_session``
harness (faithful vault-writing reconcile double); only the provider is scripted.
Synthetic data only.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

_here = Path(__file__).parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from test_gap_followups_session import (  # noqa: F401 — `db` is a fixture
    _INFRA,
    _cluster,
    _gap_click,
    _row,
    _seed,
    _writer,
    db,
)

_BULLET = "Betrieb von Kubernetes-Clustern für zwölf Dienste mit Terraform als Infrastrukturcode."


def _bullet_bridge(*, write=True):
    """Writes ONE responsibility bullet (the answer, as the reconciler would)."""
    from applire.models.profile import authorized_profile_write
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        pj = copy.deepcopy(profile_record.profile_json)
        changes = []
        if write:
            pj["work_experience"][0].setdefault("responsibilities", []).append(_BULLET)
            changes.append(FieldChange(section="work_experience", field="responsibilities",
                                       action="added", new_value=_BULLET))
        with authorized_profile_write():
            profile_record.profile_json = pj
        await db.flush()
        return InterviewTurnResult(profile_dict=pj, changes=changes, addressed=bool(changes),
                                   denial_recorded=False, denied_concepts=[])

    return AsyncMock(side_effect=_run)


def _provider(verdicts):
    """The classifier call (its prompt carries the REQUIREMENTS block) gets the
    scripted verdicts; every other structured call the turn makes gets the
    harness's neutral answer."""
    calls: list[str] = []

    async def _parse(prompt, **kwargs):
        if "REQUIREMENTS" in prompt and "Produce the gap analysis JSON" in prompt:
            calls.append(prompt)
            return {"classifications": copy.deepcopy(verdicts)}
        return {"approved": True}

    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="unused")
    provider.aparse_json = AsyncMock(side_effect=_parse)
    provider.classifier_calls = calls
    return provider


async def _answer(db, session_id, text, bridge, writer, provider):
    from applire.services.session import send_message

    with (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch("applire.services.session.question_generator_with_profile", new=writer),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        return await send_message(session_id, text, db, provider)


def _verdict(req, quote):
    return {"requirement": req, "status": "direct", "reason": f"operates {req}",
            "surface_forms": [req], "evidence_quote": quote}


@pytest.mark.asyncio
async def test_seam_send_message_open_concepts_follow_a_verified_classifier_verdict(db):
    from applire.models.gap import GapAnalysis

    job, _profile, row = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes", "Terraform"]),
    ], ledger=[_row("Kubernetes"), _row("Terraform")])
    writer = _writer("Tell me about your infrastructure work?", "Anything else?")
    created = await _gap_click(db, job, _INFRA, writer)
    # the answer is German; the requirement names are English — no literal hit
    provider = _provider([_verdict("Kubernetes", _BULLET), _verdict("Terraform", _BULLET)])
    resp = await _answer(db, created.session_id, "Ich betreibe zwölf Dienste auf Clustern.",
                         _bullet_bridge(), writer, provider)

    assert len(provider.classifier_calls) == 1
    assert resp.cluster_coverage.open_concepts == []
    await db.refresh(row)
    ledger = {r["concept"]: r for r in row.keyword_ledger}
    assert ledger["Kubernetes"]["status"] == "direct"
    assert ledger["Kubernetes"]["backing"]["unit_path"] == "work_experience[0].responsibilities[0]"


@pytest.mark.asyncio
async def test_seam_a_turn_that_wrote_nothing_makes_no_classifier_call(db):
    job, _profile, _row_ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes", "Terraform"]),
    ], ledger=[_row("Kubernetes"), _row("Terraform")])
    writer = _writer("Tell me about your infrastructure work?", "Anything else?")
    created = await _gap_click(db, job, _INFRA, writer)
    provider = _provider([_verdict("Kubernetes", _BULLET)])
    resp = await _answer(db, created.session_id, "Weiß ich nicht.",
                         _bullet_bridge(write=False), writer, provider)

    assert provider.classifier_calls == []
    assert "Kubernetes" in (resp.cluster_coverage.open_concepts if resp.cluster_coverage else ["Kubernetes"])
