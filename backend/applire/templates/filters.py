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

"""Shared render-time Jinja filters for the CV and cover-letter templates (#307).

Registered through :func:`register_filters` rather than exported as a bare dict,
because there are FOUR independent Jinja environments in the tree (``cv.py``,
``cover_letter.py``, ``thumbnails.py``, and the ``tests/ats`` harness; the section
editor reuses ``cv.py``'s). A filter registered on some of them and not the others
is the exact shape of the render-site drift ``CLAUDE.md`` warns about, so every
site calls the same registrar.
"""
import re

__all__ = ["month_year", "register_filters"]

# "2017-04", "2017-04-01", "2017/04" — the shapes the extractor and the reconciler
# actually produce. Anything else is passed through untouched (see month_year).
_ISO_MONTH_RE = re.compile(r"^\s*(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?\s*$")
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")


def month_year(value: object) -> str:
    """Render a stored partial date in MM/YYYY, the DACH CV convention (#307).

    Charter run #7 shipped ``2017-04 – heute`` in a German CV's date column; the
    blind HR screener flagged it unprompted as "unüblich in einer DACH-Bewerbung".
    The stored value is unchanged — this is presentation only.

    MM/YYYY is used for **every** output language, not only German. It is the DACH
    standard, it is unremarkable on an English CV, and it needs no localised month
    names — which would otherwise add a per-language table (and a new way for a
    language to be half-supported) to fix a formatting bug.

    Never raises and never drops information: a value that is not a recognised
    partial date is returned as-is, because the alternative is a blank date column
    on a real candidate's CV.

    >>> month_year("2017-04")
    '04/2017'
    >>> month_year("2022-01-01")
    '01/2022'
    >>> month_year("2016")
    '2016'
    >>> month_year(None)
    ''
    """
    if value is None:
        return ""
    text = str(value)

    match = _ISO_MONTH_RE.match(text)
    if match:
        year, month = match.group(1), int(match.group(2))
        # A 13th month means the value is not what we think it is — pass it through
        # rather than rendering "13/2017" with false confidence.
        if 1 <= month <= 12:
            return f"{month:02d}/{year}"
        return text.strip()

    if _YEAR_ONLY_RE.match(text):
        # A year alone is already conventional on both a CV and a certificate.
        return text.strip()

    return text.strip()


def register_filters(env) -> None:
    """Install every shared template filter on a Jinja environment.

    Call this at EVERY render site. See the module docstring for why.
    """
    env.filters["month_year"] = month_year
