# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

"""R2-10 (ruling F1-2: themes only GROUP the open requirements) — a gap cluster
made of posting terms only (no member is a counted requirement, ``fit_weight >
0``) is not a topic: it is never planned by the full interview and the gaps
response marks it ``requirement_topic = False`` so no surface counts it.

UX round 2: "Commerce Domain" (E-commerce), "Messaging", "Digital Health und
Kliniken", "Fraud Domain Infrastructure" showed as open topics with zero counted
requirements. The clustering input is deliberately wider than the requirement
set (``gap.askable_gap_inputs`` adds keyword-only terms), so the shape is
legitimate input and the predicate decides at read time.
"""

from unittest.mock import patch

import pytest

from applire.services import gap_coverage
# The interview-plan harness (faithful doubles, seeded row) lives in the ADR-089
# door tests; reuse it rather than copy it. `tests/unit` is a package whose
# import name depends on pytest's rootdir, so load it by path.
import importlib.util as _ilu
from pathlib import Path as _Path

_spec = _ilu.spec_from_file_location(
    "_gap_followups_harness", _Path(__file__).with_name("test_gap_followups_session.py")
)
_h = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_h)
_cluster, _provider, _row, _seed, _writer = _h._cluster, _h._provider, _h._row, _h._seed, _h._writer
db = _h.db  # the in-memory DB fixture


def _term(concept: str) -> dict:
    """A keyword-only posting term: in the ledger, but not a requirement."""
    row = _row(concept)
    row["fit_weight"] = 0.0
    row["sources"] = ["keyword"]
    return row


LEDGER = [
    _row("Python", "direct", claimable=True),
    _row("Kubernetes"),
    _term("E-commerce"),
    _term("Online-Marktplätze"),
]
TERMS_ONLY = _cluster("cluster-commerce", "Commerce Domain", ["E-commerce", "Online-Marktplätze"])
MIXED = _cluster("cluster-k8s", "Kubernetes", ["Kubernetes", "E-commerce"])


def test_a_cluster_of_posting_terms_only_is_not_a_requirement_topic():
    assert gap_coverage.is_requirement_topic(TERMS_ONLY, LEDGER) is False
    assert gap_coverage.is_requirement_topic(MIXED, LEDGER) is True


def test_without_a_ledger_every_cluster_stays_a_topic():
    """Legacy rows / callers without a ledger keep today's behaviour."""
    assert gap_coverage.is_requirement_topic(TERMS_ONLY, None) is True
    assert gap_coverage.is_requirement_topic(TERMS_ONLY, []) is True


def test_is_askable_refuses_a_terms_only_cluster_only_when_given_the_ledger():
    assert gap_coverage.is_askable(TERMS_ONLY) is True  # no ledger: unchanged
    assert gap_coverage.is_askable(TERMS_ONLY, 2, LEDGER) is False
    assert gap_coverage.is_askable(MIXED, 2, LEDGER) is True


def test_the_gaps_response_marks_the_terms_only_cluster():
    """Seam: GapAnalysisResponse derives the flag from its OWN ledger."""
    import uuid
    from datetime import datetime, timezone

    from applire.schemas.gap import GapAnalysisResponse

    resp = GapAnalysisResponse(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        match_score=0.5, critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
        category_a=["Python"], category_b=[], category_c=["Kubernetes"],
        keyword_ledger=LEDGER, gap_clusters=[TERMS_ONLY, MIXED],
        created_at=datetime.now(timezone.utc),
    )
    flags = {c.id: c.requirement_topic for c in resp.gap_clusters}
    assert flags == {"cluster-commerce": False, "cluster-k8s": True}
    # Never persisted: the persisted shape (LLM keys) carries no such field.
    from applire.schemas.gap_cluster import LLM_CLUSTER_KEYS

    assert "requirement_topic" not in LLM_CLUSTER_KEYS


@pytest.mark.asyncio
async def test_the_full_interview_never_plans_a_terms_only_cluster(db):
    """Seam: ``_create_targeted_session`` passes the row's ledger to is_askable."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    job, _profile, _ = await _seed(db, clusters=[TERMS_ONLY, MIXED], ledger=LEDGER)
    with patch("applire.services.session.question_generator_with_profile", new=_writer("Kubernetes?")):
        full = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, _provider())
    assert full.gaps_total == 1
    assert full.current_gap_id == "cluster-k8s"
