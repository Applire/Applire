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

"""E045 (US254) — `submit_agent_claims`: the notary path over the ADR-046 chain.

Agent = interviewer, Applire = notary. Claims are free-text testimony fed to
the existing reconcile → stance → apply → receipts → ledger-upgrade chain with
`agent_interview` provenance. No new write vocabulary."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select

from applire.exceptions import LLMTruncatedError
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.schemas.claims import ClaimItem, ClaimsSubmission
from applire.services.profile.reconcile.agent_bridge import submit_agent_claims


class _QueueProvider:
    """Returns one queued reconcile payload per call; records every prompt.

    Absorbs the full provider-ABC signature via **kwargs (stub rule)."""

    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.prompts.append(prompt)
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _skill_payload(name: str) -> dict:
    return {
        "ops": [{"op": "upsert_skill", "name": name, "category": "technical"}],
        "ambiguities": [],
        "denials": [],
    }


_EMPTY = {"ops": [], "ambiguities": [], "denials": []}


async def _seed_profile(db) -> MasterProfile:
    record = MasterProfile(
        profile_json={
            "personal_info": {"full_name": "Anna Bauer"},
            "metadata": {
                "completeness_score": 10.0,
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


async def _seed_job_with_ledger(db, concepts: list[str]) -> uuid.UUID:
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="JD text",
        role_title="Platform Engineer",
        seniority_level="senior",
        language_requirement="en",
    )
    db.add(job)
    await db.flush()
    gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=(
            await db.execute(select(MasterProfile.id))
        ).scalar_one(),
        keyword_ledger=[
            {"concept": c, "claimable": False, "status": "missing", "evidence": ""}
            for c in concepts
        ],
    )
    db.add(gap)
    await db.commit()
    return job.id


@pytest.mark.asyncio
async def test_applied_claim_writes_receipt_with_submission_id(async_db):
    record = await _seed_profile(async_db)
    provider = _QueueProvider([_skill_payload("Kubernetes")])
    result = await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement="I administered Kubernetes clusters for 3 years.")]),
        None,
        async_db,
        provider,
    )
    assert result.schema_version == "claims/1"
    assert result.results[0].status == "applied"
    assert result.results[0].changes
    await async_db.refresh(record)
    history = record.profile_json["metadata"]["enrichment_history"]
    assert history[-1]["source"] == "agent_interview"
    assert history[-1]["source_session_id"] == result.submission_id
    skills = [s["name"] for s in record.profile_json["skills"]]
    assert "Kubernetes" in skills


@pytest.mark.asyncio
async def test_no_change_claim_writes_no_receipt(async_db):
    record = await _seed_profile(async_db)
    provider = _QueueProvider([_EMPTY])
    result = await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement="Nothing new here.")]),
        None,
        async_db,
        provider,
    )
    assert result.results[0].status == "no_change"
    await async_db.refresh(record)
    assert not record.profile_json["metadata"].get("enrichment_history")


@pytest.mark.asyncio
async def test_ambiguity_parked_with_stable_confirmation_id(async_db):
    record = await _seed_profile(async_db)
    provider = _QueueProvider(
        [
            {
                "ops": [],
                "ambiguities": [
                    {
                        "op": "request_confirmation",
                        "question": "Is 'K8s admin' the same as your existing 'Kubernetes' skill?",
                        "options": ["merge", "add_separate"],
                        "context": {"section": "skills"},
                    }
                ],
                "denials": [],
            }
        ]
    )
    result = await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement="I do K8s administration.")]),
        None,
        async_db,
        provider,
    )
    assert result.results[0].status == "needs_confirmation"
    assert result.pending_review_count == 1
    await async_db.refresh(record)
    parked = record.profile_json["metadata"]["pending_confirmations"]
    assert len(parked) == 1
    assert parked[0]["confirmation_id"]  # stable id — Health-hub resolvable
    assert parked[0]["source"] == "agent_interview"


@pytest.mark.asyncio
async def test_non_member_gap_rejects_whole_call_before_any_llm_spend(async_db):
    await _seed_profile(async_db)
    job_id = await _seed_job_with_ledger(async_db, ["Kubernetes", "Terraform"])
    provider = _QueueProvider([_skill_payload("Kubernetes")])
    submission = ClaimsSubmission(
        claims=[
            ClaimItem(statement="I run Kubernetes.", gap="Kubernetes"),
            ClaimItem(statement="I also know Go.", gap="Go"),  # not in ledger
        ]
    )
    with pytest.raises(ValueError, match="Go"):
        await submit_agent_claims(submission, job_id, async_db, provider)
    assert provider.prompts == []  # nothing partially applied, no LLM spend


@pytest.mark.asyncio
async def test_gap_membership_is_equality_not_substring(async_db):
    # "AI" is a substring of "AI Governance" but NOT a ledger member — the
    # over-flip trap: substring semantics would validate it.
    await _seed_profile(async_db)
    job_id = await _seed_job_with_ledger(async_db, ["AI Governance"])
    provider = _QueueProvider([_skill_payload("AI")])
    with pytest.raises(ValueError, match="AI"):
        await submit_agent_claims(
            ClaimsSubmission(claims=[ClaimItem(statement="I did AI work.", gap="AI")]),
            job_id,
            async_db,
            provider,
        )
    assert provider.prompts == []


@pytest.mark.asyncio
async def test_gap_without_job_id_rejected(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider([])
    with pytest.raises(ValueError, match="job_id"):
        await submit_agent_claims(
            ClaimsSubmission(claims=[ClaimItem(statement="x", gap="Kubernetes")]),
            None,
            async_db,
            provider,
        )


@pytest.mark.asyncio
async def test_ledger_upgrade_gated_on_applied_changes(async_db):
    await _seed_profile(async_db)
    job_id = await _seed_job_with_ledger(async_db, ["Kubernetes", "Terraform"])
    provider = _QueueProvider([_skill_payload("Kubernetes"), _EMPTY])
    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(statement="I administered Kubernetes clusters.", gap="Kubernetes"),
                ClaimItem(statement="I have read about Terraform.", gap="Terraform"),
            ]
        ),
        job_id,
        async_db,
        provider,
    )
    assert result.ledger_upgraded == ["Kubernetes"]
    row = (
        await async_db.execute(
            select(GapAnalysis).where(GapAnalysis.job_analysis_id == job_id)
        )
    ).scalar_one()
    by_concept = {e["concept"]: e for e in row.keyword_ledger}
    assert by_concept["Kubernetes"]["claimable"] is True
    assert by_concept["Kubernetes"]["evidence"] == "I administered Kubernetes clusters."
    # No-change claim must NOT flip its entry (send_message addressed-gate parity).
    assert by_concept["Terraform"]["claimable"] is False


@pytest.mark.asyncio
async def test_ledger_upgraded_reports_canonical_concept_casing(async_db):
    """Adversarial observation 1: gap membership is normalized equality, so
    gap="kubernetes" is valid — but the envelope must echo the CANONICAL ledger
    concept ("Kubernetes"), not the caller's casing."""
    await _seed_profile(async_db)
    job_id = await _seed_job_with_ledger(async_db, ["Kubernetes"])
    provider = _QueueProvider([_skill_payload("Kubernetes")])
    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[ClaimItem(statement="I administered Kubernetes clusters.", gap="kubernetes")]
        ),
        job_id,
        async_db,
        provider,
    )
    assert result.ledger_upgraded == ["Kubernetes"]


@pytest.mark.asyncio
async def test_truncation_fails_only_that_claim(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider([LLMTruncatedError("budget"), _skill_payload("Databricks")])
    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(statement="A very long story."),
                ClaimItem(statement="I built pipelines on Databricks."),
            ]
        ),
        None,
        async_db,
        provider,
    )
    assert result.results[0].status == "error"
    assert result.results[0].detail
    assert result.results[1].status == "applied"


@pytest.mark.asyncio
async def test_claims_apply_sequentially_later_claims_see_earlier_state(async_db):
    await _seed_profile(async_db)
    provider = _QueueProvider([_skill_payload("Kubernetes"), _EMPTY])
    await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(statement="I administered Kubernetes clusters."),
                ClaimItem(statement="Second claim."),
            ]
        ),
        None,
        async_db,
        provider,
    )
    # The second reconcile call's prompt serialises the profile AFTER claim 1.
    assert "Kubernetes" in provider.prompts[1]


@pytest.mark.asyncio
async def test_updated_at_touched_but_metadata_recompute_stays_import_only(async_db):
    record = await _seed_profile(async_db)
    before_updated_at = record.updated_at
    provider = _QueueProvider([_skill_payload("Kubernetes")])
    await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement="I administered Kubernetes clusters.")]),
        None,
        async_db,
        provider,
    )
    await async_db.refresh(record)
    assert record.updated_at != before_updated_at
    meta = record.profile_json["metadata"]
    assert meta["last_updated"] == "2026-01-01T00:00:00Z"
    assert meta["completeness_score"] == 10.0


@pytest.mark.asyncio
async def test_import_replace_preserves_agent_parked_items(async_db):
    """Adversarial M2: the import path replaces both pending metadata lists
    wholesale each round — agent-parked items must survive a subsequent CV
    import, or the next upload silently destroys candidate-facing review items."""
    from unittest.mock import AsyncMock, patch

    from applire.schemas.profile import (
        Conflict,
        MasterProfileData,
        PendingConfirmation,
    )
    from applire.services.profile import import_from_text
    from applire.services.profile.merge import MergeResult

    record = await _seed_profile(async_db)
    pj = dict(record.profile_json)
    pj["metadata"] = dict(
        pj["metadata"],
        pending_confirmations=[
            PendingConfirmation(
                question="agent-parked?", source="agent_interview"
            ).model_dump(mode="json"),
            PendingConfirmation(question="old import?", source="cv_upload").model_dump(
                mode="json"
            ),
        ],
        pending_conflicts=[
            Conflict(
                section="skills", field="name", existing_value="a",
                incoming_value="b", source="agent_interview",
            ).model_dump(mode="json"),
            Conflict(
                section="skills", field="name", existing_value="c",
                incoming_value="d", source="cv_upload",
            ).model_dump(mode="json"),
        ],
    )
    record.profile_json = pj
    await async_db.commit()

    merged = MasterProfileData.model_validate(record.profile_json)
    merge_result = MergeResult(
        merged_profile=merged,
        conflicts=[
            Conflict(
                section="work_experience", field="role", existing_value="x",
                incoming_value="y", source="cv_upload",
            )
        ],
        pending_confirmations=[
            PendingConfirmation(question="new import round?", source="cv_upload")
        ],
    )
    provider = AsyncMock()
    with patch(
        "applire.services.profile.extract_with_fallback",
        new=AsyncMock(return_value={"personal_info": {"name": "Anna Bauer"}}),
    ), patch(
        "applire.services.profile.review_and_refine",
        new=AsyncMock(side_effect=lambda **kw: kw["draft"]),
    ), patch(
        "applire.services.profile.annotate_expected_fields", new=AsyncMock()
    ), patch(
        "applire.services.profile.enrich_skills",
        new=AsyncMock(side_effect=lambda p, _: p),
    ), patch(
        "applire.services.profile.reconcile_import",
        new=AsyncMock(return_value=merge_result),
    ):
        await import_from_text("CV text", async_db, provider)

    await async_db.refresh(record)
    meta = record.profile_json["metadata"]
    conf_by_q = {c["question"]: c["source"] for c in meta["pending_confirmations"]}
    assert "agent-parked?" in conf_by_q  # survived
    assert "new import round?" in conf_by_q  # latest round present
    assert "old import?" not in conf_by_q  # non-agent items still replaced
    conflict_sources = {
        (c["existing_value"], c["source"]) for c in meta["pending_conflicts"]
    }
    assert ("a", "agent_interview") in conflict_sources
    assert ("x", "cv_upload") in conflict_sources
    assert ("c", "cv_upload") not in conflict_sources


@pytest.mark.asyncio
async def test_apply_merge_upload_door_also_preserves_agent_parked_items(async_db):
    """The browser /upload door (`_apply_merge`) carries the SAME wholesale
    replace as import_from_text — both import doors must preserve agent-parked
    items (dual-door rule, E034)."""
    from unittest.mock import AsyncMock, patch

    from applire.providers.embedding.noop import NoopEmbeddingProvider
    from applire.schemas.profile import MasterProfileData, PendingConfirmation
    from applire.services.profile import _apply_merge
    from applire.services.profile.merge import MergeResult

    record = await _seed_profile(async_db)
    pj = dict(record.profile_json)
    pj["metadata"] = dict(
        pj["metadata"],
        pending_confirmations=[
            PendingConfirmation(
                question="agent-parked?", source="agent_interview"
            ).model_dump(mode="json"),
        ],
    )
    record.profile_json = pj
    await async_db.commit()

    merged = MasterProfileData.model_validate(record.profile_json)
    merge_result = MergeResult(merged_profile=merged)
    with patch(
        "applire.services.profile.reconcile_import",
        new=AsyncMock(return_value=merge_result),
    ):
        await _apply_merge(
            async_db,
            MasterProfileData.model_validate({"personal_info": {"name": "Anna Bauer"}}),
            source="cv_upload",
            emb_provider=NoopEmbeddingProvider(),
            provider=AsyncMock(),
        )

    await async_db.refresh(record)
    questions = [
        c["question"]
        for c in record.profile_json["metadata"]["pending_confirmations"]
    ]
    assert "agent-parked?" in questions


@pytest.mark.asyncio
async def test_denial_only_claim_is_recorded_not_dropped(async_db):
    """#231 (bug + F8) — a denial-only claim ("I did not personally configure
    the embedding models, the vector store or any reranking") must not vanish
    as `no_change`: the denial persists to metadata.denied_concepts WITH a
    transparency receipt, and the honest `denial_recorded` status is reported
    — never silently dropped."""
    record = await _seed_profile(async_db)
    statement = (
        "I did not personally configure the embedding models, the vector "
        "store or any reranking."
    )
    provider = _QueueProvider(
        [{"ops": [], "ambiguities": [], "denials": ["embeddings"]}]
    )
    result = await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement=statement)]),
        None,
        async_db,
        provider,
    )
    assert result.results[0].status == "denial_recorded"
    assert result.results[0].changes  # the receipt is visible on the result too
    await async_db.refresh(record)
    meta = record.profile_json["metadata"]
    denied = meta["denied_concepts"]
    assert len(denied) == 1
    assert denied[0]["concept"] == "embeddings"
    assert denied[0]["statement"] == statement
    assert denied[0]["source"] == "agent_interview"
    history = meta["enrichment_history"]
    assert history, "a denial-only turn must still leave a receipt (#231a)"
    assert history[-1]["source"] == "agent_interview"
    assert any(c["field"] == "denied_concepts" for c in history[-1]["changes"])


@pytest.mark.asyncio
async def test_redenial_updates_in_place_case_insensitively(async_db):
    """Re-denying the same concept (different casing) refreshes statement/date
    in place rather than duplicating the vault entry."""
    record = await _seed_profile(async_db)
    provider = _QueueProvider(
        [
            {"ops": [], "ambiguities": [], "denials": ["Embeddings"]},
            {"ops": [], "ambiguities": [], "denials": ["embeddings"]},
        ]
    )
    await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement="No embeddings work.")]),
        None, async_db, provider,
    )
    await submit_agent_claims(
        ClaimsSubmission(
            claims=[ClaimItem(statement="Confirmed: still no embeddings work.")]
        ),
        None, async_db, provider,
    )
    await async_db.refresh(record)
    denied = record.profile_json["metadata"]["denied_concepts"]
    assert len(denied) == 1, "a re-denial must update in place, never duplicate"
    assert denied[0]["statement"] == "Confirmed: still no embeddings work."


@pytest.mark.asyncio
async def test_applied_status_wins_over_denial_when_a_claim_yields_both(async_db):
    """Precedence: error > needs_confirmation > conflict > applied >
    denial_recorded > no_change. A claim that both applies a real change AND
    denies something else reports the higher-precedence `applied`."""
    await _seed_profile(async_db)
    provider = _QueueProvider(
        [
            {
                "ops": [{"op": "upsert_skill", "name": "Terraform", "category": "technical"}],
                "ambiguities": [],
                "denials": ["Kubernetes"],
            }
        ]
    )
    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[ClaimItem(statement="I know Terraform, not Kubernetes.")]
        ),
        None,
        async_db,
        provider,
    )
    assert result.results[0].status == "applied"


@pytest.mark.asyncio
async def test_denial_only_claim_never_upgrades_the_ledger(async_db):
    """A denial must never flip its ledger entry claimable — only a REAL
    applied change may (the #188 addressed-gate parity, now denial-aware)."""
    await _seed_profile(async_db)
    job_id = await _seed_job_with_ledger(async_db, ["LegalTech"])
    provider = _QueueProvider(
        [{"ops": [], "ambiguities": [], "denials": ["LegalTech"]}]
    )
    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(
                    statement="No direct LegalTech experience, that's an honest gap.",
                    gap="LegalTech",
                )
            ]
        ),
        job_id,
        async_db,
        provider,
    )
    assert result.results[0].status == "denial_recorded"
    assert result.ledger_upgraded == []  # a denial must never flip the gate


@pytest.mark.asyncio
async def test_no_profile_raises_lookup_error(async_db):
    with pytest.raises(LookupError):
        await submit_agent_claims(
            ClaimsSubmission(claims=[ClaimItem(statement="x")]),
            None,
            async_db,
            _QueueProvider([]),
        )
