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

"""#328 (PO decision 2026-08-07, option 4) / #382 — quantified role facts as
DERIVED PROJECTIONS of the candidate's own bullet text.

Option 4, decided rather than proposed: *the candidate's own wording keeps the
number, and the typed field stays a queryable projection.* Part A (shipped) is
the prompt-side half — the extractor and the reconciler are instructed to keep
a stated figure IN the responsibility/achievement bullet that states it. This
module is the vault-side half: at every write, each work entry's
``team_size`` / ``budget_managed`` / ``industry_context`` is **reconciled
against that entry's own bullets**, and the result recorded as a
:class:`~applire.schemas.profile.RoleFactProjection`.

Two things follow, and they are #328 and #382 respectively:

1. **The typed field can never quietly become the only home of the figure.**
   A value no bullet of the entry states is marked ``uncorroborated``. It is
   NOT deleted — an interview answer is real testimony whether or not a bullet
   repeats it, and deleting it would break profile completeness, the ADR-069
   scope floor and the furniture line at once. But the marking exists, so the
   asymmetry #328 measured (a figure reachable by the letter and by nothing
   else) is now a queryable property of the vault rather than an inference.

2. **The unit survives (#382).** ``_apply_set_field``'s type coercion turns an
   interview answer of ``6000000`` into the string ``"6000000"``, and the
   extraction schema ("Budget amount as string") invites the same shape from
   the model. Six million what? When the entry's own bullet states the SAME
   figure WITH a currency ("Budgetverantwortung von ca. 6 Mio. € pro Jahr"),
   the projection adopts the bullet's wording, because the bullet is where the
   figure lives. Nothing is ever invented: no corroborating bullet, no unit.

ADR-062 clause 6 classification — **everything in this module is a FACT.**
"Is this figure present in this text" is clause 1's own example of a fact, and
it is settled here by the ONE shared extractor
``services.oracle.matchers.figures.extract_figures`` (#215's longest-first
magnitude fix, #214/#220/#374's exclusions). No second figure parser is written
(ADR-066). The one judgement in the neighbourhood — *does this bullet's figure
BEAR on this field's semantics* — is deliberately NOT made: corroboration here
means "this entry's prose states this number", nothing more, and the projection
says only that.

ADR-070 boundary. A projection's ``quote`` looks exactly like an attestation's
({entry, quote, unit} is the ADR-070 shape) and is not one: an attestation is
MODEL-cited and fail-closed verified by ``scope_requirements.verify_attested_
evidence``, and only it may lift a scope row's status. Code-derived evidence may
never enter ``bar.attested`` — pinned from both sides by
``tests/unit/test_role_facts_projection.py``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from applire.schemas.profile import RoleFactProjection, WorkEntry
from applire.services.oracle.matchers.figures import Figure, extract_figures

# The entry's own prose. Same two nodes ADR-070 clause 1 allows an attested
# quote to resolve against (``scope_requirements._ATTESTED_PROSE_FIELDS``) —
# one real bullet, never a concatenation.
_PROSE_FIELDS = ("responsibilities", "achievements")

# The currency tokens ``figures._CURRENCY_RE`` itself recognises. Longest-first
# so "EUR" is never read as a bare "E"-less match — the #215 lesson, applied to
# a much smaller table.
_CURRENCY_TOKEN_RE = re.compile(r"EUR|USD|CHF|GBP|[€$£]", re.IGNORECASE)

# ``Figure.value`` is canonical. Since #215 the magnitude is folded in as a
# NUMERIC factor ("6 Mio. €" → "6000000"), so the ``[kmb]`` suffix branch below
# is a tolerated legacy form (the extractor's fail-closed path for a degenerate
# digit run) rather than the normal case — this stays a table lookup either
# way, never a reading.
_MAGNITUDE_FACTORS = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}
_CANONICAL_RE = re.compile(r"^(\d+(?:\.\d+)?)([kmb]?)$")


def _magnitude(canonical: str) -> float | None:
    """The numeric value of a canonical ``Figure.value``, magnitude expanded."""
    m = _CANONICAL_RE.match(canonical.strip())
    if m is None:
        return None
    return float(m.group(1)) * _MAGNITUDE_FACTORS[m.group(2)]


def _norm(text: str) -> str:
    """NFKC + whitespace collapse + casefold — the presence fold for
    ``industry_context``, which is a word rather than a figure."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().lower()


def _prose(entry: WorkEntry) -> list[str]:
    nodes: list[str] = []
    for field in _PROSE_FIELDS:
        for node in getattr(entry, field, None) or []:
            if isinstance(node, str) and node.strip():
                nodes.append(node)
    return nodes


def _corroborating_figure(
    prose: list[str], target: float, *, prefer_currency: bool
) -> tuple[Figure, str] | None:
    """The entry's own bullet stating ``target``, with the figure that states it.

    ``prefer_currency`` makes the unit-bearing reading win when the same number
    appears both ways — that preference is the whole of #382's fix and must not
    be reordered away.
    """
    fallback: tuple[Figure, str] | None = None
    for node in prose:
        for figure in extract_figures(node):
            if figure.kind not in ("currency", "number"):
                continue
            value = _magnitude(figure.value)
            if value is None or value != target:
                continue
            if figure.kind == "currency" and prefer_currency:
                return figure, node
            if fallback is None:
                fallback = (figure, node)
    return fallback


def _unit_of(raw: str) -> str | None:
    """The currency token the bullet itself wrote, verbatim. Never invented."""
    m = _CURRENCY_TOKEN_RE.search(raw)
    return m.group(0) if m else None


def _project_budget(entry: WorkEntry, prose: list[str]) -> RoleFactProjection | None:
    stored = (entry.budget_managed or "").strip()
    if not stored:
        return None
    stored_figures = extract_figures(stored)
    stored_value = next(
        (_magnitude(f.value) for f in stored_figures if f.kind in ("currency", "number")),
        None,
    )
    stored_has_unit = any(f.kind == "currency" for f in stored_figures)
    if stored_value is None:
        # A budget stated in words ("mid six figures") is not a figure this
        # extractor reads. Keep the testimony; claim nothing about it.
        return RoleFactProjection(value=stored, provenance="uncorroborated")

    match = _corroborating_figure(prose, stored_value, prefer_currency=True)
    if match is None:
        return RoleFactProjection(value=stored, provenance="uncorroborated")

    figure, node = match
    # #382: the typed field adopts the bullet's wording ONLY when it has no unit
    # of its own to lose. A stored "ca. 6 Mio. EUR" already says what it means,
    # and the candidate's qualifier is worth more than uniformity.
    if not stored_has_unit and figure.kind == "currency":
        value = figure.raw
    else:
        value = stored
    entry.budget_managed = value
    return RoleFactProjection(
        value=value,
        unit=_unit_of(value) or _unit_of(figure.raw),
        quote=node,
        provenance="derived",
    )


def _project_team_size(entry: WorkEntry, prose: list[str]) -> RoleFactProjection | None:
    size = entry.team_size
    if not isinstance(size, int) or isinstance(size, bool):
        return None
    match = _corroborating_figure(prose, float(size), prefer_currency=False)
    if match is None:
        return RoleFactProjection(value=str(size), provenance="uncorroborated")
    return RoleFactProjection(value=str(size), quote=match[1], provenance="derived")


def _project_industry(entry: WorkEntry, prose: list[str]) -> RoleFactProjection | None:
    industry = (entry.industry_context or "").strip()
    if not industry:
        return None
    needle = _norm(industry)
    # An industry is not a figure, so ``extract_figures`` has nothing to say
    # about it. Presence of the entry's own words in the entry's own text is
    # still a fact — and the employer's NAME is prose the candidate wrote, the
    # very place "Kunststofftechnik" comes from on the run-#9 CV.
    haystack = [entry.company or "", entry.role or "", *(entry.role_aliases or []), *prose]
    for node in haystack:
        if needle and needle in _norm(node):
            return RoleFactProjection(value=industry, quote=node, provenance="derived")
    return RoleFactProjection(value=industry, provenance="uncorroborated")


def project_role_facts(entry: WorkEntry) -> None:
    """Recompute ``entry.role_fact_projections`` from the entry's own text.

    Idempotent and total: the map is REBUILT, never merged into, so a stale
    projection whose bullet has since been edited away cannot survive (that is
    what makes this a projection rather than a duplicate). The typed values
    themselves are only ever re-*represented* (#382's unit), never invented and
    never deleted.
    """
    prose = _prose(entry)
    projections: dict[str, RoleFactProjection] = {}
    for field, project in (
        ("budget_managed", _project_budget),
        ("team_size", _project_team_size),
        ("industry_context", _project_industry),
    ):
        projection = project(entry, prose)
        if projection is not None:
            projections[field] = projection
    entry.role_fact_projections = projections


def project_profile_role_facts(profile: Any) -> None:
    """Apply :func:`project_role_facts` to every work entry of ``profile``."""
    for entry in getattr(profile, "work_experience", None) or []:
        if isinstance(entry, WorkEntry):
            project_role_facts(entry)
