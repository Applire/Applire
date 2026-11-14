# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scope cluster and its one fact-built question (ADR-069 cl. 3 amended
2026-11-14, ADR-089 amended; B8-12 second half, Sorbet quality-1 WP-Q2 cluster a).

Before: a scope ledger row ("Führungsspanne ~120 MA") was clustered by the LLM
into a generic leadership cluster, and the MODE A question prompt received only
its label — no figure, no vault value, no verdict. Platform-1 (Stefan, Luna, two
runs): the posting's 120 was never put to the person, and the drafted answer
chips offered "…Führungsspanne von etwa 120 Mitarbeitenden verantwortet" — an
inflated first-person claim one click away (SF-ELICIT.19's harm).

Now every scope row whose status is ``partial`` or ``gap`` gets its own
persisted cluster ``scope-<kind>`` (:func:`carve_scope_clusters`, applied at both
clustering seams in ``services/gap.py``). The cluster carries the bar FACTS the
question needs, copied from the ledger row; :func:`scope_question` fills a DE/EN
template from them — no LLM call, no answer chips — and the session asks it once
(budget 1; no follow-up, retry or probe stays on it).

Wording: founder question Q2-1 (option A recommended and built; pending ruling).
Pure module: no I/O, no LLM.
"""

from __future__ import annotations

import re
from typing import Any

#: The cluster kind marker (persisted on ``gap_analyses.gap_clusters``; the
#: response schema ignores unknown keys, so the gaps page reads it unchanged).
SCOPE_KIND = "scope"
SCOPE_CLUSTER_PREFIX = "scope-"
#: ADR-089 amended 2026-11-14: a scope cluster's question is asked once.
SCOPE_QUESTIONS_PER_GAP = 1

_CURRENCY_RE = re.compile(r"(€|EUR|CHF|\$|USD|£|GBP)")


def is_scope_cluster(cluster: Any) -> bool:
    """True for a cluster carved by :func:`carve_scope_clusters`."""
    return isinstance(cluster, dict) and cluster.get("kind") == SCOPE_KIND


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _askable_scope_rows(keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    from applire.services.keyword_ledger import is_scope_entry

    rows = []
    for row in keyword_ledger or []:
        if not isinstance(row, dict) or not is_scope_entry(row):
            continue
        if row.get("status") not in ("partial", "gap"):
            continue
        weight = row.get("fit_weight")
        if not isinstance(weight, (int, float)) or float(weight) <= 0:
            continue
        if not str(row.get("concept") or "").strip():
            continue
        rows.append(row)
    return rows


def scope_facts(row: dict[str, Any]) -> dict[str, Any]:
    """The bar facts the question is built from — copied from the ledger row
    (ADR-062 cl. 1: facts only; the posting quote is kept for the gaps page's
    context, never rendered into the question)."""
    bar = row.get("bar") if isinstance(row.get("bar"), dict) else {}
    values = []
    for v in bar.get("candidate_values") or []:
        if isinstance(v, dict) and v.get("value") not in (None, ""):
            values.append({"value": v.get("value"), "entry": str(v.get("entry") or "")})
    attested = bar.get("attested") if isinstance(bar.get("attested"), dict) else None
    currency = _CURRENCY_RE.search(str(bar.get("quote") or ""))
    return {
        "kind": bar.get("kind"),
        "value": bar.get("value"),
        "value_max": bar.get("value_max"),
        "comparator": bar.get("comparator"),
        "currency": currency.group(1) if currency else None,
        "candidate_values": values,
        "attested": (
            {"quote": str(attested.get("quote") or ""), "entry": str(attested.get("entry") or "")}
            if attested and attested.get("quote")
            else None
        ),
    }


def _cluster_id(kind: Any, taken: set[str]) -> str:
    base = SCOPE_CLUSTER_PREFIX + re.sub(r"[^a-z0-9]+", "-", str(kind or "bar").lower()).strip("-")
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}-{n}", n + 1
    return cid


def carve_scope_clusters(
    clusters: list[dict[str, Any]] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Give every askable scope row (status partial/gap, counted) its own cluster.

    Idempotent, and the ONE implementation both clustering seams call (first
    analysis and ADR-089 carry-forward):
      * the scope concept leaves the OPEN members (``gaps``) of every other
        cluster — a cluster left with no member at all is dropped;
      * an existing scope cluster for the row (same concept) keeps its id and
        its per-gap record (``outcome``), and its facts/category are refreshed
        from today's row;
      * a row without one gets a fresh cluster, its record initialised against
        this ledger (``gap_coverage.initialise_cluster_record``).
    Category from the row: ``partial`` → B, ``gap`` → C (ADR-069 cl. 3's
    status mapping). Scope clusters are appended; the interview plan orders them
    (``interview_graph.gap_detector``)."""
    from applire.services.gap_coverage import _outcome_of, initialise_cluster_record

    rows = _askable_scope_rows(keyword_ledger)
    if not rows:
        return list(clusters or [])
    concepts = {_norm(r["concept"]) for r in rows}

    kept: list[dict[str, Any]] = []
    existing: dict[str, dict[str, Any]] = {}
    for cluster in clusters or []:
        if not isinstance(cluster, dict):
            kept.append(cluster)
            continue
        if is_scope_cluster(cluster):
            members = [m for m in (cluster.get("gaps") or []) if m]
            outcome = _outcome_of(cluster)
            key = next(
                (_norm(m) for m in [*members, *outcome["covered"], *outcome["declined"]]),
                _norm(cluster.get("label")),
            )
            existing.setdefault(key, cluster)
            continue
        gaps = [g for g in (cluster.get("gaps") or []) if _norm(g) not in concepts]
        if len(gaps) == len(cluster.get("gaps") or []):
            kept.append(cluster)
            continue
        outcome = _outcome_of(cluster)
        if not gaps and not outcome["covered"] and not outcome["declined"]:
            continue  # the cluster held only scope concepts
        kept.append({**cluster, "gaps": gaps})

    taken = {str(c.get("id")) for c in kept if isinstance(c, dict)}
    taken |= {str(c.get("id")) for c in existing.values()}
    out = list(kept)
    for row in rows:
        concept = str(row["concept"]).strip()
        facts = scope_facts(row)
        category = "B" if row.get("status") == "partial" else "C"
        bar = row.get("bar") if isinstance(row.get("bar"), dict) else {}
        prior = existing.get(_norm(concept))
        if prior is not None:
            out.append({**prior, "category": category, "scope": facts, "label": concept})
            continue
        cid = _cluster_id(facts.get("kind"), taken)
        taken.add(cid)
        out.append(
            initialise_cluster_record(
                {
                    "id": cid,
                    "kind": SCOPE_KIND,
                    "label": concept,
                    "category": category,
                    "gaps": [concept],
                    "jd_skills": [],
                    "jd_context": str(bar.get("quote") or ""),
                    "scope": facts,
                },
                keyword_ledger,
                denied_concepts,
            )
        )
    return out


# ---------------------------------------------------------------------------
# The question — DE/EN templates (FOUNDER-QUESTION Q2-1, option A)
# ---------------------------------------------------------------------------


def _num(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f.is_integer() else f"{f:g}".replace(".", ",")


def _amount(value: Any, lang: str) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f >= 1_000_000:
        n = _num(f / 1_000_000)
        return f"{n} Mio." if lang == "de" else f"{n.replace(',', '.')}M"
    if f >= 1_000:
        return f"{_num(f / 1_000)} Tsd." if lang == "de" else f"{_num(f / 1_000)}k"
    return _num(f)


def _figure(facts: dict[str, Any], lang: str) -> str:
    """The posting's figure as a phrase: "ca. 120", "mindestens 5 Mio. €", "80–120"."""
    budget = facts.get("kind") == "budget"
    fmt = (lambda v: _amount(v, lang)) if budget else _num
    value = fmt(facts.get("value"))
    comparator = facts.get("comparator")
    if comparator == "range" and facts.get("value_max") is not None:
        number = f"{value}–{fmt(facts['value_max'])}"
    else:
        prefix = {
            "de": {"approx": "ca. ", "min": "mindestens "},
            "en": {"approx": "about ", "min": "at least "},
        }[lang].get(comparator or "", "")
        number = f"{prefix}{value}"
    if budget and facts.get("currency"):
        number = f"{number} {facts['currency']}"
    return number


def _entry(label: str) -> str:
    return label.replace(" @ ", ", ").strip()


def _sort_key(v: dict[str, Any]) -> float:
    try:
        return -float(v.get("value"))
    except (TypeError, ValueError):
        return 0.0


def _profile_line(facts: dict[str, Any], lang: str) -> tuple[str, bool]:
    """What the profile holds so far, and whether it holds anything."""
    values = sorted(facts.get("candidate_values") or [], key=_sort_key)[:2]
    if values:
        parts = [f"{v['value']} ({_entry(v['entry'])})" if v.get("entry") else str(v["value"]) for v in values]
        joined = (" und " if lang == "de" else " and ").join(parts)
        return (f"In deinem Profil steht bisher: {joined}." if lang == "de"
                else f"Your profile so far says: {joined}."), True
    attested = facts.get("attested")
    if attested:
        where = f" ({_entry(attested['entry'])})" if attested.get("entry") else ""
        return (f"In deinem Profil steht dazu bisher: „{attested['quote']}“{where}." if lang == "de"
                else f"Your profile so far says: “{attested['quote']}”{where}."), True
    return ("Dein Profil nennt dazu noch keine Zahl." if lang == "de"
            else "Your profile has no figure for this yet."), False


def scope_question(cluster: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    """The scope cluster's one question, built from its facts. ``choices`` is
    ``None``: a scope chip would be a first-person claim about a span."""
    lang = "de" if str(lang or "").lower().startswith("de") else "en"
    facts = cluster.get("scope") if isinstance(cluster.get("scope"), dict) else {}
    budget = facts.get("kind") == "budget"
    figure = _figure(facts, lang)
    held, has_value = _profile_line(facts, lang)
    if lang == "de":
        opening = (f"Die Stelle nennt eine Budgetverantwortung von {figure}." if budget
                   else f"Die Stelle nennt eine Führungsspanne von {figure} Mitarbeitenden.")
        if budget:
            ask = ("Wie groß war das größte Budget, das du selbst verantwortet hast – dauerhaft "
                   "oder vertretungsweise – und wie ordnest du es gegenüber den " if has_value else
                   "Welches Budget hast du bisher selbst verantwortet – dauerhaft oder "
                   "vertretungsweise – und wie ordnest du das gegenüber den ")
        else:
            ask = ("Wie groß war die größte Spanne, die du selbst verantwortet hast – dauerhaft "
                   "oder vertretungsweise – und wie ordnest du sie gegenüber den " if has_value else
                   "Wie viele Personen hast du bisher selbst geführt oder verantwortet – dauerhaft "
                   "oder vertretungsweise – und wie ordnest du das gegenüber den ")
        question = f"{opening} {held} {ask}{figure} ein?"
    else:
        opening = (f"The posting names a budget responsibility of {figure}." if budget
                   else f"The posting names a team scope of {figure} people.")
        ask = ("What is the largest budget you have been responsible for yourself" if budget
               else "What is the largest team you have been responsible for yourself")
        if not has_value:
            ask = ("What budget have you been responsible for yourself so far" if budget
                   else "How many people have you led or been responsible for yourself so far")
        question = (f"{opening} {held} {ask} – permanently or as a deputy – and how does that "
                    "compare with the scale the posting names?")
    return {"question": question, "choices": None}


# ---------------------------------------------------------------------------
# The leadership-kind ask (founder ruling Q-2; COORD Q1→Q2, WP-Q2 builds it in
# the planner). ONE separate question — never folded into the scope question
# (RULING Q2-1) — for the roles whose team size is known and whose kind
# (disciplinary vs functional) is not. Asked at the scope topic (right after the
# team-size scope question) or, when the plan has none, at the end of a full
# interview whose posting weighs leadership; at most once per session; the answer
# is reconciled like any answer. Wording: the coordinator's example (Q-2).
# ---------------------------------------------------------------------------

LEADERSHIP_KIND = "leadership_kind"
LEADERSHIP_KIND_ID = "leadership-kind"
_KIND_MAX_ROLES = 3


def _stub_entries_needing_leadership_kind(profile_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Local stand-in for Q1's ``role_facts.entries_needing_leadership_kind`` until
    the branches merge: roles with a typed ``team_size`` > 0 and no recorded kind."""
    out = []
    for entry in (profile_json or {}).get("work_experience") or []:
        if not isinstance(entry, dict):
            continue
        size = entry.get("team_size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            continue
        if entry.get("leadership_kind"):
            continue
        out.append({"entry_id": entry.get("id"), "company": entry.get("company") or "",
                    "role": entry.get("role") or "", "team_size": size})
    return out


def leadership_kind_entries(profile_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Q1's predicate when present (``services/profile/role_facts.py``), else the stub."""
    try:
        from applire.services.profile.role_facts import entries_needing_leadership_kind
    except ImportError:
        return _stub_entries_needing_leadership_kind(profile_json)
    return list(entries_needing_leadership_kind(profile_json) or [])


def is_leadership_kind_cluster(cluster: Any) -> bool:
    return isinstance(cluster, dict) and cluster.get("kind") == LEADERSHIP_KIND


def is_fact_built_cluster(cluster: Any) -> bool:
    """A cluster whose one question is built from facts and asked once — the turn
    always advances (scope cluster, leadership-kind ask)."""
    return is_scope_cluster(cluster) or is_leadership_kind_cluster(cluster)


def plan_leadership_kind(
    cluster_ids: list[str],
    categories: dict[str, str],
    clusters_by_id: dict[str, dict[str, Any]],
    profile_json: dict[str, Any] | None,
    *,
    posting_weighs_leadership: bool,
) -> tuple[list[str], dict[str, str], dict[str, dict[str, Any]]]:
    """Insert the leadership-kind ask into a full interview plan (pure).

    Right after the team-size scope cluster when the plan holds one; otherwise at
    the end, only when the posting weighs leadership. Nothing when no listed role
    needs the kind, or the plan already holds the ask."""
    if LEADERSHIP_KIND_ID in cluster_ids:
        return cluster_ids, categories, clusters_by_id
    entries = [e for e in leadership_kind_entries(profile_json) if isinstance(e, dict)][:_KIND_MAX_ROLES]
    if not entries:
        return cluster_ids, categories, clusters_by_id
    anchor = next(
        (i for i, cid in enumerate(cluster_ids)
         if is_scope_cluster(clusters_by_id.get(cid))
         and ((clusters_by_id.get(cid) or {}).get("scope") or {}).get("kind") == "team_size"),
        None,
    )
    if anchor is None and not posting_weighs_leadership:
        return cluster_ids, categories, clusters_by_id
    cluster = {"id": LEADERSHIP_KIND_ID, "kind": LEADERSHIP_KIND, "label": LEADERSHIP_KIND_ID,
               "category": "B", "gaps": [], "jd_skills": [], "jd_context": "",
               "entries": entries}
    at = len(cluster_ids) if anchor is None else anchor + 1
    ids = [*cluster_ids[:at], LEADERSHIP_KIND_ID, *cluster_ids[at:]]
    return ids, {**categories, LEADERSHIP_KIND_ID: "B"}, {**clusters_by_id, LEADERSHIP_KIND_ID: cluster}


def leadership_kind_question(cluster: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    """The ask, one question for every listed role. No chips."""
    lang = "de" if str(lang or "").lower().startswith("de") else "en"
    entries = [e for e in cluster.get("entries") or [] if isinstance(e, dict)]
    def _where(e):
        return str(e.get("company") or e.get("role") or "").strip() or "—"
    if lang == "de":
        first = f"Bei {_where(entries[0])} steht eine Teamgröße von {entries[0].get('team_size')}"
        rest = "".join(f", bei {_where(e)} von {e.get('team_size')}" for e in entries[1:])
        ask = ("War das jeweils disziplinarische Führung (Personalverantwortung) oder fachliche Anleitung?"
               if len(entries) > 1 else
               "War das disziplinarische Führung (Personalverantwortung) oder fachliche Anleitung?")
        return {"question": f"{first}{rest}. {ask}", "choices": None}
    first = f"At {_where(entries[0])} your profile shows a team size of {entries[0].get('team_size')}"
    rest = "".join(f", at {_where(e)} of {e.get('team_size')}" for e in entries[1:])
    ask = ("In each case, was that disciplinary leadership (line management) or functional guidance?"
           if len(entries) > 1 else
           "Was that disciplinary leadership (line management) or functional guidance?")
    return {"question": f"{first}{rest}. {ask}", "choices": None}
