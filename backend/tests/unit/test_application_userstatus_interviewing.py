# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UserStatus enum accepts the new 'interviewing' value (E039/US218)."""
from applire.models.application import UserStatus


def test_userstatus_has_interviewing():
    assert UserStatus.interviewing.value == "interviewing"


def test_userstatus_member_set():
    assert {m.value for m in UserStatus} == {
        "tracking", "applied", "interviewing", "offer", "rejected", "hired"
    }
