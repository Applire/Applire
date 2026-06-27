#!/usr/bin/env python3
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

"""US186 (E035) — one-time CLI pass that reshapes existing flat duplicates.

Iterates every non-deleted ``master_profiles`` row, feeds it through the ADR-046
engine (``reshape_profile``) so synonym roles fold, DE/EN employer duplicates
merge, and orphan project roles nest under their parent, then persists the cleaned
profile. An ADR-042 pre-reshape snapshot is taken before each rewrite so the pass
is reversible (``undo_last_merge``).

Idempotent: a profile the engine proposes no changes for is skipped — no snapshot,
no write, no enrichment record. Re-running the script on already-clean data is a
no-op.

This is a JSONB *data* migration (the profile lives in ``profile_json``), not DDL —
no Alembic revision. Standalone async script (the ``scripts/`` precedent).

Usage:
    # Dry run — compute + print every profile's reshape, persist NOTHING:
    PYTHONPATH=backend python3 scripts/migrate_flat_duplicates.py --dry-run

    # Reshape every profile (takes a snapshot + persists each changed one):
    PYTHONPATH=backend python3 scripts/migrate_flat_duplicates.py

    # Target a single profile by id:
    PYTHONPATH=backend python3 scripts/migrate_flat_duplicates.py --profile-id <uuid>

The LLM provider is the configured factory provider (``get_provider()``) — set
``LLM_PROVIDER`` / the matching key in ``.env`` first. Use ``mock`` to dry-run the
plumbing without an LLM key.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.db.session import AsyncSessionLocal
from applire.models.profile import MasterProfile
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import (
    EnrichmentRecord,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.profile.reconcile.migrate import ReshapeOutcome, reshape_profile
from applire.services.profile.snapshots import capture_pre_merge_snapshot


@dataclass
class ProfileReport:
    """Per-profile outcome of the migration pass (printed in the summary)."""

    profile_id: uuid.UUID
    changed: bool
    persisted: bool
    entries_before: int
    entries_after: int
    folds: int
    projects_nested: int
    ambiguities: int
    conflicts: int
    error: str | None = None


def _enrichment_record(outcome: ReshapeOutcome) -> EnrichmentRecord:
    """Build the migration's enrichment record (keys the ADR-042 snapshot)."""
    from applire.schemas.profile import FieldChange

    return EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="manual_edit",  # closest allowed source; "migration" is not in the enum
        changes=[
            FieldChange(
                section="*",
                field="*",
                action="merged",
                new_value={
                    "folds": outcome.folds,
                    "projects_nested": outcome.projects_nested,
                },
                rationale="US186 one-time flat-duplicate reshape into the typed model.",
                rationale_key="migrate_flat_duplicates",
            )
        ],
    )


async def _reshape_one(
    db: AsyncSession,
    record: MasterProfile,
    provider: LLMProvider,
    *,
    dry_run: bool,
) -> ProfileReport:
    """Reshape a single profile record; snapshot + persist unless ``dry_run``.

    Idempotent: when the engine proposes no change, nothing is snapshotted or
    written. Never raises on a single bad profile — the error is captured in the
    report so the batch continues.
    """
    try:
        profile = MasterProfileData.model_validate(record.profile_json)
    except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
        return ProfileReport(
            profile_id=record.id,
            changed=False,
            persisted=False,
            entries_before=0,
            entries_after=0,
            folds=0,
            projects_nested=0,
            ambiguities=0,
            conflicts=0,
            error=f"validate failed: {exc}",
        )

    outcome = await reshape_profile(profile, provider, source="migration")

    report = ProfileReport(
        profile_id=record.id,
        changed=outcome.changed,
        persisted=False,
        entries_before=outcome.entries_before,
        entries_after=outcome.entries_after,
        folds=outcome.folds,
        projects_nested=outcome.projects_nested,
        ambiguities=len(outcome.ambiguities),
        conflicts=len(outcome.conflicts),
    )

    # Idempotency: no engine-proposed change → no snapshot, no write.
    if not outcome.changed or dry_run:
        return report

    cleaned = outcome.profile
    now = datetime.now(timezone.utc)
    enrichment = _enrichment_record(outcome)

    if cleaned.metadata is None:
        cleaned.metadata = ProfileMetadata(
            completeness_score=cleaned.calculate_completeness(),
            created_via="manual",
            created_at=record.created_at,
            last_updated=now,
            enrichment_history=[enrichment],
        )
    else:
        cleaned.metadata.completeness_score = cleaned.calculate_completeness()
        cleaned.metadata.last_updated = now
        cleaned.metadata.enrichment_history.append(enrichment)

    # ADR-042: snapshot the pre-reshape JSON BEFORE overwriting (reversible pass).
    await capture_pre_merge_snapshot(
        db,
        profile_id=record.id,
        profile_json=record.profile_json,
        enrichment_record_id=enrichment.id,
    )

    record.profile_json = cleaned.model_dump(mode="json")
    record.updated_at = now
    await db.commit()
    await db.refresh(record)
    report.persisted = True
    return report


async def run_migration(
    db: AsyncSession,
    provider: LLMProvider,
    *,
    dry_run: bool = False,
    profile_id: uuid.UUID | None = None,
) -> list[ProfileReport]:
    """Reshape all non-deleted profiles (or one, if ``profile_id`` is given).

    Returns one ``ProfileReport`` per profile processed. Testable with an injected
    session + provider; the CLI ``main`` is a thin wrapper over this.
    """
    query = select(MasterProfile).where(MasterProfile.deleted_at.is_(None))
    if profile_id is not None:
        query = query.where(MasterProfile.id == profile_id)
    query = query.order_by(MasterProfile.created_at.asc())

    records = list((await db.execute(query)).scalars().all())

    reports: list[ProfileReport] = []
    for record in records:
        reports.append(await _reshape_one(db, record, provider, dry_run=dry_run))
    return reports


def _print_reports(reports: list[ProfileReport], *, dry_run: bool) -> None:
    mode = "DRY RUN (nothing persisted)" if dry_run else "APPLY"
    print(f"\n=== US186 flat-duplicate reshape — {mode} ===")
    if not reports:
        print("  no profiles to process.")
        return
    changed = 0
    for r in reports:
        if r.error:
            print(f"  [{r.profile_id}] ERROR: {r.error}")
            continue
        verb = (
            "WOULD CHANGE"
            if (r.changed and dry_run)
            else ("CHANGED" if r.persisted else "no change")
        )
        if r.changed:
            changed += 1
        print(
            f"  [{r.profile_id}] {verb}: "
            f"entries {r.entries_before}->{r.entries_after}, "
            f"folds={r.folds}, projects_nested={r.projects_nested}, "
            f"ambiguities={r.ambiguities}, conflicts={r.conflicts}"
        )
    print(
        f"\n  {len(reports)} profile(s) processed, "
        f"{changed} with reshape{'s (not persisted)' if dry_run else 's persisted'}.\n"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US186 — reshape existing flat duplicates into the typed model."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the reshape for every profile; persist nothing.",
    )
    parser.add_argument(
        "--profile-id",
        type=uuid.UUID,
        default=None,
        help="Target a single profile by UUID (default: all non-deleted profiles).",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    # The provider is the configured factory provider — never hard-wired. Set
    # LLM_PROVIDER=mock to exercise the plumbing without a key.
    from applire.providers import get_provider

    provider = get_provider()

    async with AsyncSessionLocal() as db:
        reports = await run_migration(
            db,
            provider,
            dry_run=args.dry_run,
            profile_id=args.profile_id,
        )
    _print_reports(reports, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
