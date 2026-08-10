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

"""#333 — a parked `pending_confirmation` must survive and be reachable.

Two halves, both regression-shaped:

**Survival** — the two import doors (`import_from_text` and `_apply_merge`)
replace `metadata.pending_confirmations` / `pending_conflicts` wholesale with
the latest round. The guard that existed preserved exactly one source
(`agent_interview`), so a `testimony`- or previous-`cv_upload`-sourced parked
item was destroyed by the next CV upload — user data silently discarded.

**Resolvability** — `assess_health` composed the Tier-2 read from
`pending_conflicts` only, so a parked confirmation produced no Health-hub issue
and therefore no "Resolve" entry point into the profile-review interview, which
already knows how to walk and resolve confirmations
(`session._open_confirmations` → `build_confirmation_clusters` →
`_handle_confirmation_answer` → `profile.resolve_confirmation`).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from applire.models.profile import MasterProfile
from applire.providers.embedding.noop import NoopEmbeddingProvider
from applire.schemas.profile import (
    Conflict,
    MasterProfileData,
    PendingConfirmation,
)
from applire.services.profile import _apply_merge, import_from_text
from applire.services.profile.health import assess_health
from applire.services.profile.merge import MergeResult
from tests.support.profile_factory import make_master_profile


async def _seed_profile(db, *, confirmations=None, conflicts=None) -> MasterProfile:
    record = make_master_profile(
        profile_json={
            "personal_info": {"full_name": "Anna Bauer"},
            "metadata": {
                "completeness_score": 10.0,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
                "pending_confirmations": [
                    c.model_dump(mode="json") for c in (confirmations or [])
                ],
                "pending_conflicts": [
                    c.model_dump(mode="json") for c in (conflicts or [])
                ],
            },
        }
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def _run_import(db, record, merge_result) -> None:
    """Drive `import_from_text` with the extraction/reconcile chain stubbed."""
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
        await import_from_text("CV text", db, AsyncMock())
    await db.refresh(record)


# ── Survival ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_testimony_confirmation_survives_a_later_cv_import(async_db):
    """The reported defect: `submit_testimony` parks an ambiguity the human must
    answer, and the next `import_cv` deletes it."""
    record = await _seed_profile(
        async_db,
        confirmations=[
            PendingConfirmation(
                question="Was the SAP role 3 or 5 years?",
                options=["3 years", "5 years"],
                source="testimony",
            )
        ],
    )
    merged = MasterProfileData.model_validate(record.profile_json)
    await _run_import(
        async_db,
        record,
        MergeResult(
            merged_profile=merged,
            pending_confirmations=[
                PendingConfirmation(question="new import round?", source="cv_upload")
            ],
        ),
    )

    questions = {
        c["question"]: c["source"]
        for c in record.profile_json["metadata"]["pending_confirmations"]
    }
    assert questions.get("Was the SAP role 3 or 5 years?") == "testimony"
    assert "new import round?" in questions


@pytest.mark.asyncio
async def test_earlier_import_confirmation_survives_the_next_import(async_db):
    """An unresolved `cv_upload` confirmation from a previous round is the user's
    open question too — a second upload must not silently drop it."""
    record = await _seed_profile(
        async_db,
        confirmations=[
            PendingConfirmation(question="old import?", source="cv_upload")
        ],
        conflicts=[
            Conflict(
                section="skills", field="name", existing_value="c",
                incoming_value="d", source="cv_upload",
            )
        ],
    )
    merged = MasterProfileData.model_validate(record.profile_json)
    await _run_import(
        async_db,
        record,
        MergeResult(
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
        ),
    )

    meta = record.profile_json["metadata"]
    questions = {c["question"] for c in meta["pending_confirmations"]}
    assert questions == {"old import?", "new import round?"}
    existing_values = {c["existing_value"] for c in meta["pending_conflicts"]}
    assert existing_values == {"c", "x"}


@pytest.mark.asyncio
async def test_re_raised_parked_item_is_not_duplicated(async_db):
    """Preserving across rounds must not accumulate: an ambiguity the new round
    raises again is the *same* open question, kept once with its original id."""
    parked = PendingConfirmation(
        question="Is 'Projektleiter' the same role as 'Project Lead'?",
        options=["same role", "two roles"],
        source="cv_upload",
    )
    parked_conflict = Conflict(
        section="skills", field="name", existing_value="c",
        incoming_value="d", source="cv_upload",
    )
    record = await _seed_profile(
        async_db, confirmations=[parked], conflicts=[parked_conflict]
    )
    merged = MasterProfileData.model_validate(record.profile_json)
    await _run_import(
        async_db,
        record,
        MergeResult(
            merged_profile=merged,
            conflicts=[
                Conflict(
                    section="skills", field="name", existing_value="c",
                    incoming_value="d", source="cv_upload",
                )
            ],
            pending_confirmations=[
                PendingConfirmation(
                    question="Is 'Projektleiter' the same role as 'Project Lead'?",
                    options=["same role", "two roles"],
                    source="cv_upload",
                )
            ],
        ),
    )

    meta = record.profile_json["metadata"]
    assert len(meta["pending_confirmations"]) == 1
    # The preserved item wins, so an in-flight resolve still targets a live id.
    assert meta["pending_confirmations"][0]["confirmation_id"] == parked.confirmation_id
    assert len(meta["pending_conflicts"]) == 1
    assert meta["pending_conflicts"][0]["conflict_id"] == parked_conflict.conflict_id


@pytest.mark.asyncio
async def test_resolved_parked_items_are_not_carried_forward(async_db):
    """Only *open* items are preserved — an answered one is done."""
    record = await _seed_profile(
        async_db,
        confirmations=[
            PendingConfirmation(
                question="already answered?",
                source="testimony",
                resolved=True,
                chosen_option="yes",
            )
        ],
    )
    merged = MasterProfileData.model_validate(record.profile_json)
    await _run_import(async_db, record, MergeResult(merged_profile=merged))

    assert record.profile_json["metadata"]["pending_confirmations"] == []


@pytest.mark.asyncio
async def test_upload_door_also_preserves_non_agent_parked_items(async_db):
    """Dual-door rule (E034): the browser `/upload` door carries the same
    wholesale replace and must preserve the same items."""
    record = await _seed_profile(
        async_db,
        confirmations=[
            PendingConfirmation(question="testimony-parked?", source="testimony")
        ],
    )
    merged = MasterProfileData.model_validate(record.profile_json)
    with patch(
        "applire.services.profile.reconcile_import",
        new=AsyncMock(return_value=MergeResult(merged_profile=merged)),
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
    assert "testimony-parked?" in questions


# ── Resolvability ───────────────────────────────────────────────────────────


def _profile_with_confirmations(*confirmations) -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "metadata": {
                "pending_confirmations": [
                    c.model_dump(mode="json") for c in confirmations
                ]
            },
        }
    )


def test_open_confirmation_becomes_a_health_issue():
    """Without this the Health hub renders nothing for a parked confirmation, so
    the "Resolve" button that opens the profile-review interview never exists."""
    confirmation = PendingConfirmation(
        question="Was the SAP role 3 or 5 years?",
        options=["3 years", "5 years"],
        source="testimony",
    )
    health = assess_health(_profile_with_confirmations(confirmation))

    issues = [i for i in health.issues if i.thread == "confirmation"]
    assert len(issues) == 1
    assert issues[0].id == f"confirmation:{confirmation.confirmation_id}"
    assert "Was the SAP role 3 or 5 years?" in issues[0].summary
    # Visible and actionable, never a blocking gate (ADR-041 amended).
    assert issues[0].profile_mismatch_severity == "review"
    assert issues[0].source_record_ref == "testimony"


def test_resolved_confirmation_is_not_a_health_issue():
    health = assess_health(
        _profile_with_confirmations(
            PendingConfirmation(
                question="answered", source="testimony", resolved=True,
                chosen_option="yes",
            )
        )
    )
    assert [i for i in health.issues if i.thread == "confirmation"] == []
