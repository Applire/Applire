# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Reproduction: bullets carried onto a pending confirmation are never applied.

Found by WP-O3's fate trace during Nougat build 1 (2026-09-08, code read) and
reproduced here against the real applier before any fix was scoped.

**The carrier.** When an entity op is AMBIGUOUS (`classify_engagement_dupe`
returns candidates rather than a match), `_apply_upsert_work` / `_project` /
`_volunteer` park a `RequestConfirmation` and deliberately leave the op's local
`ref` OUT of `ref_map`. A co-batched `AddBullets` targeting that ref therefore
resolves to nothing, and `_apply_add_bullets` carries its bullets onto the
confirmation's `context["pending_bullets"]` — with the stated intent *"so the
resolution turn can apply them instead of losing them silently"*
(`reconcile/apply.py`, the `entity is None` branch).

**The resolution turn does not exist.** Established by exhausting the positive
set rather than by grepping for an absence:

* `pending_bullets` has exactly ONE writer and ZERO production readers
  (`grep -rn pending_bullets` over the whole tree returns the write site and two
  assertions in `test_reconcile_apply.py` — and those assert only that the
  carrier was WRITTEN, never that anything acts on it. Partial consumption
  reading as consumption is the trap; a test is not a consumer).
* `_apply_resolve_confirmation` is bookkeeping by design (ADR-063 design §4.5):
  it clears the park and receipts the answer. It never touches `context`.
* `session._apply_interview_confirmation` returns early for anything that is not
  a skill confirmation, and says so: *"Not a skill confirmation (entity
  near-dupe etc.) — advancing is enough to break the loop; entity-merge
  resolution is out of #187's scope."*

So the loss is WIDER than the carrier: on the interview path the ambiguous
ENTITY is never created or merged either, whichever option the candidate picks.
`pending_bullets` is a lifeboat for a rescue that was never built, and its
presence makes the code read as though the bullets are safe.

These tests pin the behaviour as it is. They are written to FAIL when the
resolution turn lands, which is the point — the fix's own regression test is
this file with the assertions inverted.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, ProfileMetadata  # noqa: E402
from applire.services.profile.reconcile.apply import apply_ops  # noqa: E402
from applire.services.profile.reconcile.ops import (  # noqa: E402
    AddBullets,
    ResolveConfirmation,
    UpsertWork,
)


def _profile_with_a_near_dupe_employer() -> MasterProfileData:
    """A vault holding one UNDATED position at a near-dupe org.

    Same shape `test_reconcile_apply.py::test_ambiguous_work_upsert_addbullets_
    on_its_ref_carries_into_confirmation` uses, so this file reproduces against
    the exact input the carrier was written for: near-dupe org, no start date on
    either side, which `classify_engagement_dupe` calls AMBIGUOUS rather than a
    match (the #177 guard's "ask, never guess" arm).
    """
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "work_experience": [
                {
                    "id": "w-helv",
                    "company": "Helvetia Pharma GmbH",
                    "role": "Systems Engineer",
                }
            ],
            "metadata": ProfileMetadata().model_dump(mode="json"),
        }
    )


def _ambiguous_batch() -> list:
    """An ambiguous `upsert_work` plus an `AddBullets` targeting its local ref."""
    return [
        UpsertWork(ref="w1", company="Helvetia Pharma", role="Systems Engineer"),
        AddBullets(
            target="w1",
            responsibilities=["Verantwortlich für die Qualifizierung der MES-Anbindung"],
            achievements=["OEE von 61 % auf 73 % gesteigert"],
        ),
    ]


def test_an_ambiguous_entity_op_parks_a_confirmation_and_creates_nothing():
    """The precondition. Nothing is wrong yet — the system asks instead of guessing."""
    profile = _profile_with_a_near_dupe_employer()
    result = apply_ops(profile, _ambiguous_batch(), "interview")

    assert len(result.pending_confirmations) == 1
    # The ambiguous op did not create a second Helvetia entry.
    assert len(result.profile.work_experience) == 1


def test_the_candidates_bullets_are_carried_onto_the_confirmation_and_nowhere_else():
    """The bullets reach the confirmation's context — and no entity."""
    profile = _profile_with_a_near_dupe_employer()
    result = apply_ops(profile, _ambiguous_batch(), "interview")

    carried = result.pending_confirmations[0].context.get("pending_bullets")
    assert carried == {
        "responsibilities": ["Verantwortlich für die Qualifizierung der MES-Anbindung"],
        "achievements": ["OEE von 61 % auf 73 % gesteigert"],
    }
    # Not on the existing entry either — the guard did not silently fall back.
    existing = result.profile.work_experience[0]
    assert existing.responsibilities == []
    assert existing.achievements == []


def test_resolving_the_confirmation_clears_the_park_and_applies_no_bullet():
    """THE DEFECT. The candidate answers; the park closes; the bullets vanish.

    `_apply_resolve_confirmation` is bookkeeping by design and never reads
    `context`. Nothing else does either. So a candidate who answered a question
    the system asked them loses the content that question was about, with a
    receipt that says their answer was recorded.
    """
    profile = _profile_with_a_near_dupe_employer()
    first = apply_ops(profile, _ambiguous_batch(), "interview")

    # Park it durably, exactly as the committer does.
    parked = first.profile
    from applire.schemas.profile import PendingConfirmation

    conf = first.pending_confirmations[0]
    parked.metadata.pending_confirmations = [
        PendingConfirmation(
            question=conf.question,
            options=list(conf.options),
            context=dict(conf.context),
            source="interview",
            option_keys=list(conf.option_keys),
        )
    ]
    confirmation_id = parked.metadata.pending_confirmations[0].confirmation_id

    resolved = apply_ops(
        parked,
        [
            ResolveConfirmation(
                confirmation_id=confirmation_id,
                chosen_option="Different — keep both",
            )
        ],
        "interview",
    )

    # The park is closed and the answer receipted …
    assert resolved.profile.metadata.pending_confirmations == []
    assert any(
        c.rationale_key == "confirmation_resolved" for c in resolved.changes
    )
    # … and NOTHING carries the bullets. Neither a new entry for the
    # "keep both" answer, nor the existing entry, nor any receipt naming them.
    assert len(resolved.profile.work_experience) == 1
    assert resolved.profile.work_experience[0].responsibilities == []
    assert resolved.profile.work_experience[0].achievements == []
    assert not any(
        "MES-Anbindung" in str(c.new_value) or "OEE" in str(c.new_value)
        for c in resolved.changes
    )
    # And no `not_applied` item names the loss, so no surface can report it.
    assert resolved.not_applied == []


def test_the_carrier_has_exactly_one_writer_and_no_production_reader():
    """The absence, proven by exhausting the POSITIVE set rather than measuring
    the negative one — the rule this codebase uses for every "nothing reads X".

    Scans `backend/applire/` for the literal and asserts every occurrence sits
    in the ONE function that writes it. Comments and the diagnostic WARNING name
    it too — those are not readers, which is exactly the distinction that makes
    this check worth having: a grep for the field name finds "consumers" and
    passes, so the test names the FILE and asserts nothing else mentions it.
    When a resolution turn lands in another module, this reddens and is the
    place to record its reader.
    """
    root = Path(__file__).resolve().parents[2] / "applire"
    files = sorted(
        {
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if "pending_bullets" in path.read_text(encoding="utf-8")
        }
    )
    assert files == ["services/profile/reconcile/apply.py"], files

    # …and inside that file, every mention is inside `_apply_add_bullets`.
    src = (root / "services/profile/reconcile/apply.py").read_text(encoding="utf-8")
    import ast

    tree = ast.parse(src)
    owners = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and "pending_bullets" in ast.get_source_segment(src, node)
    }
    assert owners == {"_apply_add_bullets"}, owners
