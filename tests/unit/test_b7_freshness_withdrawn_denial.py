# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial pass, Hazelnut build 7 — L-2 + F-2 (2): a withdrawn denial.

Under L-2 the letter names ONLY the candidate's recorded denials ("Mit X habe ich
noch nicht gearbeitet"). The 0086/0088 heal asks "that answer only said your
profile does not mention X yet — take it back?"; "Ja, zurücknehmen" applies
`WithdrawDenial`, whose receipt is `metadata.denied_concepts` action `removed`.
`profile_updated_since` counts only a NEWLY ADDED denial among metadata changes,
so the letter written before the withdrawal keeps stating a "no" the person has
just said they never gave — and the letter view shows no "Neu erstellen" cue.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from applire.services.profile_freshness import profile_updated_since

CREATED = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


def test_a_denial_withdrawn_after_the_letter_marks_the_letter_behind():
    profile = {
        "metadata": {
            "denied_concepts": [],
            "withdrawn_denials": [{"concept": "Apache Kafka"}],
            "enrichment_history": [
                {
                    "timestamp": (CREATED + timedelta(hours=1)).isoformat(),
                    "source": "manual_edit",
                    "changes": [
                        {"section": "metadata", "field": "denied_concepts", "action": "removed",
                         "old_value": "Apache Kafka"}
                    ],
                }
            ],
        }
    }
    # the letter (sections=None — any content) names "Apache Kafka" as the
    # candidate's recorded no; that no is gone now.
    assert profile_updated_since(CREATED, profile) is True


def test_a_withdrawal_before_the_letter_does_not_mark_it_and_an_escalation_never_does():
    def trail(hours, action):
        return {"metadata": {"enrichment_history": [{
            "timestamp": (CREATED + timedelta(hours=hours)).isoformat(),
            "source": "manual_edit",
            "changes": [{"section": "metadata", "field": "denied_concepts", "action": action}],
        }]}}

    assert profile_updated_since(CREATED, trail(-1, "removed")) is False
    assert profile_updated_since(CREATED, trail(1, "updated")) is False
