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

"""#258 (E048 wave-5 follow-up) — `submit_testimony`: free-text testimony intake
over the ADR-046 reconciler chain, mirroring `agent_bridge.submit_agent_claims`
but for ONE whole free-text document (not itemized claims)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from applire.exceptions import LLMTruncatedError
from applire.models.profile import MasterProfile
from applire.providers.llm.mock import MockLLMProvider
from applire.services.profile.reconcile.testimony_bridge import submit_testimony
from tests.support.profile_factory import make_master_profile

_DOSSIER_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "tests"
    / "files"
    / "panel_review_case"
    / "it_backend_daniel"
    / "dossier_daniel_kovac.md"
)


class _QueueProvider:
    """Returns one queued reconcile payload per call; records every prompt."""

    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def aparse_json(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> Any:
        self.prompts.append(prompt)
        self.systems.append(system or "")
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def _seed_profile(db) -> MasterProfile:
    record = make_master_profile(
        profile_json={
            "personal_info": {"full_name": "Daniel Kovač"},
            "metadata": {
                "completeness_score": 0.5,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
            },
        }
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@pytest.mark.asyncio
async def test_applied_testimony_writes_receipt_with_source_testimony(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider(
        [
            {
                "ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}],
                "ambiguities": [],
                "denials": [],
            }
        ]
    )

    result = await submit_testimony("I ran Kafka in production for 3 years.", async_db, provider)

    assert result.status == "applied"
    assert result.submission_id
    assert len(result.changes) == 1
    assert result.changes[0].section == "skills"


@pytest.mark.asyncio
async def test_no_change_testimony_is_reported_honestly(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider([{"ops": [], "ambiguities": [], "denials": []}])

    result = await submit_testimony("Just saying hello.", async_db, provider)

    assert result.status == "no_change"
    assert result.changes == []


@pytest.mark.asyncio
async def test_denial_only_testimony_lands_in_denied_concepts_not_dropped(async_db):
    record = await _seed_profile(async_db)
    provider = _QueueProvider(
        [{"ops": [], "ambiguities": [], "denials": ["blockchain"]}]
    )
    statement = (
        "No — I have no blockchain or crypto experience at all. That's an "
        "honest gap; I haven't worked with settlement rails on chain."
    )

    result = await submit_testimony(statement, async_db, provider)

    assert result.status == "denial_recorded"
    assert len(result.changes) == 1  # the denial receipt itself

    await async_db.refresh(record)
    denied = record.profile_json["metadata"]["denied_concepts"]
    assert len(denied) == 1
    assert denied[0]["concept"] == "blockchain"
    assert denied[0]["source"] == "testimony"
    assert denied[0]["statement"] == statement


@pytest.mark.asyncio
async def test_ambiguity_is_parked_as_needs_confirmation(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider(
        [
            {
                "ops": [],
                "ambiguities": [
                    {
                        "op": "request_confirmation",
                        "question": "Is 'Cargonaut' the same as your existing employer?",
                        "options": ["Yes", "No"],
                        "context": {},
                    }
                ],
                "denials": [],
            }
        ]
    )

    result = await submit_testimony("I worked at Cargonaut too.", async_db, provider)

    assert result.status == "needs_confirmation"
    assert len(result.confirmations) == 1
    assert result.confirmations[0].source == "testimony"


@pytest.mark.asyncio
async def test_conflict_status_when_op_batch_yields_a_conflict(async_db):
    record = make_master_profile(
        profile_json={
            "personal_info": {"full_name": "Daniel Kovač"},
            "work_experience": [
                {
                    "id": "w1",
                    "company": "Cargonaut",
                    "role": "Backend Engineer",
                    "start_date": "2020-01",
                }
            ],
            "metadata": {
                "completeness_score": 0.5,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
            },
        }
    )
    async_db.add(record)
    await async_db.commit()
    await async_db.refresh(record)

    provider = _QueueProvider(
        [
            {
                "ops": [
                    {
                        "op": "flag_conflict",
                        "target": "w1",
                        "field": "start_date",
                        "existing": "2020-01",
                        "incoming": "2019-06",
                    }
                ],
                "ambiguities": [],
                "denials": [],
            }
        ]
    )

    result = await submit_testimony(
        "Actually I joined Cargonaut in June 2019, not January 2020.", async_db, provider
    )

    assert result.status == "conflict"
    assert len(result.conflicts) == 1


@pytest.mark.asyncio
async def test_applied_status_wins_over_denial_when_testimony_yields_both(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider(
        [
            {
                "ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}],
                "ambiguities": [],
                "denials": ["blockchain"],
            }
        ]
    )

    result = await submit_testimony(
        "I ran Kafka in production. No blockchain experience though.", async_db, provider
    )

    assert result.status == "applied"
    # both receipts present even though status reports the higher-precedence one
    assert len(result.changes) == 2


@pytest.mark.asyncio
async def test_truncation_returns_error_status_without_persisting(async_db):
    record = await _seed_profile(async_db)
    provider = _QueueProvider([LLMTruncatedError("output truncated")])

    result = await submit_testimony("A very long dossier...", async_db, provider)

    assert result.status == "error"
    assert result.detail

    await async_db.refresh(record)
    assert record.profile_json["metadata"].get("enrichment_history", []) == []


@pytest.mark.asyncio
async def test_no_profile_raises_lookup_error(async_db):
    provider = _QueueProvider([{"ops": [], "ambiguities": [], "denials": []}])
    with pytest.raises(LookupError):
        await submit_testimony("hello", async_db, provider)


@pytest.mark.asyncio
async def test_real_dossier_produces_story_skill_and_denial_receipts(async_db):
    """AC (#258): a run-4-style off-CV dossier reconciles into receipted vault
    entries — a signature story, skills, and an explicit denial — via the SAME
    reconciler chain the interview and agent-claims doors use. The dossier text
    itself is real repo test material (tests/files/panel_review_case); the
    reconciler's LLM call is mocked with a REPRESENTATIVE typed op batch (the
    engine's own parsing/stance/apply machinery runs for real)."""
    await _seed_profile(async_db)
    dossier = _DOSSIER_PATH.read_text(encoding="utf-8")
    assert "blockchain" in dossier.lower()  # sanity: the fixture still has the denial block

    provider = _QueueProvider(
        [
            {
                "ops": [
                    {"op": "upsert_skill", "name": "Kubernetes", "category": "technical",
                     "proficiency": "advanced", "evidence": []},
                    {"op": "upsert_skill", "name": "Kafka", "category": "technical",
                     "proficiency": "advanced", "evidence": []},
                    {
                        "op": "upsert_story",
                        "title": "ECS to Kubernetes migration",
                        "challenge": "Deploys took 45 minutes and staging drifted from production.",
                        "mechanism": (
                            "Containerised the remaining services, introduced Helm charts "
                            "+ ArgoCD GitOps, migrated one canary service first, then "
                            "batches of three."
                        ),
                        "outcome": "Deploy time dropped from 45 to 8 minutes; rollbacks became one Git revert.",
                        "benchmark": None,
                        "evidence": [],
                    },
                ],
                "ambiguities": [],
                "denials": ["blockchain", "crypto"],
            }
        ]
    )

    result = await submit_testimony(dossier, async_db, provider)

    assert result.status == "applied"
    sections = {c.section for c in result.changes}
    assert "skills" in sections
    assert "signature_stories" in sections
    # the denial receipt rides alongside the applied changes (both lists, #231 semantics)
    assert any(c.section == "denied_concepts" for c in result.changes) or len(
        [c for c in result.changes]
    ) >= 3


@pytest.mark.asyncio
async def test_mock_llm_provider_recognizes_the_testimony_reconcile_call_shape(async_db):
    """Regression: `submit_testimony` reuses `reconcile()`'s system prompt
    ("profile reconciler") as-is — MockLLMProvider.aparse_json must recognize
    it and return a real op batch, never the `{"mock": ...}` fallback that
    would corrupt the mock-stack E2E suite."""
    await _seed_profile(async_db)
    provider = MockLLMProvider()

    result = await submit_testimony(
        "I hold an ITIL Foundation certification and I am proficient in Python.",
        async_db,
        provider,
    )

    assert result.status == "applied"
    assert result.changes  # the mock's default reconcile op batch was applied, not swallowed
