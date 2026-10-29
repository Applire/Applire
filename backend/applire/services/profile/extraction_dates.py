# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-24 (Hazelnut build 5, delivery run 5) — the extraction corrector may not
take a true date away on the reviewer's say-so.

Twice in this build the extraction was right and the extraction REVIEWER called
a date "fabricated": D-14 (an end date, run 3) and D-24 (both dates of a 2008
role, run 5: "the source gives no dates for this role", although the source
line read "August 2008 - Mai 2011"). Both times the corrector obeyed and a true
fact was deleted. The prompt fix of D-14b (rule B: read the entry's own date
line) cut the first shape; the second came back in other words. An automated
remedy amplifies a control's false positives, so the remedy itself is now
bounded by a FACT (ADR-062 clause 1), the same way for every reviewer wording:

    A date the first extraction gave a work entry is restored when the
    correction removed or changed it AND the date's year occurs in the source
    text near that entry (its own span: the line before a line that names the entry's
    employer or role, that line, and the lines after it up to the next line
    that names another entry).

A date whose year the source does not state near the entry is not restored;
the reviewer's removal of a truly fabricated date stands. Months are not
checked: a year is what a fabrication invents, and a month name may be written
in any language.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_DATE_FIELDS = ("start_date", "end_date")
_YEAR = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")
#: An entry's span: the line before the first line naming it (a CV may put
#: the dates above the title), that line, and the lines after it up to the
#: next line that names ANOTHER entry (LinkedIn: employer, role, dates).
_MAX_LINES_AFTER = 6


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _year(value: Any) -> str | None:
    m = _YEAR.search(str(value or ""))
    return m.group(1) if m else None


def _names(entry: dict) -> set[str]:
    return {n for n in (_norm(entry.get("company")), _norm(entry.get("role"))) if len(n) >= 3}


def _spans(lines: list[str], entry: dict, others: set[str]) -> list[str]:
    """The source lines that belong to ``entry``: around every line naming it,
    never across a line that names another entry."""
    own = _names(entry)
    foreign = others - own
    low = [_norm(ln) for ln in lines]

    def names_other(i: int) -> bool:
        return any(n in low[i] for n in foreign)

    out: list[str] = []
    for i, ln in enumerate(low):
        if not any(n in ln for n in own):
            continue
        block = [lines[i - 1]] if i > 0 and not names_other(i - 1) else []
        block.append(lines[i])
        for j in range(i + 1, min(len(lines), i + 1 + _MAX_LINES_AFTER)):
            if names_other(j):
                break
            block.append(lines[j])
        out.append(" ".join(block))
    return out


def _match(draft_entry: dict, refined: list[dict]) -> dict | None:
    key = (_norm(draft_entry.get("company")), _norm(draft_entry.get("role")))
    same = [e for e in refined if (_norm(e.get("company")), _norm(e.get("role"))) == key]
    if len(same) == 1:
        return same[0]
    by_company = [e for e in refined if _norm(e.get("company")) == key[0]]
    return by_company[0] if len(by_company) == 1 and not same else None


def keep_source_dates(draft: dict, refined: dict, source: str) -> list[str]:
    """Restore, IN PLACE on ``refined``, every work-entry date the review loop
    removed or changed although its year stands in the source near the entry.
    Returns one note per restored field (for the log and the tests)."""
    if not isinstance(draft, dict) or not isinstance(refined, dict) or draft is refined:
        return []
    before = [e for e in draft.get("work_experience") or [] if isinstance(e, dict)]
    after = [e for e in refined.get("work_experience") or [] if isinstance(e, dict)]
    lines = [ln for ln in (source or "").splitlines() if ln.strip()]
    every_name = set().union(*(_names(e) for e in before)) if before else set()
    notes: list[str] = []
    for d in before:
        r = _match(d, after)
        if r is None:
            continue
        spans = None
        for name in _DATE_FIELDS:
            old, new = d.get(name), r.get(name)
            year = _year(old)
            if not year or _norm(old) == _norm(new):
                continue
            if spans is None:
                spans = _spans(lines, d, every_name)
            if any(year in span for span in spans):
                r[name] = old
                notes.append(f"{d.get('company')!s} / {d.get('role')!s}: {name} {new!r} -> {old!r}")
    if notes:
        logger.warning(
            "extraction review: %d date(s) restored — the year stands in the source near the entry (D-24): %s",
            len(notes), "; ".join(notes),
        )
    return notes
