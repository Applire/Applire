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

"""Gap hints = Keyword Ledger × live document coverage (ADR-019/ADR-048, #117).

Every hint is derived at read time from two orthogonal axes:

- **evidence** (ledger ``status``/``claimable`` — owned by gap analysis; only
  profile enrichment changes it), which decides the hint *kind* and therefore
  the CTA the UI offers;
- **coverage** (surface-form presence in the current document text — the same
  normalised-substring predicate the ATS audit uses), which decides whether a
  hint shows at all.

Covered entries never hint: a claimable keyword already in the document is
done, and an honest-gap keyword in the document is the ATS panel's
truthfulness warning, not a section hint. Coverage is NEVER persisted into
the gap analysis. Pre-ledger analyses (``keyword_ledger`` NULL/empty) fall
back to the category_b/c labels, read-only, with the same coverage filter.
"""
from dataclasses import dataclass
from typing import Any

from applire.schemas.cv_sections import GapHintItem
from applire.services.ats_audit import _find, _norm
from applire.services.cv_gap_mapper import map_gaps_to_sections


@dataclass(frozen=True)
class _Candidate:
    label: str
    kind: str                 # "claimable" | "honest"
    surface_forms: tuple[str, ...]


def _candidates(
    ledger: list[dict[str, Any]] | None,
    category_b: list[str],
    category_c: list[str],
) -> list[_Candidate]:
    """Hint candidates on the evidence axis (coverage not yet applied)."""
    out: list[_Candidate] = []
    seen: set[str] = set()

    if ledger:
        for entry in ledger:
            concept = (entry.get("concept") or "").strip()
            if not concept or _norm(concept) in seen:
                continue
            # keyword-only entries (fit_weight 0) never hint in sections —
            # parity with category_b/c; US204 routes them via the ATS panel.
            if not (entry.get("fit_weight") or 0.0):
                continue
            claimable = bool(entry.get("claimable"))
            status = entry.get("status")
            if not claimable and status != "gap":
                continue  # defensive: unknown status without claim support
            forms = tuple(f for f in (entry.get("surface_forms") or []) if f) or (concept,)
            seen.add(_norm(concept))
            out.append(_Candidate(
                label=concept,
                kind="claimable" if claimable else "honest",
                surface_forms=forms,
            ))
        return out

    # Legacy fallback: pre-ledger analyses only carry flat labels.
    for label, kind in [*((g, "claimable") for g in category_b),
                        *((g, "honest") for g in category_c)]:
        label = (label or "").strip()
        if label and _norm(label) not in seen:
            seen.add(_norm(label))
            out.append(_Candidate(label=label, kind=kind, surface_forms=(label,)))
    return out


def _covered(candidate: _Candidate, document_norm: str) -> bool:
    return any(_find(form, document_norm) >= 0 for form in candidate.surface_forms)


def _document_norm(section_contents: dict[str, str]) -> str:
    return _norm("\n".join(section_contents.values()))


def build_gap_hints(
    ledger: list[dict[str, Any]] | None,
    category_b: list[str],
    category_c: list[str],
    section_contents: dict[str, str],
) -> tuple[dict[str, list[GapHintItem]], list[GapHintItem]]:
    """Return (section_id -> hints, general hints) for the current document.

    Coverage is document-wide: a keyword present in ANY section suppresses its
    hint everywhere. Placement of the surviving hints reuses the deterministic
    token-overlap mapper (zero overlap -> general bucket).
    """
    doc = _document_norm(section_contents)
    open_candidates = [c for c in _candidates(ledger, category_b, category_c)
                       if not _covered(c, doc)]
    if not open_candidates:
        return {}, []

    by_label = {c.label: c for c in open_candidates}
    raw_map = map_gaps_to_sections(list(by_label.keys()), section_contents)

    def _items(labels: list[str]) -> list[GapHintItem]:
        return [GapHintItem(id=lbl, label=lbl, kind=by_label[lbl].kind) for lbl in labels]

    gap_map = {sid: _items(labels) for sid, labels in raw_map.items() if sid != "__general__"}
    return gap_map, _items(raw_map.get("__general__", []))


def resolved_gap_hints(
    ledger: list[dict[str, Any]] | None,
    category_b: list[str],
    category_c: list[str],
    contents_before: dict[str, str],
    contents_after: dict[str, str],
) -> list[str]:
    """Hint ids that an edit just covered (uncovered before, covered after).

    Purely informational for the UI — nothing is written back to the gap
    analysis (the evidence axis only moves via profile enrichment).
    """
    before = _document_norm(contents_before)
    after = _document_norm(contents_after)
    return [
        c.label
        for c in _candidates(ledger, category_b, category_c)
        if not _covered(c, before) and _covered(c, after)
    ]
