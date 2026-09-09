# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#684 family — bullets carried onto a pending confirmation, and what happens
when the candidate answers.

**Status: the defect this file was written to reproduce is FIXED** (founder
ruling V-5, 2026-09-09). The file is kept as the regression, with the two
"lost" assertions flipped to "landed" — the reproduction's own record of what
used to happen stays in this docstring, because a fix whose defect is no longer
described is a fix nobody can evaluate.

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

**What the resolution turn does now** (`session._apply_engagement_confirmation`
+ `apply.UserConfirmedEngagement`): the parked op is rebuilt from the
confirmation's own `context["incoming"]`, the answer travels as a capability
rather than an op field (ADR-063 clause 1), `"distinct"` creates the entry with
the #177 guard skipped, `"merge"` folds it into the id the builder recorded in
`context["existing_ids"]`, and `pending_bullets` ride along as an `AddBullets`
op against the same local ref so they land on whichever entity results.
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


def test_resolve_confirmation_alone_is_still_only_bookkeeping():
    """`ResolveConfirmation` on its own writes no content — BY DESIGN, and this
    stays true after the fix.

    ADR-063 design §4.5 makes that op the lifecycle act: it closes the park and
    receipts the answer. What changed with founder ruling V-5 is that the
    session layer now ALSO emits the rebuilt entity op beside it
    (`_apply_engagement_confirmation`), which is where the content comes from.
    Pinning the separation matters: if a future change makes
    `_apply_resolve_confirmation` write content, two paths will be writing the
    same entity and they will diverge.
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


def test_the_carrier_now_has_exactly_one_writer_AND_one_reader():
    """The absence claim, re-run after the fix — and it has flipped.

    Before founder ruling V-5 this asserted ONE writer and NO reader, proven by
    exhausting the positive set rather than measuring the negative one. That was
    the finding. Now the enumeration must show the reader too, and name it: if a
    future refactor drops `_apply_engagement_confirmation`'s read, the bullets
    go back to being written into a void and this reddens.
    """
    import ast

    root = Path(__file__).resolve().parents[2] / "applire"
    files = sorted(
        {
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if "pending_bullets" in path.read_text(encoding="utf-8")
        }
    )
    assert files == [
        "services/profile/reconcile/apply.py",   # the writer
        "services/session.py",                   # the reader (V-5)
    ], files

    owners: set[str] = set()
    for rel in files:
        src = (root / rel).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                "pending_bullets" in (ast.get_source_segment(src, node) or "")
            ):
                owners.add(node.name)
    # `_apply_add_bullets` writes it; `_apply_engagement_confirmation` reads it;
    # `_apply_interview_confirmation` only NAMES it, in the comment recording why
    # its early return stopped being an early return. Three mentions, one of
    # which is prose — which is exactly why this test enumerates function names
    # rather than trusting a grep count to mean "consumers".
    assert owners == {
        "_apply_add_bullets",
        "_apply_engagement_confirmation",
        "_apply_interview_confirmation",
    }, owners
