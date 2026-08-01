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

"""ADR-069 clauses 2–3 — the fact layer for quantified scope requirements.

ADR-062 classification (declared per clause 6): everything in this module is a
FACT. It assembles the confrontation — the JD's stated bar verbatim, and the
candidate's typed vault values WITH their field semantics — guarantees the
resulting ledger row exists, enforces the fail-closed floor (no typed vault
value ⇒ the status can never be ``direct``) and verifies a ``direct``
verdict's citation resolves to a real vault value. The sufficiency JUDGEMENT
itself is the model's, made inside the gap-analysis call: the vault's
``team_size`` counts direct reports in one role while a JD figure may be a
total organisational span, and a deterministic ``>=`` over those would
compare different quantities (ADR-069 Context — the refuted first draft).

Earned by (provenance): charter runs 10 and 12, ``operations_marcus_de`` —
the JD's headline requirement ("Gesamtverantwortung … ca. 120 Mitarbeitende")
never became a gap, a cluster, or an interview question, while the vault's
``team_size: 38`` sat unread in the same prompt; both blind reviewers named
the unaddressed span the application's single largest risk, twice (#387/#350).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# The stated semantics of each vault fact field, spelled out for the judge —
# the mismatch between these and what a JD figure measures is exactly why the
# status is a judgement, not a >= (ADR-069 clause 2).
_KIND_SEMANTICS = {
    "team_size": "direct reports in that one role (not total organisational span)",
    "budget": "budget managed in that one role, as the candidate stated it (free text)",
}

# Deterministic concept labels per JD language (ADR-038 labels pattern —
# the concept string surfaces in clusters, the interview and the UI).
_KIND_LABELS = {
    "team_size": {"de": "Führungsspanne", "en": "Team scope"},
    "budget": {"de": "Budgetverantwortung", "en": "Budget responsibility"},
}

_COMPARATOR_SYMBOLS = {"approx": "~", "min": "≥", "exact": "", "range": ""}

_CLAIMABLE_STATUSES = ("direct", "partial")


def _fmt_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _fmt_amount(value: float, lang: str) -> str:
    """Human-scale money formatting for the budget concept label."""
    if value >= 1_000_000:
        scaled = _fmt_number(value / 1_000_000)
        return f"{scaled} Mio." if lang == "de" else f"{scaled}M"
    if value >= 1_000:
        return f"{_fmt_number(value / 1_000)}k"
    return _fmt_number(value)


def scope_concept_label(req: dict[str, Any], jd_language: str | None) -> str:
    """Deterministic ledger concept for a scope requirement (a fact: assembled
    from the bar's own stored values, no prose read)."""
    lang = "de" if (jd_language or "").startswith("de") else "en"
    kind = req.get("kind", "")
    label = _KIND_LABELS.get(kind, {}).get(lang, kind)
    fmt = (lambda v: _fmt_amount(v, lang)) if kind == "budget" else _fmt_number
    value = fmt(req.get("value", 0))
    if req.get("comparator") == "range" and req.get("value_max") is not None:
        number = f"{value}–{fmt(req['value_max'])}"
    else:
        number = f"{_COMPARATOR_SYMBOLS.get(req.get('comparator', ''), '')}{value}"
    unit = " MA" if kind == "team_size" and lang == "de" else ""
    return f"{label} {number}{unit}"


def collect_candidate_values(
    profile_json: dict[str, Any] | None, kind: str
) -> list[dict[str, Any]]:
    """The candidate's typed vault values for one scope kind, verbatim, each
    carrying its entry label and its field semantics (#328's fields, finally
    confronted with the JD side)."""
    values: list[dict[str, Any]] = []
    for entry in (profile_json or {}).get("work_experience") or []:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role") or ""
        company = entry.get("company") or ""
        label = f"{role} @ {company}".strip(" @ ") or company or role
        if kind == "team_size":
            raw = entry.get("team_size")
            if isinstance(raw, int) and not isinstance(raw, bool):
                values.append(
                    {
                        "value": raw,
                        "entry": label,
                        "semantics": _KIND_SEMANTICS["team_size"],
                    }
                )
        elif kind == "budget":
            raw = entry.get("budget_managed")
            if isinstance(raw, str) and raw.strip():
                values.append(
                    {
                        "value": raw.strip(),
                        "entry": label,
                        "semantics": _KIND_SEMANTICS["budget"],
                    }
                )
    return values


def build_scope_prompt_block(
    scope_requirements: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
    jd_language: str | None,
) -> list[dict[str, Any]]:
    """The SCOPE REQUIREMENTS section for the gap-analysis prompt: bar + typed
    candidate values, both verbatim, semantics stated. Empty list when the JD
    states no bar (the prompt block is omitted entirely)."""
    block: list[dict[str, Any]] = []
    for req in scope_requirements or []:
        if not isinstance(req, dict) or req.get("kind") not in _KIND_SEMANTICS:
            continue
        block.append(
            {
                "concept": scope_concept_label(req, jd_language),
                "kind": req["kind"],
                "jd_quote": req.get("quote", ""),
                "jd_value": req.get("value"),
                "jd_value_max": req.get("value_max"),
                "comparator": req.get("comparator", "approx"),
                "level": req.get("level", "required"),
                "candidate_values": collect_candidate_values(profile_json, req["kind"]),
            }
        )
    return block


def _fmt_candidate_values(values: list[dict[str, Any]]) -> str:
    if not values:
        return "no typed vault value"
    return "; ".join(f"{v['value']} ({v['entry']} — {v['semantics']})" for v in values)


def build_scope_ledger_entries(
    prompt_block: list[dict[str, Any]],
    scope_classifications: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Turn the model's scope judgements into ledger entries, floor applied.

    Deterministic guarantees (ADR-069 clause 2):
      * every prompt-block entry becomes a ledger row — existence is never the
        model's choice (a missing judgement lands as ``gap`` = unknown);
      * no typed candidate value ⇒ the status can never be ``direct``;
      * a ``direct`` verdict must cite one of the candidate values' ``entry``
        strings; an unresolvable citation downgrades to ``partial`` (both raw
        numbers stay recorded on the ``bar`` facet — fail-closed, loudly);
      * the ledger ``evidence`` is composed HERE from the recorded facts (the
        derivation-semantics rule, SF-GAP.4) — the model's reason is quoted
        inside it as a judgement, never presented as a vault fact.
    """
    by_concept: dict[str, dict[str, Any]] = {}
    for cls in scope_classifications or []:
        if isinstance(cls, dict) and isinstance(cls.get("concept"), str):
            by_concept[cls["concept"].strip()] = cls

    entries: list[dict[str, Any]] = []
    for item in prompt_block:
        concept = item["concept"]
        values = item.get("candidate_values") or []
        cls = by_concept.get(concept, {})
        status = cls.get("status")
        reason = (cls.get("reason") or "").strip()
        cited = (cls.get("cited_entry") or "").strip()

        if status not in ("direct", "partial", "gap"):
            status = "gap"
            reason = reason or "not judged (no scope classification returned)"

        if not values and status != "gap":
            logger.warning(
                "scope_requirements: floor forced %r from %r to 'gap' — no typed "
                "vault value of kind %r exists (ADR-069 clause 2 fail-closed).",
                concept,
                status,
                item.get("kind"),
            )
            status = "gap"
        elif status == "direct":
            if not any(cited == v.get("entry") for v in values):
                logger.warning(
                    "scope_requirements: 'direct' on %r downgraded to 'partial' — "
                    "cited_entry %r resolves to no candidate value (ADR-069 "
                    "clause 2 citation check).",
                    concept,
                    cited,
                )
                status = "partial"
                cited = ""

        evidence = (
            f"JD bar ({item.get('kind')}): \"{item.get('jd_quote', '')}\". "
            f"Vault: {_fmt_candidate_values(values)}. "
            f"Judgement: {reason or 'none'}"
        )
        entries.append(
            {
                "concept": concept,
                "surface_forms": [],
                "sources": [item.get("level", "required")],
                "fit_weight": 1.0 if item.get("level", "required") == "required" else 0.5,
                "status": status,
                "evidence": evidence,
                "claimable": status in _CLAIMABLE_STATUSES,
                "narrative_backed": True,
                "bar": {
                    "kind": item.get("kind"),
                    "value": item.get("jd_value"),
                    "value_max": item.get("jd_value_max"),
                    "comparator": item.get("comparator"),
                    "quote": item.get("jd_quote", ""),
                    "level": item.get("level", "required"),
                    "candidate_values": values,
                    "cited_entry": cited or None,
                },
            }
        )
    return entries
