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

"""US184 — import-path bridge over the ADR-046 reconciler.

CV / LinkedIn / PDF / cv-paste import reconciles a WHOLE incoming MasterProfileData
into the existing profile via one reconcile() + apply_ops(), returning the existing
MergeResult shape so the upload/import call sites, the ADR-042 snapshot, and the
response contract are unchanged. Drop-in for the retired lexical merge_profiles."""
from __future__ import annotations

from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import Conflict, MasterProfileData
from applire.services.profile.merge import MergeResult
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import RequestConfirmation
from applire.services.profile.reconciliation import compute_merge_reconciliation


def _to_conflict(rc: RequestConfirmation, source: str) -> Conflict:
    """Map an engine ambiguity (a RequestConfirmation) onto the import path's
    Conflict shape so it surfaces on the existing conflict-resolution UI.

    The confirmation's question becomes the (truncated) ``field`` label and its
    options become the ``incoming_value`` to choose from; ``existing_value`` is
    ``None`` because an ambiguity has no single prior value to contrast against."""
    return Conflict(
        section="",
        field=(rc.question[:64] if rc.question else "ambiguity"),
        existing_value=None,
        incoming_value=rc.options,
        source=source,
        suggested_resolution=rc.question or None,
    )


async def reconcile_import(
    existing: MasterProfileData,
    incoming: MasterProfileData,
    source: str,
    provider: LLMProvider,
    lang: str = "en",
) -> MergeResult:
    """Reconcile a WHOLE incoming profile into ``existing`` via the ADR-046 engine.

    Drop-in for the lexical ``merge_profiles``: one ``reconcile`` call + one
    deterministic ``apply_ops``, returning the existing ``MergeResult`` shape.
    Never raises — the engine degrades to empty ops on LLM noise, ``apply_ops``
    is pure, and ``compute_merge_reconciliation`` is deterministic."""
    result = await reconcile(existing, incoming, source, provider, lang)
    applied = apply_ops(existing, result.ops, source)
    ambiguities = list(result.ambiguities) + list(applied.pending_confirmations)
    conflicts = list(applied.conflicts) + [_to_conflict(a, source) for a in ambiguities]
    added = [
        (c.new_value if isinstance(c.new_value, str) else f"{c.section}.{c.field}")
        for c in applied.changes
    ]
    return MergeResult(
        merged_profile=applied.profile,
        added=added,
        conflicts=conflicts,
        changes=applied.changes,
        reconciliation=compute_merge_reconciliation(incoming, applied.profile),
    )
