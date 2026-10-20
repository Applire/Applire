# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""783:L7 / L60 (ADR-063 amended 2026-10-20) — an op whose ``target`` names no
entity is receipted on ``not_applied`` as ``target_unresolved``, never dropped
with a log line only.

Reproduction (before the fix): ``add_bullets(target=<a UUID the vault does not
hold>)`` returned ``changes == []`` and ``not_applied == []`` — the bullets were
gone and nothing but a ``logger.warning`` said so (Nougat gap-coverage B record
#8: ``7b568a19…`` written for the real ``31a79718…``, EKS-migration bullets
lost). The import witness compares ENTRIES, so a bullet list lost on an entry
that exists passes it.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EnrichmentRecord,
    ImportNotApplied,
    MasterProfileData,
    ProfileMetadata,
    is_loss_reason,
)
from applire.services.profile.health import _not_applied_issue  # noqa: E402
from applire.services.profile.reconcile.apply import apply_ops  # noqa: E402
from applire.services.profile.reconcile.ops import (  # noqa: E402
    AddBullets,
    SetField,
    UpsertWork,
)

_REAL = "31a79718-0000-4000-8000-000000000001"
_TYPO = "7b568a19-0000-4000-8000-000000000001"


def _vault() -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "work_experience": [
                {"id": _REAL, "company": "Nordwind Logistik GmbH", "role": "Platform Engineer"}
            ],
            "metadata": ProfileMetadata().model_dump(mode="json"),
        }
    )


def test_add_bullets_on_an_unknown_target_is_receipted_as_loss():
    result = apply_ops(
        _vault(),
        [AddBullets(target=_TYPO, achievements=["EKS-Migration von 40 Services in 3 Monaten"])],
        "interview",
    )
    assert result.changes == []
    items = [i for i in result.not_applied if i.reason == "target_unresolved"]
    assert len(items) == 1
    assert items[0].section is None
    assert "EKS-Migration" in items[0].label
    assert is_loss_reason("target_unresolved")
    # the vault entry that exists did not silently receive or lose anything
    assert result.profile.work_experience[0].achievements == []


def test_add_bullets_on_the_real_target_is_not_receipted():
    result = apply_ops(
        _vault(),
        [AddBullets(target=_REAL, achievements=["EKS-Migration von 40 Services in 3 Monaten"])],
        "interview",
    )
    assert result.changes
    assert not [i for i in result.not_applied if i.reason == "target_unresolved"]


def test_add_bullets_on_a_local_ref_of_this_batch_is_not_receipted():
    result = apply_ops(
        _vault(),
        [
            UpsertWork(ref="w1", company="Seeblick Reederei AG", role="SRE"),
            AddBullets(target="w1", responsibilities=["Betrieb der Kubernetes-Cluster"]),
        ],
        "interview",
    )
    assert not [i for i in result.not_applied if i.reason == "target_unresolved"]


def test_set_field_on_an_unknown_target_is_receipted_as_loss():
    result = apply_ops(
        _vault(),
        [SetField(target=_TYPO, field="location", value="Hamburg")],
        "interview",
    )
    items = [i for i in result.not_applied if i.reason == "target_unresolved"]
    assert len(items) == 1
    assert "location" in items[0].label and "Hamburg" in items[0].label


def test_the_receipt_reaches_the_health_thread():
    item = ImportNotApplied(section=None, label="add_bullets: EKS-Migration", reason="target_unresolved")
    record = EnrichmentRecord(
        timestamp="2026-10-20T10:00:00Z", source="interview", not_applied=[item]
    )
    issue = _not_applied_issue(record)
    assert issue is not None
    assert "target_unresolved" not in issue.summary  # a sentence, not the raw key
