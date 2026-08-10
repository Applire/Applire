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

"""ADR-063 / #480 PR 1 — `commit_ops`, the vault's one write path.

The design's §2 invariant table, one test per row. Row 2 — the persisted-denial
re-floor, added by PR 4 — has its own file (`test_commit_ops_refloor.py`);
what this file pins about it is that its receipts stay OFF `changes`.

| 1 | ops applied through `apply_ops`, the only path from intent to state |
| 3 | the enrichment trail is UNCONDITIONAL — this is what closes the
      `if receipt_changes:` holes the testimony and agent bridges shipped |
| 4 | completeness recompute is UNIVERSAL (was import-only) |
| 5 | `metadata.last_updated` + `record.updated_at` |
| 6 | deterministic skill enrichment, unconditional |
| 7 | receipt separation — demotions/denials/refloorings are receipted but never
      enter `bool(changes)` (the #231/#352 separation) |
| 8 | `_ensure_loadable` round-trip as the last gate before assignment |

Plus the contract properties: **flush, not commit** (amendment (3)); the §7.4
`grounding` parameter (`None` = a direct act, ADR-061 clause 2); the §7.2
`embedding_provider` parameter (`None` = leave stale, log ONCE).
"""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import (
    MasterProfile,
    ProfileSnapshot,
)
from applire.services.profile.commit import (
    CommitProvenance,
    EnrichPolicy,
    SnapshotClass,
    TurnGrounding,
    commit_ops,
)
from applire.services.profile.reconcile.ops import (
    DemoteSkill,
    RequestConfirmation,
    UpsertSkill,
)

_SOURCE = "testimony"

_SEED = {
    "personal_info": {"full_name": "Daniel Kovač", "email": "daniel@example.invalid"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Rheinwerk GmbH",
            "role": "Automation Engineer",
            "start_date": "2018-01",
            "end_date": "2023-12",
            "technologies": ["Kubernetes"],
        }
    ],
    "skills": [{"name": "Kubernetes", "category": "technical", "status": "confirmed"}],
    "metadata": {
        "completeness_score": 0.0,
        "created_via": "cv_upload",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
    },
}


def _provenance(session_id: str | None = None) -> CommitProvenance:
    return CommitProvenance(
        source=_SOURCE,
        intake="testimony",
        session_id=session_id or str(uuid.uuid4()),
        actor="candidate",
    )


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db_session):
    from applire.models.profile import authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=dict(_SEED))
    db_session.add(record)
    await db_session.commit()
    return record


# ── Invariant 1 — apply_ops is the only path from intent to state ─────────────


@pytest.mark.asyncio
async def test_ops_are_applied_and_persisted(db_session, seeded):
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Apache Kafka", category="technical")],
        _provenance(),
    )

    assert any(c.section == "skills" for c in result.changes)
    names = [s["name"] for s in seeded.profile_json["skills"]]
    assert "Apache Kafka" in names
    assert result.record is seeded


@pytest.mark.asyncio
async def test_no_profile_creates_the_first_one(db_session):
    """#480 PR 8 — the `record=None` contract: creation, not `LookupError`.

    Until PR 8 the committer refused an empty vault, so the three keyword-arg
    constructor sites created the row themselves — outside the write token and
    outside every invariant. Creation belongs to the committer now, so the ops
    the caller brings land on the new row through the ordinary path.
    """
    result = await commit_ops(
        db_session, [UpsertSkill(name="Rust", category="technical")], _provenance()
    )

    rows = (await db_session.execute(select(MasterProfile))).scalars().all()
    assert len(rows) == 1
    assert rows[0] is result.record
    assert [s["name"] for s in rows[0].profile_json["skills"]] == ["Rust"]
    # The invariants apply to a creation exactly as they do to an update.
    metadata = rows[0].profile_json["metadata"]
    assert len(metadata["enrichment_history"]) == 1
    assert metadata["last_updated"] is not None


@pytest.mark.asyncio
async def test_the_created_row_is_constructed_inside_the_write_token(db_session):
    """PR 8 is PR 9's prerequisite: the constructor itself must be authorised.

    `MasterProfile(profile_json=…)` fires the clause-6 setter (ADR-063 amended
    2026-08-09) — which is exactly why the three creation sites made the guard
    count 19 writers, not 16. Creating the row inside `commit_ops` puts the
    token around the constructor, so PR 9's strict mode cannot break profile
    creation.
    """
    # Strict since PR 9: an unauthorised constructor raises
    # `UnauthorizedProfileWriteError`, so the call completing is the assertion.
    # The row assertion keeps it from passing vacuously.
    result = await commit_ops(db_session, [], _provenance())

    assert result.record.id is not None


@pytest.mark.asyncio
async def test_create_profile_record_writes_an_empty_row(db_session):
    """The creation primitive on its own — the Mode-B guided stub's shape.

    A guided interview may start with no vault at all; its stub row is created
    empty and filled later, one interview turn at a time. `{}` is what that site
    has always written and what its readers (`profile_json or {}`) index, so the
    primitive preserves it exactly; content can only ever arrive as ops.
    """
    from applire.services.profile.commit import create_profile_record

    record = await create_profile_record(db_session)

    assert record.profile_json == {}
    # Flushed, not committed — commit.py never owns the transaction, and the
    # caller needs the id for its own foreign key.
    assert record.id is not None


@pytest.mark.asyncio
async def test_creation_is_authorised_by_the_token_not_by_the_module_name(
    db_session, monkeypatch
):
    """The TOKEN carries the creation — the module exception is belt and braces.

    `commit.py` is on `AUTHORIZED_PROFILE_WRITE_MODULES`, so a constructor left
    outside `authorized_profile_write()` would still look authorised and no
    ordinary test could tell. The contextvar is the real mechanism (ADR-063
    clause 6, "two authorisations, in order of preference"), and PR 9's gate
    asserts the module list stays exactly three entries — so pin the primary
    mechanism on its own by taking the fallback away.
    """
    import applire.models.profile as profile_model
    from applire.services.profile.commit import create_profile_record

    monkeypatch.setattr(
        profile_model,
        "AUTHORIZED_PROFILE_WRITE_MODULES",
        frozenset(
            {"applire/services/profile/snapshots.py", "applire/services/photo.py"}
        ),
    )
    # With the module fallback taken away, only the token can authorise the
    # constructor — and under strict mode an unauthorised one raises.
    record = await create_profile_record(db_session)

    assert record.profile_json == {}


@pytest.mark.asyncio
async def test_record_none_resolves_the_latest_profile(db_session, seeded):
    result = await commit_ops(
        db_session, [UpsertSkill(name="Go", category="technical")], _provenance()
    )

    assert result.record.id == seeded.id


# ── Invariant 3 — the enrichment trail is UNCONDITIONAL ───────────────────────


@pytest.mark.asyncio
async def test_a_no_op_turn_still_leaves_a_receipt(db_session, seeded):
    """The hole this closes: both bridges wrapped the `EnrichmentRecord` append
    in `if receipt_changes:`, so a turn that changed nothing left no trace that
    it happened at all."""
    result = await commit_ops(db_session, [], _provenance(session_id="sub-1"))

    history = seeded.profile_json["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == _SOURCE
    assert history[0]["source_session_id"] == "sub-1"
    assert history[0]["changes"] == []
    assert result.enrichment_record is not None
    assert result.changes == []


@pytest.mark.asyncio
async def test_every_commit_appends_exactly_one_record(db_session, seeded):
    await commit_ops(db_session, [], _provenance())
    await commit_ops(
        db_session, [UpsertSkill(name="Terraform", category="technical")], _provenance()
    )

    assert len(seeded.profile_json["metadata"]["enrichment_history"]) == 2


# ── Invariant 7 — receipt separation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_demotion_is_receipted_but_is_not_a_change(db_session, seeded):
    result = await commit_ops(
        db_session, [DemoteSkill(name="Kubernetes", declared_denial="Kubernetes")], _provenance()
    )

    assert result.demotions, "the demotion must be receipted"
    assert result.changes == [], "…and must never read as gap-addressing content"
    receipt = seeded.profile_json["metadata"]["enrichment_history"][0]["changes"]
    assert len(receipt) == len(result.demotions)
    assert seeded.profile_json["skills"][0]["status"] == "denied"


@pytest.mark.asyncio
async def test_a_denial_is_receipted_but_is_not_a_change(db_session, seeded):
    result = await commit_ops(
        db_session,
        [],
        _provenance(),
        grounding=TurnGrounding(
            text="I have never touched Ansible.", denials=["Ansible"]
        ),
    )

    assert result.denials, "the denial must be receipted"
    assert result.changes == []
    denied = [d["concept"] for d in seeded.profile_json["metadata"]["denied_concepts"]]
    assert "Ansible" in denied
    receipt = seeded.profile_json["metadata"]["enrichment_history"][0]["changes"]
    assert len(receipt) == len(result.denials)


@pytest.mark.asyncio
async def test_the_receipt_carries_all_three_lists(db_session, seeded):
    result = await commit_ops(
        db_session,
        [
            UpsertSkill(name="Podman", category="technical"),
            DemoteSkill(name="Kubernetes", declared_denial="Kubernetes"),
        ],
        _provenance(),
        grounding=TurnGrounding(
            text="Podman yes; Kubernetes I have never touched.",
            denials=["Kubernetes"],
        ),
    )

    receipt = result.enrichment_record.changes
    assert len(receipt) == len(result.changes) + len(result.denials) + len(
        result.demotions
    ) + len(result.refloored)
    assert result.changes and result.denials and result.demotions


@pytest.mark.asyncio
async def test_a_turn_with_no_persisted_denial_reflooring_reports_none(
    db_session, seeded
):
    """Invariant 2's quiet case. The re-floor's own behaviour is pinned in
    `test_commit_ops_refloor.py`; here it must simply not invent receipts."""
    result = await commit_ops(db_session, [], _provenance())

    assert result.refloored == []


# ── Invariant 4 — completeness recompute is universal ─────────────────────────


@pytest.mark.asyncio
async def test_completeness_is_recomputed_on_every_write(db_session, seeded):
    """Was import-only: the testimony and agent doors both left the stored score
    at whatever the last import wrote."""
    before = seeded.profile_json["metadata"].get("completeness_score", 0.0)

    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Kafka", category="technical")],
        _provenance(),
    )

    assert result.completeness > before
    assert seeded.profile_json["metadata"]["completeness_score"] == result.completeness


# ── Invariant 5 — last_updated + record.updated_at ────────────────────────────


@pytest.mark.asyncio
async def test_last_updated_and_updated_at_both_move(db_session, seeded):
    """Both clocks are written EXPLICITLY, to the same instant as the receipt —
    `record.updated_at` is not left to SQLAlchemy's `onupdate`, which would
    drift from `metadata.last_updated` and from the trail."""
    seeded.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    result = await commit_ops(db_session, [], _provenance())

    stamp = result.enrichment_record.timestamp
    assert seeded.updated_at == stamp
    assert seeded.profile_json["metadata"]["last_updated"] != "2020-01-01T00:00:00Z"
    assert result.profile.metadata.last_updated == stamp


# ── Invariant 6 — deterministic skill enrichment, unconditional ───────────────


@pytest.mark.asyncio
async def test_deterministic_skill_enrichment_runs_without_a_provider(db_session, seeded):
    """No provider is threaded on the testimony/agent doors; the deterministic
    half must still establish a skill's provenance (ADR-058 clause 2 — the same
    edit may not behave differently by door)."""
    await commit_ops(db_session, [], _provenance())

    kubernetes = seeded.profile_json["skills"][0]
    assert kubernetes["source"], "provenance must be stamped"
    assert kubernetes.get("years_experience") is not None


@pytest.mark.asyncio
async def test_enrichment_can_be_switched_off_explicitly(db_session, seeded):
    await commit_ops(db_session, [], _provenance(), enrichment=EnrichPolicy.SKIP)

    assert not seeded.profile_json["skills"][0].get("source")


# ── Invariant 8 — the loadable round-trip is the last gate ────────────────────


@pytest.mark.asyncio
async def test_what_is_assigned_always_re_loads(db_session, seeded):
    from applire.schemas.profile import MasterProfileData

    await commit_ops(
        db_session,
        [UpsertSkill(name="Kafka", category="technical")],
        _provenance(),
    )

    MasterProfileData.model_validate(seeded.profile_json)  # must not raise


@pytest.mark.asyncio
async def test_an_unloadable_metadata_write_never_reaches_the_column(
    db_session, seeded, monkeypatch, caplog
):
    """`apply_ops` round-trips its own output, but everything the committer does
    afterwards touches metadata — so the gate is re-run as the LAST step before
    assignment. Forced here by widening `EnrichmentRecord.source` so the
    committer writes a receipt the load path rejects.
    """
    import applire.services.profile.commit as commit_module
    from applire.schemas.profile import EnrichmentRecord, MasterProfileData

    class _WidenedRecord(EnrichmentRecord):
        source: str  # the real field is a closed Literal

    monkeypatch.setattr(commit_module, "EnrichmentRecord", _WidenedRecord)
    caplog.clear()

    with caplog.at_level("ERROR", logger="applire.services.profile.commit"):
        await commit_ops(
            db_session,
            [UpsertSkill(name="Kafka", category="technical")],
            CommitProvenance(
                source="not_a_declared_source", intake="test", session_id="s"
            ),
        )

    # What landed still loads — that is the whole point of the gate — and it is
    # the untouched previous state: half a turn is never persisted.
    MasterProfileData.model_validate(seeded.profile_json)
    assert seeded.profile_json["metadata"]["enrichment_history"] == []
    assert [s["name"] for s in seeded.profile_json["skills"]] == ["Kubernetes"]
    assert any("round-trip" in r.getMessage() for r in caplog.records)


# ── Flush, not commit (amendment (3)) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_commit_ops_flushes_and_never_commits(db_session, seeded):
    """The committer must not own the transaction: `services/session.py` writes
    `session.state` in the SAME transaction, and a committer that split that
    could desync "gap addressed" from the vault."""
    calls: list[str] = []
    for name in ("commit", "rollback", "refresh"):
        original = getattr(db_session, name)

        async def _spy(*a, _n=name, _o=original, **kw):
            calls.append(_n)
            return await _o(*a, **kw)

        setattr(db_session, name, _spy)

    await commit_ops(
        db_session, [UpsertSkill(name="Kafka", category="technical")], _provenance()
    )

    assert calls == []
    # …and the flush really happened: the row is visible to a fresh SELECT in
    # this transaction.
    row = (
        await db_session.execute(select(MasterProfile).where(MasterProfile.id == seeded.id))
    ).scalar_one()
    assert any(s["name"] == "Kafka" for s in row.profile_json["skills"])


# ── The write guard authorises the committer ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_committers_own_write_is_authorised(db_session, seeded):
    result = await commit_ops(
        db_session, [UpsertSkill(name="Kafka", category="technical")], _provenance()
    )

    # Strict since PR 9: an unauthorised assignment raises inside the call.
    assert any(s["name"] == "Kafka" for s in result.record.profile_json["skills"])


# ── §7.4 — grounding is a parameter; None is a direct act ─────────────────────


@pytest.mark.asyncio
async def test_an_ungrounded_write_is_accepted_as_a_direct_act(db_session, seeded):
    """ADR-061 clause 2 — direct input is `confirmed`; the committer never
    re-adjudicates a manual edit through an LLM."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Kafka", category="technical")],
        _provenance(),
        grounding=None,
    )

    assert result.changes
    assert seeded.profile_json["metadata"]["denied_concepts"] == []


@pytest.mark.asyncio
async def test_grounding_carries_the_turn_text_onto_the_denial_record(db_session, seeded):
    await commit_ops(
        db_session,
        [],
        _provenance(),
        grounding=TurnGrounding(
            text="No LegalTech experience — that is an honest gap.",
            denials=["LegalTech"],
        ),
    )

    denial = seeded.profile_json["metadata"]["denied_concepts"][0]
    assert denial["statement"] == "No LegalTech experience — that is an honest gap."
    assert denial["source"] == _SOURCE


# ── Confirmations and conflicts land on the vault's own channels ──────────────


@pytest.mark.asyncio
async def test_ambiguities_and_applier_confirmations_are_parked(db_session, seeded):
    result = await commit_ops(
        db_session,
        [RequestConfirmation(question="Merge Rheinwerk with Rheinwerk GmbH?")],
        _provenance(),
        ambiguities=[RequestConfirmation(question="Which role is current?")],
    )

    assert len(result.pending_confirmations) == 2
    parked = seeded.profile_json["metadata"]["pending_confirmations"]
    assert [p["question"] for p in parked] == [
        "Which role is current?",
        "Merge Rheinwerk with Rheinwerk GmbH?",
    ]


# ── §7.2 — the embedding parameter ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_embedding_provider_leaves_the_embedding_stale_and_logs_once(
    db_session, seeded, caplog
):
    import applire.services.profile.commit as commit_module

    commit_module.reset_embedding_staleness_log()
    caplog.clear()
    with caplog.at_level("INFO", logger="applire.services.profile.commit"):
        await commit_ops(db_session, [], _provenance())
        await commit_ops(db_session, [], _provenance())
        await commit_ops(db_session, [], _provenance())

    stale = [r for r in caplog.records if "embedding" in r.getMessage()]
    assert len(stale) == 1, "log ONCE, never per write"
    assert seeded.embedding is None


class _EmbeddingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        # A zero vector is the documented "not computed" signal — and the only
        # shape the SQLite test variant of the pgvector column can hold.
        return [0.0] * 1024


@pytest.mark.asyncio
async def test_an_embedding_provider_is_used_when_supplied(db_session, seeded):
    provider = _EmbeddingProvider()

    await commit_ops(
        db_session,
        [UpsertSkill(name="Kafka", category="technical")],
        _provenance(),
        embedding_provider=provider,
    )

    assert provider.calls == 1
    assert seeded.embedding is None  # zero vector → NULL, never persisted


@pytest.mark.asyncio
async def test_without_a_provider_no_embedding_call_is_made(db_session, seeded):
    provider = _EmbeddingProvider()

    await commit_ops(db_session, [], _provenance(), embedding_provider=None)

    assert provider.calls == 0


# ── The snapshot parameter is declared, not yet wired (PR 2) ──────────────────


@pytest.mark.asyncio
async def test_no_snapshot_is_captured_when_none_is_asked_for(db_session, seeded):
    """The other half of PR 2's snapshot parameter, and the one that keeps the
    #339 block honest: `snapshot=None` — every intake but the two import
    writers — must capture NOTHING. PR 1 pinned that `MERGE` raised; the
    superseding pin is that the default writes no snapshot row at all, so a
    ten-turn interview can never evict the import snapshot the undo exists for
    (ADR-063 amendment (5))."""
    from applire.models.profile import ProfileSnapshot

    await commit_ops(
        db_session,
        [UpsertSkill(name="Terraform", category="technical")],
        _provenance(),
    )
    await db_session.commit()

    rows = (await db_session.execute(select(ProfileSnapshot))).scalars().all()
    assert rows == []


# ── #480 PR 2 — `snapshot=MERGE` is real (ADR-042) ────────────────────────────


@pytest.mark.asyncio
async def test_merge_snapshot_captures_the_state_before_the_ops_land(db_session, seeded):
    """The bytes an undo restores are the PRE-op vault, not the post-op one —
    a snapshot of the result would restore nothing."""
    before = dict(seeded.profile_json)

    await commit_ops(
        db_session,
        [UpsertSkill(name="Terraform", category="technical")],
        _provenance(),
        snapshot=SnapshotClass.MERGE,
    )
    await db_session.commit()

    rows = (await db_session.execute(select(ProfileSnapshot))).scalars().all()
    assert len(rows) == 1
    assert rows[0].profile_json == before
    assert [s["name"] for s in rows[0].profile_json["skills"]] == ["Kubernetes"]
    # ...and the vault itself did move.
    assert sorted(s["name"] for s in seeded.profile_json["skills"]) == [
        "Kubernetes",
        "Terraform",
    ]


@pytest.mark.asyncio
async def test_merge_snapshot_is_keyed_to_the_record_this_write_minted(db_session, seeded):
    """`undo_last_merge` compares the snapshot's key against the profile's head
    enrichment record to decide whether later edits are being discarded. The
    committer mints that record, so it is the only thing that can key the
    snapshot honestly."""
    result = await commit_ops(
        db_session,
        [UpsertSkill(name="Terraform", category="technical")],
        _provenance(),
        snapshot=SnapshotClass.MERGE,
    )
    await db_session.commit()

    rows = (await db_session.execute(select(ProfileSnapshot))).scalars().all()
    assert rows[0].enrichment_record_id == result.enrichment_record.id
    history = seeded.profile_json["metadata"]["enrichment_history"]
    assert history[-1]["id"] == result.enrichment_record.id


@pytest.mark.asyncio
async def test_merge_snapshot_rides_the_callers_transaction(db_session, seeded):
    """`capture_pre_merge_snapshot` is called INSIDE `commit_ops`, which
    flushes and never commits — so the snapshot and the merge it protects are
    atomic in the caller's transaction. A caller that rolls back gets neither."""
    await commit_ops(
        db_session,
        [UpsertSkill(name="Terraform", category="technical")],
        _provenance(),
        snapshot=SnapshotClass.MERGE,
    )
    await db_session.rollback()

    rows = (await db_session.execute(select(ProfileSnapshot))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_merge_snapshot_carries_the_merge_statistics_onto_the_receipt(
    db_session, seeded
):
    """US161 (ADR-041 amended) — `EnrichmentRecord.reconciliation` is the
    profile-health surface's silent-data-loss detector (FMEA JF-M-3.3). Only an
    import can compute it, so it rides out of the applier on `ApplyImportMerge`
    and lands on the record the committer mints."""
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.ops import ApplyImportMerge

    merged = MasterProfileData.model_validate(
        {
            **{k: v for k, v in _SEED.items() if k != "metadata"},
            "skills": [
                {"name": "Kubernetes", "category": "technical", "status": "confirmed"},
                {"name": "Terraform", "category": "technical", "status": "confirmed"},
            ],
        }
    )
    result = await commit_ops(
        db_session,
        [
            ApplyImportMerge(
                merged=merged,
                changes=[],
                reconciliation={"skills": {"extracted": 2, "stored": 2, "delta": 0}},
            )
        ],
        CommitProvenance(source="cv_upload", intake="import", actor="candidate"),
        snapshot=SnapshotClass.MERGE,
        enrichment=EnrichPolicy.SKIP,
    )
    await db_session.commit()

    assert result.enrichment_record.reconciliation == {
        "skills": {"extracted": 2, "stored": 2, "delta": 0}
    }
    stored = seeded.profile_json["metadata"]["enrichment_history"][-1]
    assert stored["reconciliation"] == {"skills": {"extracted": 2, "stored": 2, "delta": 0}}


@pytest.mark.asyncio
async def test_a_non_merge_write_leaves_the_reconciliation_field_empty(db_session, seeded):
    result = await commit_ops(
        db_session, [UpsertSkill(name="Terraform", category="technical")], _provenance()
    )
    assert result.enrichment_record.reconciliation is None
