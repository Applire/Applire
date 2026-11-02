# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation of the AddRoleRequest schema."""
import pytest
from pydantic import ValidationError

from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry


def test_valid_minimal_request():
    req = AddRoleRequest(
        title="Director of QA",
        company="Varena",
        start_date="2026-06-01",
        close_roles=[],
        source="manual",
    )
    assert req.title == "Director of QA"
    assert req.close_roles == []
    assert req.source_ref is None


def test_close_roles_entries_have_role_id_and_end_date():
    req = AddRoleRequest(
        title="Director",
        company="Varena",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id="abc", end_date="2026-05-31")],
        source="manual",
    )
    assert req.close_roles[0].role_id == "abc"
    assert req.close_roles[0].end_date == "2026-05-31"


def test_source_rejects_unknown_value():
    with pytest.raises(ValidationError):
        AddRoleRequest(
            title="X",
            company="Y",
            start_date="2026-06-01",
            close_roles=[],
            source="meow",   # invalid
        )
