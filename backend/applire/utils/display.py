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

"""Human-readable formatting for reconciliation values shown to users.

Reconciliation Conflict / FieldChange / ConfirmationPrompt values are typed
``Any`` — they may carry lists (e.g. a confirmation's option list) or dicts
(e.g. a structured entity). A bare ``str([...])`` / ``str({...})`` leaks a raw
Python repr (``['Yes', 'No']``) into a sentence the user reads. Every site that
turns such a value into a *display string* must route through
``format_display_value`` so lists become clean comma-joined text and dicts a
sensible ``key: value`` summary — never internal repr.
"""
from __future__ import annotations

from typing import Any


def format_display_value(value: Any) -> str:
    """Render a reconciliation value as clean human-readable text.

    - ``None`` → ``""`` (absence — nothing to show)
    - ``str`` → itself
    - ``list``/``tuple``/``set`` → comma-joined items (each recursively formatted)
    - ``dict`` → ``"key: value, key: value"`` summary
    - everything else → ``str(value)``
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(
            f"{key}: {format_display_value(val)}" for key, val in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return ", ".join(format_display_value(item) for item in value)
    return str(value)
