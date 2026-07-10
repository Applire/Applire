# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UserStatus enum accepts the new 'hired' value."""
from applire.models.application import UserStatus


def test_userstatus_has_hired():
    assert UserStatus.hired.value == "hired"


def test_userstatus_member_set():
    # 'interviewing' added by E039/US218 (migration 0044).
    assert {m.value for m in UserStatus} == {
        "tracking", "applied", "interviewing", "rejected", "offer", "hired"
    }
