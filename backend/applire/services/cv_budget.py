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

"""Deterministic per-role bullet-count budgets (E042 / US237, ADR-051 §3 + amendment §5/§6).

Pure functions only — no DB, no LLM, no I/O — so the tier math is hermetically
unit-testable and safely reusable by Task 1.3's post-render condense loop.

Tier model (ADR-051 §3, amendment §6):
    Base ceilings at the region-standard target page count:
        top    ("recent + relevant")  -> ceiling 5, range 4-5
        mid    ("mid-age or neutral") -> ceiling 3, range 2-3
        bottom ("old / irrelevant, one-liner") -> ceiling 1, range 0-1
    Every FULL page of ``target_pages`` above the region standard raises every
    tier ceiling by 1 (DACH target 3 -> 6/4/2). A target below standard (only
    reachable at 1, since DACH standard is 2 and the resolver floors at 1)
    lowers every ceiling by 1 vs. the base, floored at 1 for the top tier and
    0 for the others — deterministic, see :func:`_tier_table`.

Tier assignment = recency x relevance (amendment §5):
    Recency buckets a role's age from its END date (or its START date when the
    role is open-ended but not identified as the current position — see
    :func:`_recency_tier` for the exact fallback) against a deterministic
    ``today``: <=6y "recent", <=12y "mid", older "old". A role is always
    "recent" when ``is_current`` is True, or it has no end_date AND is the
    entry with the latest start_date (the best-effort "this must be the
    current job" signal when ``is_current`` was never annotated).

    Relevance is the count of CLAIMABLE keyword-ledger entries (surface_forms
    union {concept}) present in the role's own bullet/project text, via the
    SAME shared presence predicate the ATS audit uses
    (``applire.services.ats_audit.surface_present``) — never a second matcher.
    >=2 hits "relevant", 1 hit "neutral", 0 hits "irrelevant".

    Combine: recent+(relevant|neutral)->top; recent+irrelevant->mid;
    mid-age+relevant->top; mid-age+(neutral|irrelevant)->mid;
    old+relevant->mid; old+(neutral|irrelevant)->bottom.

Cross-language fallback (amendment §5, binding): the ledger's surface forms
are JD-language while the profile may be written in another language, so an
all-zero hit count across every role never means "everything is irrelevant"
— it means the relevance signal is VOID. When there is no claimable ledger at
all, or every role scores 0 hits, tiers are assigned by recency alone
(recent->top, mid->mid, old->bottom).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from applire.norms import DEFAULT_REGION, REGION_NORMS

TierName = Literal["top", "mid", "bottom"]

_BASE_CEILINGS: dict[TierName, int] = {"top": 5, "mid": 3, "bottom": 1}
# Floors apply when target_pages sits below the region standard (only target=1
# is reachable today, since resolve_target_pages floors at 1) — never let a
# ceiling go negative, and never strip the top tier down to a bare 0.
_CEILING_FLOORS: dict[TierName, int] = {"top": 1, "mid": 0, "bottom": 0}

_RECENT_MAX_YEARS = 6
_MID_MAX_YEARS = 12

_RECENCY_ONLY_TIER: dict[str, TierName] = {"recent": "top", "mid": "mid", "old": "bottom"}

# recency x relevance -> tier (ADR-051 amendment §5).
_COMBINE: dict[tuple[str, str], TierName] = {
    ("recent", "relevant"): "top",
    ("recent", "neutral"): "top",
    ("recent", "irrelevant"): "mid",
    ("mid", "relevant"): "top",
    ("mid", "neutral"): "mid",
    ("mid", "irrelevant"): "mid",
    ("old", "relevant"): "mid",
    ("old", "neutral"): "bottom",
    ("old", "irrelevant"): "bottom",
}


@dataclass(frozen=True)
class BulletTier:
    """One row of the tier table actually used for a given target/region (Task 1.3 reuses this)."""

    name: TierName
    max_bullets: int
    min_bullets: int


@dataclass(frozen=True)
class RoleBudget:
    """The resolved budget for one work-experience entry."""

    work_entry_id: str
    tier: TierName
    max_bullets: int
    company: str = ""
    role: str = ""


@dataclass(frozen=True)
class BudgetResult:
    """Output of :func:`compute_bullet_budgets` — per-role budgets plus the tier table used."""

    roles: dict[str, RoleBudget]
    tiers: dict[TierName, BulletTier]
    target_pages: int
    region: str


def _tier_table(target_pages: int, region: str) -> dict[TierName, BulletTier]:
    """Scale the base tier ceilings by full pages of ``target_pages`` above/below the
    region standard (ADR-051 §3 + amendment §6)."""
    standard = REGION_NORMS[region].cv_standard_pages
    delta = target_pages - standard
    table: dict[TierName, BulletTier] = {}
    for name, base in _BASE_CEILINGS.items():
        ceiling = max(_CEILING_FLOORS[name], base + delta)
        min_bullets = max(0, ceiling - 1)
        table[name] = BulletTier(name=name, max_bullets=ceiling, min_bullets=min_bullets)
    return table


def _parse_year_month(date_str: str | None) -> tuple[int, int] | None:
    """Parse a profile partial date ('YYYY', 'YYYY-MM', 'YYYY-MM-DD', ...) into
    (year, month); bare years assume mid-year (month 6) for age estimation.
    Unparseable/missing values return None."""
    import re

    if not date_str:
        return None
    m = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?", str(date_str))
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 6
    month = min(max(month, 1), 12)
    return year, month


def _years_ago(date_str: str | None, today: date) -> float | None:
    parsed = _parse_year_month(date_str)
    if parsed is None:
        return None
    year, month = parsed
    return (today - date(year, month, 1)).days / 365.25


def _latest_start_id(entries: list[dict[str, Any]]) -> str | None:
    """The work-entry id with the latest parseable start_date (ties keep the first
    seen) — used as the "no end_date but clearly current" recency signal."""
    best_id: str | None = None
    best_key: tuple[int, int] | None = None
    for e in entries:
        key = _parse_year_month(e.get("start_date"))
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_key = key
            best_id = str(e.get("id") or "")
    return best_id


def _recency_tier(entry: dict[str, Any], today: date, latest_start_id: str | None) -> str:
    """"recent" / "mid" / "old" — see module docstring for the exact rule."""
    is_current = entry.get("is_current") is True
    no_end = not entry.get("end_date")
    eid = str(entry.get("id") or "")
    if is_current or (no_end and latest_start_id is not None and eid == latest_start_id):
        return "recent"
    # Not identified as current: age from end_date, falling back to start_date when
    # end_date is missing/unparseable (deterministic best-effort, documented above).
    anchor = entry.get("end_date") or entry.get("start_date")
    years = _years_ago(anchor, today)
    if years is None:
        # Wholly unparseable date data — conservative fallback: treat as "old" rather
        # than risk over-budgeting an unknown-age role.
        return "old"
    if years <= _RECENT_MAX_YEARS:
        return "recent"
    if years <= _MID_MAX_YEARS:
        return "mid"
    return "old"


def _entry_text_norm(entry: dict[str, Any]) -> str:
    """Normalised text blob of a work entry's own bullets/responsibilities/achievements
    plus any attached project text (see :func:`attach_projects`), via the shared
    ``ats_audit._norm`` fold so presence-matching stays consistent with the ATS panel."""
    from applire.services.ats_audit import _norm

    parts: list[str] = []
    for key in ("bullets", "responsibilities", "achievements"):
        parts.extend(s for s in (entry.get(key) or []) if isinstance(s, str))
    for proj in entry.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        desc = proj.get("description")
        if isinstance(desc, str):
            parts.append(desc)
        for key in ("bullets", "responsibilities", "achievements"):
            parts.extend(s for s in (proj.get(key) or []) if isinstance(s, str))
    return _norm("\n".join(parts))


def _hit_count(entry_text_norm: str, claimable_ledger: list[dict[str, Any]]) -> int:
    """Count of claimable ledger entries whose surface_forms union {concept} are
    present in ``entry_text_norm`` via the shared ATS presence predicate."""
    from applire.services.ats_audit import surface_present

    hits = 0
    for led in claimable_ledger:
        forms = list(led.get("surface_forms") or [])
        if led.get("concept"):
            forms.append(led["concept"])
        if any(surface_present(f, entry_text_norm) for f in forms):
            hits += 1
    return hits


def attach_projects(work_entries: list[dict[str, Any]], projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group each profile ``ProjectEntry`` under its parent work entry so relevance
    hit-counting can see project text too — mirrors ``services.cv._nest_projects``'s
    id/company identity rule (``associated_experience`` is an id on the reconcile
    path, a company NAME on the CV-extraction path). Pure: returns NEW dicts, never
    mutates ``work_entries``/``projects``.
    """
    by_id: dict[str, list[dict[str, Any]]] = {}
    by_company: dict[str, list[dict[str, Any]]] = {}
    for p in projects or []:
        ref = p.get("associated_experience")
        if not ref:
            continue
        key = str(ref).strip()
        by_id.setdefault(key, []).append(p)
        by_company.setdefault(key.lower(), []).append(p)

    enriched: list[dict[str, Any]] = []
    for w in work_entries:
        wid = str(w.get("id") or "")
        company = (w.get("company") or "").strip().lower()
        assoc = list(by_id.get(wid) or [])
        if company:
            for p in by_company.get(company, []):
                if p not in assoc:
                    assoc.append(p)
        enriched.append({**w, "projects": assoc})
    return enriched


def compute_bullet_budgets(
    work_entries: list[dict[str, Any]],
    keyword_ledger: list[dict[str, Any]] | None,
    target_pages: int,
    region: str = DEFAULT_REGION,
    today: date | None = None,
) -> BudgetResult:
    """Compute the per-role bullet-count ceiling for every work entry (US237).

    ``work_entries`` — the profile's work-experience list; each dict carries at
    least ``id``, ``start_date``, ``end_date``, ``is_current``, and its bullet
    text under ``responsibilities``/``achievements`` (WorkEntry's own field
    names) and/or a pre-combined ``bullets`` key. Attach associated
    ``ProjectEntry`` text under a ``projects`` key first via
    :func:`attach_projects` so relevance hit-counting sees it too.

    ``keyword_ledger`` — the stored ``GapAnalysis.keyword_ledger`` list (or
    ``None``/empty for legacy pre-E037 rows); only ``claimable`` entries count
    toward relevance (ADR-048).

    ``today`` defaults to ``date.today()`` — always pass it explicitly in tests
    for determinism.
    """
    today = today or date.today()
    tiers = _tier_table(target_pages, region)

    claimable = [e for e in (keyword_ledger or []) if e.get("claimable")]
    latest_start_id = _latest_start_id(work_entries)

    hit_counts: dict[str, int] = {}
    for entry in work_entries:
        eid = str(entry.get("id") or "")
        hit_counts[eid] = _hit_count(_entry_text_norm(entry), claimable) if claimable else 0

    # Cross-language fallback (amendment §5): no claimable ledger, or every role
    # scored zero hits -> the relevance signal is void, fall back to recency-only.
    relevance_void = not claimable or (
        bool(work_entries) and all(h == 0 for h in hit_counts.values())
    )

    roles: dict[str, RoleBudget] = {}
    for entry in work_entries:
        eid = str(entry.get("id") or "")
        recency = _recency_tier(entry, today, latest_start_id)
        if relevance_void:
            tier_name = _RECENCY_ONLY_TIER[recency]
        else:
            hits = hit_counts.get(eid, 0)
            relevance = "relevant" if hits >= 2 else "neutral" if hits == 1 else "irrelevant"
            tier_name = _COMBINE[(recency, relevance)]
        spec = tiers[tier_name]
        roles[eid] = RoleBudget(
            work_entry_id=eid,
            tier=tier_name,
            max_bullets=spec.max_bullets,
            company=str(entry.get("company") or ""),
            role=str(entry.get("role") or ""),
        )

    return BudgetResult(roles=roles, tiers=tiers, target_pages=target_pages, region=region)


def render_budget_table(budget: BudgetResult) -> str:
    """Render the per-role budgets as a prompt fragment shared by both generation
    paths (ADR-051 §3). Returns "" for an empty role set (nothing to inject)."""
    if not budget.roles:
        return ""
    lines = [
        f"ROLE BULLET BUDGETS (ADR-051 §3) — this document targets {budget.target_pages} "
        f"page(s) ({budget.region} region norm). Each work-history role below has a maximum "
        "bullet count — a per-role ceiling, not a quota to fill. Respect it by prioritising "
        "the most JD-relevant achievements; condense older/less relevant roles toward a "
        "single line rather than padding them out.",
    ]
    for rb in budget.roles.values():
        label = " — ".join(p for p in (rb.company, rb.role) if p) or rb.work_entry_id
        lines.append(f"  - [{rb.work_entry_id}] {label}: max {rb.max_bullets} bullet(s) (tier: {rb.tier})")
    return "\n".join(lines)


def role_budget_line(budget: BudgetResult, work_entry_id: str) -> str:
    """Render a single role's max-bullets constraint (segmented per-role prompt)."""
    rb = budget.roles.get(work_entry_id)
    if rb is None:
        return ""
    return (
        f"MAX BULLETS FOR THIS ENTRY: {rb.max_bullets} (tier: {rb.tier}) — a ceiling, not a "
        "quota; prioritise the most JD-relevant achievements and condense the rest."
    )
