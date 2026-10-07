# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alternate names ("aliases") — ADR-046 / ADR-063 amended 2026-10-07 (#709 #716).

One module for every FACT the vault needs about the other names an entry is
known by: which field holds which entry's aliases, whether a name IS one of an
entry's names, and the import-path writer that records a name the reconciler
bound to an existing entry.

Doctrine (ADR-062 clause 1). Whether "Roche" and "Roche Diagnostics GmbH" are
one employer is a JUDGEMENT, and it stays the model's: an alias is only ever
born from a binding the model made (`match_existing`, or an upsert with a
`target`), and its TEXT is the incoming document's own wording — never a
translation or normalisation the code produced, and never the model's free-text
`incoming` string (adversarial pass 2026-10-07: splitting that string on " / "
puts a swapped "Diplom / TU München" into the wrong lists). Everything the code
does afterwards is a string comparison against names already in the vault.

Comparisons are exact after the committer's ``_norm`` (NFC, strip, casefold) —
an alias is a name, and a name compares exactly; the near-dupe instruments are
never widened to fuzzy-compare aliases. Every reader applies the EXACTLY-ONE
rule: when two entries of a section carry the same name, no alias reading
fires and the caller falls through to whatever it did before.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Sequence

#: section -> {natural-key field: alias field}. Sections absent here have no
#: alias field (certifications, publications, projects, signature stories):
#: a `match_existing` against them keeps writing only its receipt.
ALIAS_FIELDS: dict[str, dict[str, str]] = {
    "skills": {"name": "aliases"},
    "languages": {"language": "aliases"},
    "education": {"institution": "institution_aliases", "degree": "degree_aliases"},
    "work_experience": {"company": "company_aliases", "role": "role_aliases"},
    "volunteer_activities": {"organization": "organization_aliases"},
}

#: The three sections whose identity also carries a start date (B1 of
#: 2026-08-28: a repeat stint is a distinct CV line).
ENGAGEMENT_SECTIONS: frozenset[str] = frozenset(
    {"work_experience", "projects", "volunteer_activities"}
)


def norm(value: object) -> str:
    """The committer's ``_norm`` (``reconcile/apply.py``), restated here so this
    module has no import cycle with the applier. Pinned equal by a test."""
    return unicodedata.normalize("NFC", str(value) if value is not None else "").strip().casefold()


def month(value: object) -> str | None:
    """``YYYY-MM`` of a stated start date, or ``None`` for a missing or
    year-only date — a shared year is too weak to name one stint."""
    text = norm(value)
    if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit():
        return text[:7]
    return None


def months_agree(a: object, b: object) -> bool:
    """Both sides state a month and it is the same (strict — no wildcard)."""
    ma, mb = month(a), month(b)
    return ma is not None and ma == mb


def months_contradict(a: object, b: object) -> bool:
    """Both sides state a month and they differ."""
    ma, mb = month(a), month(b)
    return ma is not None and mb is not None and ma != mb


def names_of(entry: Any, field: str, section: str) -> set[str]:
    """The normalised names ``entry`` carries for ``field``: its own value plus
    every recorded alias of that field."""
    names = {norm(getattr(entry, field, "") or "")}
    alias_field = ALIAS_FIELDS.get(section, {}).get(field)
    if alias_field:
        names |= {norm(a) for a in (getattr(entry, alias_field, None) or []) if isinstance(a, str)}
    names.discard("")
    return names


def alias_hit(entry: Any, field: str, section: str, value: object) -> bool:
    """``value`` equals one of ``entry``'s RECORDED aliases for ``field`` (not its
    own value — that is the caller's ordinary key comparison)."""
    wanted = norm(value)
    alias_field = ALIAS_FIELDS.get(section, {}).get(field)
    if not wanted or not alias_field:
        return False
    return wanted in {norm(a) for a in (getattr(entry, alias_field, None) or []) if isinstance(a, str)}


def unique_entry_by_names(
    entries: Sequence[Any], section: str, values: dict[str, object], *, require_alias: bool = True
) -> Any | None:
    """The ONE entry of ``section`` whose names cover every non-empty value in
    ``values`` (field -> incoming value), with at least one field matched
    through a recorded alias when ``require_alias``. Two or more such entries
    -> ``None`` (the exactly-one rule)."""
    wanted = {f: norm(v) for f, v in values.items() if norm(v)}
    if not wanted:
        return None
    hits = []
    for entry in entries:
        if not all(w in names_of(entry, f, section) for f, w in wanted.items()):
            continue
        if require_alias and not any(alias_hit(entry, f, section, w) for f, w in wanted.items()):
            continue
        hits.append(entry)
    return hits[0] if len(hits) == 1 else None


def add_alias(entry: Any, field: str, section: str, value: str) -> bool:
    """Append ``value`` to ``entry``'s alias list for ``field`` when it is a NEW
    name for it: non-empty, not its own value, not already recorded, and not the
    value of the entry's OTHER natural-key field (positional sanity — a role
    never lands in the company list). Returns whether it was written."""
    alias_field = ALIAS_FIELDS.get(section, {}).get(field)
    if not alias_field or not isinstance(value, str) or not value.strip():
        return False
    if norm(value) in names_of(entry, field, section):
        return False
    for other in ALIAS_FIELDS.get(section, {}):
        if other != field and norm(value) in names_of(entry, other, section):
            return False
    current = list(getattr(entry, alias_field, None) or [])
    current.append(value.strip())
    setattr(entry, alias_field, current)
    return True


def engagement_alias_entry(
    entries: Sequence[Any], section: str, org_field: str, org: object, start: object
) -> Any | None:
    """The ONE engagement entry whose RECORDED organisation alias equals ``org``
    and whose stated start month equals ``start``'s (both stated). Used by the
    applier's no-target guard: an alias may turn an incoming entry into a MATCH
    only on that strict evidence — anything weaker falls through to the
    existing guard, so an AMBIGUOUS verdict still asks (an alias never silences
    a question; adversarial finding 4, 2026-10-07)."""
    hits = [
        e for e in entries
        if alias_hit(e, org_field, section, org) and months_agree(start, getattr(e, "start_date", None))
    ]
    return hits[0] if len(hits) == 1 else None
