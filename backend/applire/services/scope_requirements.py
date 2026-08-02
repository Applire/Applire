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
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ADR-070 clause 1 — quote-resolution normalisation: NFKC + apostrophe fold +
# dash fold + whitespace collapse + lower. Mirrors ats_audit._norm plus the
# apostrophe fold from cross_document._normalize_punct (the U+2019 lesson:
# a model retyping typographic punctuation as ASCII must still resolve).
_APOSTROPHE_CHARS = "’ʼ‘‛´`"
_QUOTE_CHARS = "“”„‟«»"
_DIGIT_RE = re.compile(r"\d")


def _quote_states_a_figure(quote_norm: str) -> bool:
    """ADR-070 clause 1, amended 2026-08-02 (#421): a digit, or a spelled EN/DE
    integer per the shared ``stance._spelled_figures`` word tables — German
    prose writes small team sizes as words ("zwei Werkstudierenden"), and the
    digit-only gate starved exactly the small-team case the attestation rail
    exists for. The tables exclude the standalone German article "ein/eine",
    so the widening cannot admit a figure-free quote (fail-closed preserved).
    """
    if _DIGIT_RE.search(quote_norm):
        return True
    from applire.services.profile.reconcile.stance import _spelled_figures

    return bool(_spelled_figures(quote_norm))

# The vault prose fields an attested quote may resolve against — one text node,
# not a concatenation (the quote must live in a single real bullet).
_ATTESTED_PROSE_FIELDS = ("responsibilities", "achievements")

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


def _norm_quote(s: str) -> str:
    """ADR-070 clause 1 — the quote-resolution fold (see module constants)."""
    s = unicodedata.normalize("NFKC", s or "")
    for ch in _APOSTROPHE_CHARS:
        s = s.replace(ch, "'")
    for ch in _QUOTE_CHARS:
        s = s.replace(ch, '"')
    s = re.sub(r"[-‐-―−]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


def _work_entry_label(entry: dict[str, Any]) -> str:
    role = entry.get("role") or ""
    company = entry.get("company") or ""
    return f"{role} @ {company}".strip(" @ ") or company or role


def verify_attested_evidence(
    attested: Any, profile_json: dict[str, Any] | None
) -> dict[str, Any] | None:
    """ADR-070 clause 1 — fail-closed verification of a model-cited attestation.

    The model cites; code verifies (ADR-061). Checks, in order: the shape holds
    (``quote`` and ``unit`` both non-empty), the quote carries a numeric figure
    (a scope attestation without a number is not one), and the quote resolves —
    normalised substring of ONE real vault prose node (``responsibilities`` /
    ``achievements``), the model-cited entry tried first, every other work entry
    after (the resolved label is stored, never the model's string). Any failure
    drops the attestation with a run-visible warning and returns ``None``.

    Deliberately blind to two things the ADR names as accepted limits: the
    quote's BEARING on the bar (a judgement no code check can make — the
    ``unit`` travelling with the quote is the mitigation) and its PROVENANCE
    (the vault has no per-bullet source marker; CV-imported prose is as
    eligible as interview testimony).
    """
    if not isinstance(attested, dict):
        return None
    quote = str(attested.get("quote") or "").strip()
    unit = str(attested.get("unit") or "").strip()
    cited = str(attested.get("entry") or "").strip()
    if not quote or not unit:
        if quote or unit or cited:
            logger.warning(
                "scope_requirements: attested_evidence dropped — quote and unit are "
                "both required (ADR-070 clause 1 fail-closed). Got quote=%r unit=%r.",
                bool(quote),
                unit,
            )
        return None
    quote_norm = _norm_quote(quote)
    if not _quote_states_a_figure(quote_norm):
        logger.warning(
            "scope_requirements: attested_evidence dropped — the quote states no "
            "numeric figure, digit or spelled (ADR-070 clause 1, #421): %r",
            quote[:120],
        )
        return None
    entries = [
        e
        for e in (profile_json or {}).get("work_experience") or []
        if isinstance(e, dict)
    ]
    entries.sort(key=lambda e: 0 if _work_entry_label(e) == cited else 1)
    for entry in entries:
        for field in _ATTESTED_PROSE_FIELDS:
            for node in entry.get(field) or []:
                if isinstance(node, str) and quote_norm and quote_norm in _norm_quote(node):
                    return {
                        "entry": _work_entry_label(entry),
                        "quote": quote,
                        "unit": unit,
                    }
    logger.warning(
        "scope_requirements: attested_evidence dropped — the quote resolves to no "
        "vault prose node (ADR-070 clause 1 fail-closed): %r (cited entry %r)",
        quote[:120],
        cited,
    )
    return None


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
    profile_json: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Turn the model's scope judgements into ledger entries, floor applied.

    Deterministic guarantees (ADR-069 clause 2, ADR-070 clause 1):
      * every prompt-block entry becomes a ledger row — existence is never the
        model's choice (a missing judgement lands as ``gap`` = unknown);
      * a ``direct`` verdict must cite one of the TYPED candidate values'
        ``entry`` strings; an unresolvable citation downgrades to ``partial``
        (both raw numbers stay recorded on the ``bar`` facet — fail-closed,
        loudly); no typed candidate value ⇒ the status can never be ``direct``;
      * a model-cited ``attested_evidence`` is verified fail-closed
        (:func:`verify_attested_evidence`) and stored as ``bar.attested``; a
        VERIFIED attestation lifts the partial-floor only — ``partial`` becomes
        legal with zero typed values (known-something beats unknown), ``direct``
        stays typed-and-cited (ADR-070 clause 1). ``profile_json`` omitted →
        attestation silently unavailable, everything else unchanged;
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
        attested = (
            verify_attested_evidence(cls.get("attested_evidence"), profile_json)
            if profile_json is not None
            else None
        )

        if status not in ("direct", "partial", "gap"):
            status = "gap"
            reason = reason or "not judged (no scope classification returned)"

        if not values and status != "gap":
            if attested is not None:
                if status == "direct":
                    logger.warning(
                        "scope_requirements: 'direct' on %r downgraded to 'partial' "
                        "— an attestation supports at most 'partial'; 'direct' "
                        "requires a typed vault value (ADR-070 clause 1).",
                        concept,
                    )
                status = "partial"
                cited = ""
            else:
                logger.warning(
                    "scope_requirements: floor forced %r from %r to 'gap' — no typed "
                    "vault value of kind %r exists and no verified attestation "
                    "(ADR-069 clause 2 fail-closed).",
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

        attested_fmt = (
            f"Attested ({attested['unit']}): \"{attested['quote']}\" "
            f"({attested['entry']}). "
            if attested
            else ""
        )
        evidence = (
            f"JD bar ({item.get('kind')}): \"{item.get('jd_quote', '')}\". "
            f"Vault: {_fmt_candidate_values(values)}. "
            f"{attested_fmt}"
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
                    # #421: the prompt contract says cited_entry is REQUIRED for
                    # `direct` and omitted otherwise; only the `direct` path
                    # validates it against a typed value, so a non-direct row
                    # never carries one (the floor branch used to ship a `gap`
                    # row still holding the model's unresolvable citation).
                    "cited_entry": (cited or None) if status == "direct" else None,
                    "attested": attested,
                },
            }
        )
    return entries


def render_scope_positioning_block(
    keyword_ledger: list[dict[str, Any]] | None, jd_language: str | None
) -> str:
    """ADR-070 clause 2 — the scope-positioning prompt block, shared verbatim by
    the CV and cover-letter writers (ADR-066: one logical operation, one
    implementation).

    Renders the CANDIDATE side only, for ``partial`` scope entries carrying
    candidate material: the kind's language-keyed label WITHOUT any number, the
    typed vault values with their entry labels and field semantics, and the
    verified attested quote with its entry and its ``unit``. It never renders
    ``concept``, ``bar.value``, ``bar.value_max`` or ``bar.quote`` — no string
    containing the JD's own figure can enter this block, so the run-#7
    pathology (an instrument telling the writer to surface the posting's
    number) is structurally impossible HERE. (The letter's raw JD excerpt
    still carries the posting's number elsewhere in the prompt — that channel
    is instruction-guarded, as it always has been; this block's structural
    claim is scoped to this block.)

    ``direct`` renders nothing (met needs no positioning); ``gap`` renders
    nothing (an honest letter cannot position evidence that does not exist —
    ADR-070 clause 5's explicit limitation). Empty → "" so callers add nothing.
    """
    lang = "de" if (jd_language or "").startswith("de") else "en"
    items: list[str] = []
    for entry in keyword_ledger or []:
        if not isinstance(entry, dict) or not entry.get("bar"):
            continue
        if entry.get("status") != "partial":
            continue
        bar = entry["bar"]
        attested = bar.get("attested")
        values = bar.get("candidate_values") or []
        if not attested and not values:
            continue
        kind = bar.get("kind", "")
        label = _KIND_LABELS.get(kind, {}).get(lang, kind or "scope")
        lines = [f"- {label}:"]
        for v in values:
            lines.append(
                f"  typed vault value: {v.get('value')} "
                f"({v.get('entry')} — {v.get('semantics')})"
            )
        if attested:
            lines.append(
                f"  attested in the vault: \"{attested.get('quote')}\" "
                f"({attested.get('entry')} — unit: {attested.get('unit')})"
            )
        items.append("\n".join(lines))
    if not items:
        return ""
    header = [
        "=== POSITIONING: SCOPE (ADR-070) — the candidate's own scale evidence ===",
        "The posting states a quantified scope requirement (team scope / budget). The",
        "candidate's REAL scale evidence is listed below — it is the strongest available",
        "answer to the posting's scale question, and it is REQUIRED content: state it",
        "honestly and prominently, as exactly what its own unit/semantics say it is.",
        "NEVER state or imply the posting's own figure as something the candidate has",
        "done, led, or held — the posting's number is the employer's question; the",
        "values below are the candidate's answer.",
        "For a CV: a bullet grounded in the attested statement belongs in its matching",
        "role, its figure kept verbatim. For a cover letter: fold ONE honest, specific",
        "statement of this scale into the positioning/motivation content.",
        "",
    ]
    return "\n".join(header + items)
