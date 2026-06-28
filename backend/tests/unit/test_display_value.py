# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Bug 2 regression — reconciliation values render as clean human-readable text."""
from __future__ import annotations

from applire.utils.display import format_display_value


def test_none_renders_empty():
    assert format_display_value(None) == ""


def test_string_passthrough():
    assert format_display_value("Acme Corp") == "Acme Corp"


def test_list_renders_comma_joined_not_repr():
    out = format_display_value(["Yes, same role", "No, separate roles"])
    assert out == "Yes, same role, No, separate roles"
    # never a raw Python repr leaked into a sentence the user reads
    assert "[" not in out and "'" not in out


def test_dict_renders_key_value_summary_not_repr():
    out = format_display_value({"company": "Acme", "role": "Dev"})
    assert out == "company: Acme, role: Dev"
    assert "{" not in out and "'" not in out


def test_scalar_falls_back_to_str():
    assert format_display_value(6) == "6"
