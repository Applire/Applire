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

"""#382 — does a stored ``budget_managed`` wording carry a unit?

ONE implementation of that question (ADR-066), because four layers now ask it:
the template filter that renders the furniture line, the deterministic role-facts
copy in CV generation, profile completeness, and the Profile Health hub. It is a
FACT in ADR-062 clause 1's sense — "is this token present in this text" — so it
is settled by a vocabulary, never by a judgement and never by an LLM.

The vocabulary is deliberately the SAME currency table
``services.profile.role_facts`` reconciles a projection's unit with, which is in
turn the table ``oracle.matchers.figures`` recognises. A unit this module cannot
see is a unit the projection could not have recorded either, so the two never
disagree about the same value.

Lives in ``utils`` rather than in ``services`` because ``templates.filters`` is a
consumer: a Jinja filter must not import the service layer.

**Nothing here ever infers a unit.** ``"6000000"`` is six million of *something*;
the only honest readings are "ask" and "say nothing", which is precisely the
PO decision of 2026-08-08 (Option A) this module exists to serve.
"""

from __future__ import annotations

import re

__all__ = ["CURRENCY_TOKEN_RE", "budget_unit", "budget_needs_unit"]

# Longest-first so "EUR" is never read as a bare "E"-less match — the #215
# lesson, applied to a much smaller table.
CURRENCY_TOKEN_RE = re.compile(r"EUR|USD|CHF|GBP|[€$£]", re.IGNORECASE)


def budget_unit(value: object) -> str | None:
    """The currency token the wording itself writes, verbatim, or ``None``.

    >>> budget_unit("ca. 6 Mio. EUR")
    'EUR'
    >>> budget_unit("€200k")
    '€'
    >>> budget_unit("6000000") is None
    True
    """
    if value is None:
        return None
    match = CURRENCY_TOKEN_RE.search(str(value))
    return match.group(0) if match else None


def budget_needs_unit(value: object) -> bool:
    """True when a budget value EXISTS but states no unit (#382).

    An absent budget is deliberately ``False``: "no budget recorded" is a
    completeness gap and "six million of what?" is a unit gap, and presenting
    them to the user as the same question would answer neither.

    >>> budget_needs_unit("6000000")
    True
    >>> budget_needs_unit("ca. 6 Mio. EUR")
    False
    >>> budget_needs_unit(None)
    False
    """
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return budget_unit(text) is None
