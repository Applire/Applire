# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:190 — the segmented import fallback (ADR-047) keeps every slice's
binding receipts. Before, `_reconcile_import_batched` dropped `matched`, so a
large import showed no recognised pairs and offered no #717 undo."""

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile import import_bridge
from applire.services.profile.reconcile.ops import MatchExisting, ReconcileResult


@pytest.mark.asyncio
async def test_the_batched_fallback_keeps_each_slices_matched_receipts(monkeypatch):
    existing = MasterProfileData.model_validate({
        "skills": [{"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"}],
        "languages": [{"id": "l-de", "language": "Deutsch", "level": "native"}],
    })
    incoming = MasterProfileData.model_validate({
        "skills": [{"name": "Python 3", "category": "technical"}],
        "work_experience": [{"company": "Rheinwerk GmbH", "role": "Engineer", "start_date": "2020-01"}],
    })
    calls: list[int] = []

    async def fake_reconcile(current, slice_info, source, provider, lang):
        calls.append(1)
        if slice_info.skills:
            return ReconcileResult(ops=[MatchExisting(target="s-py", incoming="Python 3")])
        return ReconcileResult(ops=[])

    async def no_hold(current, ops, provider):
        return ops, []

    monkeypatch.setattr(import_bridge, "reconcile", fake_reconcile)
    monkeypatch.setattr(import_bridge, "hold_back_restatements", no_hold)

    applied, *_ = await import_bridge._reconcile_import_batched(
        existing, incoming, "cv_upload", provider=None, lang="de"
    )
    assert len(calls) >= 2  # really segmented
    assert [(m.entity_id, m.incoming) for m in applied.matched] == [("s-py", "Python 3")]
