# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

"""UAT-2b/2c (founder 2026-11-08, ADR-064 amended; ADR-095 additive) — the
create and the resumed-session doors carry ``choice_asks`` (index-aligned with
``choices``) and ``choice_unmentioned`` (the producer's literal list, only for
the very choices it was computed for). ``send_message`` is pinned in
``test_b6_a_offered_choice_not_a_denial.py``. Synthetic data only.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from applire.services.choice_grounding import record_offered_choices

# The ADR-089 door harness (faithful doubles, seeded row) — loaded by path, as
# test_r2_10_requirement_topics.py does.
import importlib.util as _ilu
from pathlib import Path as _Path

_spec = _ilu.spec_from_file_location(
    "_gap_followups_harness_uat2", _Path(__file__).with_name("test_gap_followups_session.py")
)
_h = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_h)
_cluster, _provider, _row, _seed = _h._cluster, _h._provider, _h._row, _h._seed
db = _h.db

BRIDGE = "As shift lead I also planned the rota against the weekly volume forecast."
PLAIN = "I planned the rota for 25 people across two shifts."
ASK = "As shift lead, did you also plan the rota against a volume forecast?"


async def _gen(state, profile, provider, **kwargs):
    drafted = [{"text": BRIDGE, "ask": ASK, "level": "partial"}, {"text": PLAIN, "level": "direct"}]
    record_offered_choices(state, drafted, [BRIDGE, PLAIN], unmentioned=["Forecasting"])
    return {"question": "How did you plan the rota?", "choices": [BRIDGE, PLAIN]}


@pytest.mark.asyncio
async def test_create_session_carries_the_asks_and_the_unmentioned_list(db):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    cluster = _cluster("cluster-plan", "Planning", ["Forecasting", "Kubernetes"])
    job, _profile, _ = await _seed(db, clusters=[cluster], ledger=[_row("Forecasting"), _row("Kubernetes")])
    with patch("applire.services.session.question_generator_with_profile", new=AsyncMock(side_effect=_gen)):
        out = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, _provider())
    assert out.choices == [BRIDGE, PLAIN]
    assert out.choice_asks == [ASK, None]
    assert out.choice_unmentioned == ["Forecasting"]
    assert {"choice_asks", "choice_unmentioned"} <= set(out.model_dump()), "MCP returns model_dump"


def _existing(state: dict):
    return SimpleNamespace(
        id=uuid.uuid4(), mode="targeted", hard_ceiling=12, questions_asked=2, state=state,
        job_analysis_id=uuid.uuid4(), gap_analysis_id=None, status="active",
    )


def test_the_resumed_response_carries_the_asks_and_the_unmentioned_list():
    from applire.services.session import _resumed_response

    state = {
        "critical_gaps": ["Forecasting"], "current_gap_index": 0,
        "current_question": "How did you plan the rota?",
        "current_choices": [BRIDGE, PLAIN], "questions_asked": 2,
        "gap_clusters_by_id": {}, "addressed_gaps": [],
    }
    record_offered_choices(
        state, [{"text": BRIDGE, "ask": ASK, "level": "partial"}, {"text": PLAIN, "level": "direct"}],
        [BRIDGE, PLAIN], unmentioned=["Forecasting"],
    )
    out = _resumed_response(_existing(state))
    assert out.choices == [BRIDGE, PLAIN]
    assert out.choice_asks == [ASK, None]
    assert out.choice_unmentioned == ["Forecasting"]


def test_a_resumed_question_without_recorded_asks_carries_none():
    from applire.services.session import _resumed_response

    state = {
        "critical_gaps": ["Forecasting"], "current_gap_index": 0,
        "current_question": "Q?", "current_choices": ["Some option"], "questions_asked": 2,
    }
    out = _resumed_response(_existing(state))
    assert (out.choice_asks, out.choice_unmentioned) == (None, None)
