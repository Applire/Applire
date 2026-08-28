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

"""US186 (E035) — one-time reshape of existing flat duplicates into the typed model.

Existing profiles accumulated flat duplicate rows BEFORE the ADR-046 reconciler
existed: synonym job titles stored as separate work entries, project work flattened
into standalone roles, DE/EN translations of the same employer kept apart. This
module feeds each such profile back through the SAME engine (``reconcile`` +
``apply_ops``) so the duplicates fold and orphan project roles nest — reusing the
LLM-proposer / code-disposer contract rather than writing a parallel lexical
de-dup (exactly what E035 retired).

Approach (the reuse): the engine reconciles *new info* against a *profile*. For an
intra-profile cleanup we pass the profile as BOTH the context (so the LLM sees the
real entity ``id``s to ``target`` / ``parent`` against) AND — as the ``new_info`` —
a flattened rendering of the profile's own messy entries. The reconciler then emits
``upsert_work(target=<existing id>, role=<synonym>)`` to fold a synonym title and
``upsert_project(parent=<existing id>)`` to nest an orphan project role. The
deterministic applier does the rest.

Boundaries:
- ADR-040 (truthful): only engine-proposed ops are applied; an ambiguity the engine
  cannot resolve is RECORDED and the entry is left unchanged (never auto-resolved,
  never an invented link).
- ADR-042 (reversible): reversibility was the RUNNER's job, never this module's —
  the one-time pass took a pre-reshape snapshot before persisting each profile.
- The reshape itself is PURE: it deep-copies, never persists, never calls
  ``get_provider()`` — the provider is injected.

**No production importer, since #480 PR 9.** The runner
(``scripts/migrate_flat_duplicates.py``) shipped in the ``v0.37.0-beta`` …
``v0.38.0-beta`` releases and was deleted as a spent one-time pass — and as an
unrouted vault writer, which is what actually surfaced it (ADR-063 clause 6).
This is now a pure library function whose only caller is
``backend/tests/unit/test_migrate_flat_duplicates.py``. It is kept rather than
deleted alongside the runner because the reshape logic is the reusable half: any
future caller inherits BOTH obligations the runner carried — take the snapshot,
and persist through ``commit_ops``, which the deleted script did not do.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import (
    RequestConfirmation,
    UpsertProject,
)


@dataclass
class ReshapeOutcome:
    """The result of reshaping one profile (no persistence)."""

    profile: MasterProfileData
    changed: bool
    folds: int  # entity ops that merged into an existing entity (synonym fold / dup merge)
    projects_nested: int  # projects given a parent (associated_experience set)
    entries_before: int
    entries_after: int
    ambiguities: list[RequestConfirmation] = field(default_factory=list)
    conflicts: list = field(default_factory=list)


def _flatten_for_reconcile(profile: MasterProfileData) -> dict:
    """Render the profile's own entries as the ``new_info`` material to re-reconcile.

    We hand the engine the SAME structured entries it already has (including ids),
    framed as "information to fold back in". The reconciler is told (via the prompt
    rules) to target existing ids and nest project work — so re-presenting the
    entries is what triggers the fold/nest decisions. We pass the experience-bearing
    sections only; scalar/personal sections cannot duplicate-fold and would just add
    prompt noise.
    """
    dump = profile.model_dump(mode="json")
    return {
        "work_experience": dump.get("work_experience", []),
        "projects": dump.get("projects", []),
        "volunteer_activities": dump.get("volunteer_activities", []),
        "skills": dump.get("skills", []),
    }


def _count_entries(profile: MasterProfileData) -> int:
    return (
        len(profile.work_experience)
        + len(profile.projects)
        + len(profile.volunteer_activities)
    )


async def reshape_profile(
    profile: MasterProfileData,
    provider: LLMProvider,
    source: str = "migration",
    lang: str = "en",
) -> ReshapeOutcome:
    """Reshape one profile's flat duplicates into the typed model via the engine.

    PURE: operates on the engine's deep copy, never persists. The engine
    degrades to empty ops on LLM noise, so a provider failure alone still
    yields a no-op ``ReshapeOutcome`` (``changed=False``) — but this is no
    longer guaranteed non-raising end to end (ADR-063 amended 2026-08-28,
    #597): ``apply_ops`` now raises ``VaultWriteRevertedError`` instead of
    silently reverting when its own defence-in-depth reload gate trips on a
    schema-rejecting op result. Not translated here, unlike the doors named
    in the amendment (testimony/claims/import) — this function has no
    production caller today (only this module's own unit tests), so the
    contract change has no live blast radius; a future caller inherits the
    same propagate-uncaught default every other undecided door gets.
    """
    entries_before = _count_entries(profile)

    result = await reconcile(
        profile,
        _flatten_for_reconcile(profile),
        source,
        provider,
        lang,
    )
    applied = apply_ops(profile, result.ops, source)

    # Projects nested = upsert_project ops that name a parent (the engine resolved
    # an orphan project role onto an existing job/volunteer). Counted from the ops
    # the engine actually proposed — never inferred.
    projects_nested = sum(
        1 for op in result.ops if isinstance(op, UpsertProject) and op.parent is not None
    )
    # Folds = entity ops that merged into an existing entity (target set) — synonym
    # role folds and DE/EN employer dup merges.
    folds = sum(
        1
        for op in result.ops
        if getattr(op, "target", None) is not None
    )

    ambiguities = list(result.ambiguities) + list(applied.pending_confirmations)

    changed = bool(applied.changes)

    return ReshapeOutcome(
        profile=applied.profile,
        changed=changed,
        folds=folds,
        projects_nested=projects_nested,
        entries_before=entries_before,
        entries_after=_count_entries(applied.profile),
        ambiguities=ambiguities,
        conflicts=list(applied.conflicts),
    )
