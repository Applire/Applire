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

"""Cover-letter date formatting.

The letter date is system-injected, never LLM-generated — the model cannot
know today's date and hallucinates one if asked. Month names are mapped
explicitly so output does not depend on installed system locales.
"""

from datetime import date

_GERMAN_MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]
_ENGLISH_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def format_letter_date(language: str, today: date | None = None) -> str:
    """Format *today* as a DACH letter date: '10. Juni 2026' (de) /
    '10 June 2026' (en)."""
    d = today or date.today()
    if language == "de":
        return f"{d.day}. {_GERMAN_MONTHS[d.month - 1]} {d.year}"
    return f"{d.day} {_ENGLISH_MONTHS[d.month - 1]} {d.year}"
