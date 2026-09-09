# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder ruling V-6 (2026-09-09) — the `not_applied` receipt gets a reader.

`commit_ops` has copied `ApplyResult.not_applied` onto every `EnrichmentRecord`
since #615 (2026-08-28) and **nothing read it**. The two import doors derive
`merge_status` from `MergeResult.not_applied` instead; the testimony door's wire
field is a different computation entirely; the frontend carried no such field.
So a durable record of "this did not land" existed and no surface showed it —
and ADR-061's 2026-09-08 amendment (#684) then routed the summary drop onto the
same channel, where it would have inherited the same silence.

These pin the thread, its wording, its scope, and — the one that is easy to get
wrong — that it is **not** a decision the candidate owes.
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


def _profile(records: list[EnrichmentRecord]) -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "metadata": ProfileMetadata(enrichment_history=records).model_dump(
                mode="json"
            ),
        }
    )


def _record(items, *, source="interview", minutes_ago=0) -> EnrichmentRecord:
    return EnrichmentRecord(
        timestamp=NOW - timedelta(minutes=minutes_ago),
        source=source,
        not_applied=items,
    )


def _not_applied(health) -> list:
    return [i for i in health.issues if i.thread == "not_applied"]


def test_a_statement_intake_drop_reaches_the_health_hub():
    """#684's own receipt, surfaced. Before V-6 this produced no issue at all."""
    health = assess_health(
        _profile(
            [
                _record(
                    [
                        ImportNotApplied(
                            section="professional_summary",
                            label="en",
                            reason="summary_populated",
                        )
                    ]
                )
            ]
        )
    )
    issues = _not_applied(health)
    assert len(issues) == 1
    assert issues[0].profile_mismatch_severity == "review"
    assert issues[0].field_ref == "professional_summary"


def test_the_summary_slot_is_named_as_the_dispute_surface_names_it():
    """A hub saying "en" while the dispute says "your self-description (English)"
    is two names for one thing — the drift class #685 was filed against."""
    health = assess_health(
        _profile(
            [
                _record(
                    [
                        ImportNotApplied(
                            section="professional_summary",
                            label="en",
                            reason="summary_populated",
                        )
                    ]
                )
            ]
        )
    )
    summary = _not_applied(health)[0].summary
    assert "your self-description (English)" in summary
    assert "(en)" not in summary


def test_the_issue_says_WHY_not_only_that_something_is_missing():
    """The "and why" half of the ruling. A count with no reason is a worry."""
    health = assess_health(
        _profile(
            [
                _record(
                    [
                        ImportNotApplied(
                            section="education",
                            label="Universität Stuttgart / M.Sc.",
                            reason="no_op_carried_entry",
                        ),
                        ImportNotApplied(
                            section=None, label="upsert_work", reason="op_rejected"
                        ),
                    ],
                    source="cv_upload",
                )
            ]
        )
    )
    summary = _not_applied(health)[0].summary
    assert "no change carried it" in summary
    assert "the change was malformed and dropped" in summary
    # …and it does not leak the raw reason enum at the candidate.
    assert "no_op_carried_entry" not in summary
    assert "op_rejected" not in summary


def test_the_import_door_is_covered_too_which_is_the_gap_open_since_615():
    """Both doors, both channels. The import path has written this receipt since
    2026-08-28 with no reader; this is the same thread reading it."""
    health = assess_health(
        _profile(
            [
                _record(
                    [
                        ImportNotApplied(
                            section="languages", label="Französisch", reason="no_op_carried_entry"
                        )
                    ],
                    source="linkedin_import",
                )
            ]
        )
    )
    issues = _not_applied(health)
    assert len(issues) == 1
    assert "linkedin_import" in issues[0].summary


def test_one_issue_per_record_not_one_per_item():
    """The candidate submitted ONE thing; three receipts about it are three
    worries about one event."""
    items = [
        ImportNotApplied(section="skills", label=f"Skill {n}", reason="no_op_carried_entry")
        for n in range(5)
    ]
    health = assess_health(_profile([_record(items, source="cv_upload")]))
    issues = _not_applied(health)
    assert len(issues) == 1
    assert issues[0].summary.startswith("5 items")
    assert "and 2 more" in issues[0].summary   # three named, the rest counted


def test_a_clean_record_produces_no_issue():
    health = assess_health(_profile([_record([])]))
    assert _not_applied(health) == []


def test_only_the_trail_head_is_read():
    """Founder ruling V-6 says "off the trail head", and the reason is real: an
    item that did not land two merges ago may since have been supplied by a
    later one, and a hub reporting it forever would nag about something already
    fixed — the mirror of SF-PROFILE.2's "conflicts age forever".

    **The residual this pins is deliberate and is stated in the report:** a loss
    older than the newest record is NOT surfaced. If that is not what the ruling
    meant, this test is the one line to change.
    """
    old = _record(
        [ImportNotApplied(section="education", label="Old loss", reason="no_op_carried_entry")],
        minutes_ago=30,
    )
    head = _record([], source="cv_upload")
    issues = _not_applied(assess_health(_profile([old, head])))
    assert issues == []

    # …and with the head carrying items, exactly the head's are reported.
    head_with = _record(
        [ImportNotApplied(section="skills", label="New loss", reason="op_rejected")],
        source="cv_upload",
    )
    issues = _not_applied(assess_health(_profile([old, head_with])))
    assert len(issues) == 1
    assert "New loss" in issues[0].summary
    assert "Old loss" not in issues[0].summary


def test_it_is_never_counted_as_a_decision_the_candidate_owes():
    """The load-bearing negative. The gaps page's popup stack counts
    `conflict`/`confirmation` — a decision to make. There is nothing to pick
    between here, and counting it would tell the candidate they owe an answer
    they cannot give."""
    health = assess_health(
        _profile(
            [
                _record(
                    [
                        ImportNotApplied(
                            section="professional_summary",
                            label="en",
                            reason="summary_populated",
                        )
                    ]
                )
            ]
        )
    )
    assert {i.thread for i in health.issues} == {"not_applied"}
    assert not [i for i in health.issues if i.thread in {"conflict", "confirmation"}]


# ── founder ruling V-7 — the reader composes; the server sends pieces ────────


def test_the_issue_carries_STRUCTURED_pieces_not_only_an_english_sentence():
    """V-7. The DE screenshot showed the V-6 sentence rendered as raw English
    directly beneath a fully-German conflict card. `applire-i18n` makes an
    untranslated user-facing sentence a defect, so the backend stops sending a
    sentence as the contract and sends the parts the reader composes from:
    a count, the `EnrichmentRecord.source` KEY (localised through the same
    `profile.sources.*` dictionary the conflict rows use), the raw reason KEYS,
    and the item labels.

    `summary` survives as the fallback for a consumer that has not been updated
    — the same disposition #626 gave the conflict thread.
    """
    health = assess_health(
        _profile(
            [
                _record(
                    [
                        ImportNotApplied(
                            section="professional_summary",
                            label="en",
                            reason="summary_populated",
                        )
                    ]
                )
            ]
        )
    )
    issue = _not_applied(health)[0]
    assert issue.not_applied_count == 1
    assert issue.not_applied_source == "interview"        # a KEY, not "your interview answer"
    assert issue.not_applied_reasons == ["summary_populated"]   # KEYS, not sentences
    assert issue.not_applied_labels == ["en"]             # the raw slot; the reader names it
    assert issue.summary                                   # …and the fallback still exists


def test_every_other_thread_leaves_the_structured_fields_null():
    """One field, one master. A `conflict` issue carrying a `not_applied_count`
    would be the two-masters mistake ADR-061's own amendment names."""
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
        assert issue.not_applied_count is None
        assert issue.not_applied_source is None
        assert issue.not_applied_reasons is None
        assert issue.not_applied_labels is None


def test_the_reason_keys_all_have_a_catalogue_entry_in_BOTH_languages():
    """The parity a catalogue check cannot make: `notAppliedReason.*` must cover
    every value `ImportNotApplied.reason` can hold, or a real drop renders as a
    raw enum. Enumerated from the Literal itself, so a fourth reason added
    without its copy reddens here rather than on a user's screen.
    """
    import json
    import typing
    from pathlib import Path

    from applire.schemas.profile import ImportNotApplied

    reasons = set(
        typing.get_args(ImportNotApplied.model_fields["reason"].annotation)
    )
    assert reasons, "reason is expected to be a Literal"

    frontend = Path(__file__).resolve().parents[3] / "frontend" / "messages"
    for locale in ("en", "de"):
        catalog = json.loads((frontend / f"{locale}.json").read_text(encoding="utf-8"))
        entries = catalog["health"]["notAppliedReason"]
        missing = reasons - set(entries)
        assert not missing, f"{locale}.json is missing notAppliedReason: {sorted(missing)}"
        # …and the German values are really German, which parity cannot tell.
        if locale == "de":
            assert entries != catalog and all(
                v != json.loads((frontend / "en.json").read_text(encoding="utf-8"))[
                    "health"
                ]["notAppliedReason"][k]
                for k, v in entries.items()
            ), "a de.json value was left as its English counterpart"
