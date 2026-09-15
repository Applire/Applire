# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#705 (founder UAT on the Nougat RC, 2026-09-15) — the not-retained overlay
names WHICH items, not just a 3-item-capped sentence.

`HealthIssue.not_applied_labels`/`_reasons` (V-7) cap at three items and are
two PARALLEL lists — a reader cannot tell which reason belongs to which item
once there is more than one reason in play, and the fourth-and-later item is
invisible everywhere except the "and N more" tail. `not_applied_items` (#705)
carries the FULL, per-item ``{section, label, reason}`` receipt — reusing
``ImportNotApplied`` itself rather than a parallel shape — sorted by section so
a grouped reader renders in a stable order.

Also pins ruling U-4 (2026-09-15): the SAME 23 lost items must not render as
TWO Health threads (`accuracy` AND `not_applied`) with different severities —
`_accuracy_issue`'s loss branch retires in favour of `not_applied` whenever a
record carries a `not_applied` witness, and stays exactly as before for a
legacy record that does not (predating #615).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EnrichmentRecord,
    ImportNotApplied,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.profile.health import assess_health  # noqa: E402

NOW = datetime.now(timezone.utc)

# The five sections #705's own screenshot named: education, languages,
# projects, skills, work_experience — 23 items spread unevenly across them,
# matching the founder's UAT report ("23 extracted item(s)").
_SECTIONS = ["work_experience", "education", "skills", "languages", "projects"]


def _profile(records: list[EnrichmentRecord]) -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "metadata": ProfileMetadata(enrichment_history=records).model_dump(
                mode="json"
            ),
        }
    )


def _twenty_three_items() -> list[ImportNotApplied]:
    items = []
    for n in range(23):
        section = _SECTIONS[n % len(_SECTIONS)]
        items.append(
            ImportNotApplied(
                section=section,
                label=f"{section} entry {n}",
                reason="no_op_carried_entry" if n % 2 == 0 else "op_rejected",
            )
        )
    return items


def _record(items, *, source="cv_upload", minutes_ago=0, reconciliation=None) -> EnrichmentRecord:
    return EnrichmentRecord(
        timestamp=NOW - timedelta(minutes=minutes_ago),
        source=source,
        not_applied=items,
        reconciliation=reconciliation,
    )


def _not_applied(health) -> list:
    return [i for i in health.issues if i.thread == "not_applied"]


def _accuracy(health) -> list:
    return [i for i in health.issues if i.thread == "accuracy"]


def test_all_23_items_come_through_none_truncated():
    items = _twenty_three_items()
    health = assess_health(_profile([_record(items)]))
    issue = _not_applied(health)[0]
    assert issue.not_applied_items is not None
    assert len(issue.not_applied_items) == 23
    # Every item is the real ImportNotApplied fact, not a truncated echo.
    assert {i.label for i in issue.not_applied_items} == {i.label for i in items}


def test_the_items_are_sorted_by_section():
    items = _twenty_three_items()
    health = assess_health(_profile([_record(items)]))
    issue = _not_applied(health)[0]
    sections = [i.section for i in issue.not_applied_items]
    assert sections == sorted(sections)


def test_each_item_carries_its_own_section_label_and_reason():
    """The load-bearing property `not_applied_labels`/`_reasons` cannot offer:
    which reason belongs to which item, once there is more than one of each."""
    items = [
        ImportNotApplied(section="education", label="Uni Stuttgart / M.Sc.", reason="no_op_carried_entry"),
        ImportNotApplied(section="skills", label="Rust", reason="op_rejected"),
    ]
    health = assess_health(_profile([_record(items)]))
    issue = _not_applied(health)[0]
    by_label = {i.label: i for i in issue.not_applied_items}
    assert by_label["Uni Stuttgart / M.Sc."].section == "education"
    assert by_label["Uni Stuttgart / M.Sc."].reason == "no_op_carried_entry"
    assert by_label["Rust"].section == "skills"
    assert by_label["Rust"].reason == "op_rejected"


def test_every_other_thread_leaves_not_applied_items_null():
    from applire.schemas.profile import Conflict

    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "metadata": ProfileMetadata(
                pending_conflicts=[
                    Conflict(
                        section="work_experience",
                        field="end_date",
                        existing_value="2019-12",
                        incoming_value="2020-01",
                        source="cv_upload",
                    )
                ]
            ).model_dump(mode="json"),
        }
    )
    for issue in assess_health(profile).issues:
        if issue.thread == "not_applied":
            continue
        assert issue.not_applied_items is None


# ── ruling U-4 (2026-09-15) — the duplicate accuracy thread retires ─────────


def test_a_merge_loss_covered_by_a_not_applied_witness_yields_exactly_one_issue():
    """One fact, two readers is the reported defect: the SAME 23 items showed
    up as both a critical `accuracy` card and a `review` `not_applied` card.
    The `not_applied` thread owns the fact; `accuracy`'s loss branch retires
    for any record carrying a `not_applied` witness."""
    items = _twenty_three_items()
    record = _record(
        items,
        reconciliation={
            "work_experience": {"extracted": 10, "stored": 5, "delta": 5},
            "education": {"extracted": 10, "stored": 5, "delta": 5},
        },
    )
    health = assess_health(_profile([record]))
    assert len(health.issues) == 1
    assert health.issues[0].thread == "not_applied"
    assert health.issues[0].id == f"not_applied:{record.id}"
    # The surviving thread's severity stays `review` (V-6) — never raised.
    assert health.issues[0].profile_mismatch_severity == "review"


def test_a_legacy_record_without_the_witness_still_yields_the_accuracy_loss_issue():
    """A record predating #615 has no `not_applied` witness at all — the
    accuracy loss issue is the only record of the loss, so it must survive."""
    record = _record(
        [],
        reconciliation={"skills": {"extracted": 10, "stored": 7, "delta": 3}},
    )
    health = assess_health(_profile([record]))
    accuracy = _accuracy(health)
    assert len(accuracy) == 1
    assert "did not retain 3 extracted" in accuracy[0].summary
    assert _not_applied(health) == []


def test_low_confidence_without_loss_is_unaffected_by_the_guard():
    """The guard only touches the LOSS branch — a low-confidence-only record
    (no reconciliation loss) keeps emitting its accuracy issue regardless of
    whether it also carries `not_applied` items."""
    record = EnrichmentRecord(
        timestamp=NOW,
        source="cv_upload",
        confidence=0.4,
        not_applied=[
            ImportNotApplied(section="skills", label="Go", reason="op_rejected")
        ],
    )
    health = assess_health(_profile([record]))
    accuracy = _accuracy(health)
    assert len(accuracy) == 1
    assert "Low-confidence merge" in accuracy[0].summary
