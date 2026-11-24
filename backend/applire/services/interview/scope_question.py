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

# MD-Q3 / adversarial M5: the unit and the qualifier are read from the bar's OWN
# figure in its quote — the figure the bar's value names (verified there), the
# currency written in or right next to it, the qualifier written right before it.
# Never a substring anywhere in the quote ("EUROPA-Region" is no EUR), never
# invented (no unit on the figure → none printed; no qualifier → none printed).
from applire.services.scope_requirements import (  # noqa: E402 — one unit reader (M5/N5)
    _UNIT_CODE,
    figure_currencies,
)

#: The qualifier the posting writes right before its figure (casefolded window).
_QUALIFIER_BEFORE = (
    ("upto", re.compile(r"(?:bis\s+zu|maximal|höchstens|max\.|up\s+to|at\s+most)\s*$")),
    ("min", re.compile(r"(?:mindestens|min\.|wenigstens|at\s+least|\bab)\s*$")),
    ("over", re.compile(r"(?:mehr\s+als|über|more\s+than|\bover)\s*$")),
    ("approx", re.compile(
        r"(?:ca\.|circa|zirka|rund|etwa|ungefähr|~|about|around|approx\.?|approximately|roughly)\s*$"
    )),
)
_QUALIFIER_WORDS = {
    "de": {"approx": "ca. ", "min": "mindestens ", "upto": "bis zu ", "over": "über "},
    "en": {"approx": "about ", "min": "at least ", "upto": "up to ", "over": "more than "},
}


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
        bar = row.get("bar") if isinstance(row.get("bar"), dict) else {}
        # L3 / M5 (MD-Q3): a bar without a number, or whose number its own quote
        # does not state, is not put to the person — the question would print
        # "ca. None" or a figure the posting never wrote (``verified`` is False
        # for both).
        if not _figure_facts(bar.get("kind"), bar.get("value"), bar.get("quote"))["verified"]:
            continue
        rows.append(row)
    return rows


def _row_groups(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """M4 (MD-Q3): rows naming the same bar — the same concept, or the same kind
    and figures — form ONE group: one cluster, one id, one question."""
    groups: list[list[dict[str, Any]]] = []
    keys: list[set[Any]] = []
    for row in rows:
        bar = row.get("bar") if isinstance(row.get("bar"), dict) else {}
        own = {
            ("c", _norm(row.get("concept"))),
            ("b", bar.get("kind"), _number(bar.get("value")), _number(bar.get("value_max"))),
        }
        for i, k in enumerate(keys):
            if k & own:
                groups[i].append(row)
                k |= own
                break
        else:
            groups.append([row])
            keys.append(set(own))
    return groups


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _figure_facts(kind: Any, value: Any, quote: Any) -> dict[str, Any]:
    """The bar's figure as its quote writes it: whether the quote states the
    value (``verified``), the currency on it, and the qualifier before it."""
    out: dict[str, Any] = {"verified": False, "currency": None, "qualifier": None}
    number = _number(value)
    text = str(quote or "")
    if number is None or not text.strip():
        return out
    from applire.services.oracle.matchers.figures import (
        extract_figures,
        extract_range_bare_numbers,
        extract_spelled_figures,
    )

    wanted = str(int(number)) if number.is_integer() else None
    from applire.services.scope_requirements import small_figures

    figures = (extract_figures(text) + extract_spelled_figures(text)
               + extract_range_bare_numbers(text)
               + (small_figures(text) if kind == "team_size" else []))
    hit = next(
        (f for f in figures if f.kind in ("number", "currency") and wanted and f.value == wanted),
        None,
    )
    if hit is None:
        return out
    out["verified"] = True
    start = text.find(hit.raw)
    if start < 0:
        return out
    if kind == "budget":
        units = figure_currencies(text, hit.raw, start)
        out["currency"] = next(iter(units)) if len(units) == 1 else None
    window = " ".join(text[max(0, start - 24):start].casefold().split())
    if hit.raw.rstrip().endswith("+"):
        out["qualifier"] = "min"
    else:
        out["qualifier"] = next((q for q, rx in _QUALIFIER_BEFORE if rx.search(window)), None)
    return out


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
    figure = _figure_facts(bar.get("kind"), bar.get("value"), bar.get("quote"))
    return {
        "kind": bar.get("kind"),
        "value": bar.get("value"),
        "value_max": bar.get("value_max"),
        "comparator": bar.get("comparator"),
        "currency": figure["currency"],
        "qualifier": figure["qualifier"],
        "candidate_values": values,
        "attested": (
            {"quote": str(attested.get("quote") or ""), "entry": str(attested.get("entry") or "")}
            if attested and attested.get("quote")
            else None
        ),
    }


#: MD-Q5 (2): the recorded denial ids the turn answering a scope cluster's
#: question wrote (``gap_coverage.record_turn_outcome(recorded_limits=…)``).
RECORDED_LIMITS_KEY = "recorded_limits"

#: WP-Q6 (b) (ADR-075 amended 2026-11-15): the person's own answers to a scope
#: cluster's question, verbatim (``record_turn_outcome(scope_answer=…)``) — their
#: statement on THAT bar, whatever the reconciler extracted from it. Lives on the
#: gap-analysis row, never in ``profile_json``: it is testimony about one
#: requirement, not a vault fact.
SCOPE_ANSWERS_KEY = "scope_answers"
#: The id a scope answer carries in the gap prompt's SCOPE ANSWERS block, and the
#: one the classifier copies into ``stated_limit``.
SCOPE_ANSWER_ID_PREFIX = "answer: "
#: The ``bar.recorded_limit["source"]`` of a limit bound to a stored answer.
SCOPE_ANSWER_SOURCE = "scope_answer"
#: One bar's stored answers are joined up to this many characters (a scope
#: question is asked once; a follow-up adds one more answer at most).
_SCOPE_ANSWER_MAX_CHARS = 800


def scope_answer_id(concept: Any) -> str:
    return f"{SCOPE_ANSWER_ID_PREFIX}{' '.join(str(concept or '').split())}"


def turn_scope_answers(clusters: list[Any] | None) -> dict[str, str]:
    """Per scope concept (casefolded, whitespace-joined), the person's stored
    answer(s) to that bar's scope question, joined — the structural half of the
    answer binding (``scope_requirements.verified_stated_limit(answers=…)``).
    Keyed like :func:`turn_bound_limits` (every member, the label)."""
    out: dict[str, str] = {}
    for cluster in clusters or []:
        if not is_scope_cluster(cluster):
            continue
        answers = [
            " ".join(a.split()) for a in cluster.get(SCOPE_ANSWERS_KEY) or []
            if isinstance(a, str) and a.strip()
        ]
        if not answers:
            continue
        statement = " ".join(dict.fromkeys(answers))[:_SCOPE_ANSWER_MAX_CHARS].strip()
        outcome = cluster.get("outcome") if isinstance(cluster.get("outcome"), dict) else {}
        for m in [*(cluster.get("gaps") or []), *(outcome.get("covered") or []),
                  *(outcome.get("declined") or []), cluster.get("label")]:
            if isinstance(m, str) and m.strip():
                out.setdefault(_norm(m), statement)
    return out


def turn_bound_limits(clusters: list[Any] | None) -> dict[str, set[str]]:
    """Per scope concept (casefolded, whitespace-joined), the denial ids recorded
    on the turn that asked that concept's scope question — the structural half of
    the binding (``scope_requirements.verified_stated_limit(asked_for=…)``)."""
    out: dict[str, set[str]] = {}
    for cluster in clusters or []:
        if not is_scope_cluster(cluster):
            continue
        ids = {str(i) for i in cluster.get(RECORDED_LIMITS_KEY) or [] if isinstance(i, str) and i.strip()}
        if not ids:
            continue
        outcome = cluster.get("outcome") if isinstance(cluster.get("outcome"), dict) else {}
        for m in [*(cluster.get("gaps") or []), *(outcome.get("covered") or []),
                  *(outcome.get("declined") or []), cluster.get("label")]:
            if isinstance(m, str) and m.strip():
                out.setdefault(_norm(m), set()).update(ids)
    return out


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
    groups = _row_groups(rows)

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
    for group in groups:
        row = group[0]
        concept = str(row["concept"]).strip()
        members = list(dict.fromkeys(str(r["concept"]).strip() for r in group))
        facts = scope_facts(row)
        # The most urgent status of the group: a gap row makes it category C.
        category = "C" if any(r.get("status") == "gap" for r in group) else "B"
        bar = row.get("bar") if isinstance(row.get("bar"), dict) else {}
        # One group per bar (M4), so one prior at most: the carry-forward keeps
        # ONE cluster id even when an earlier carve left duplicates behind.
        prior = next((existing[_norm(m)] for m in members if _norm(m) in existing), None)
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
                    "gaps": members,
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


def _figure(facts: dict[str, Any], lang: str) -> str | None:
    """The posting's figure as a phrase: "ca. 120", "mindestens 5 Mio. €", "80–120",
    "bis zu 120", "5 Mio." (no unit written → none). ``None`` without a number."""
    if _number(facts.get("value")) is None:
        return None
    budget = facts.get("kind") == "budget"
    fmt = (lambda v: _amount(v, lang)) if budget else _num
    value = fmt(facts.get("value"))
    comparator = facts.get("comparator")
    if comparator == "range" and _number(facts.get("value_max")) is not None:
        number = f"{value}–{fmt(facts['value_max'])}"
    else:
        # M5: the qualifier the posting wrote; a model "approx" with no "ca." in
        # the quote prints none; "min" (closed set, e.g. "8+") stays "mindestens".
        qualifier = facts.get("qualifier") if "qualifier" in facts else (
            comparator if comparator in ("approx", "min") else None)
        if qualifier is None and comparator == "min":
            qualifier = "min"
        number = f"{_QUALIFIER_WORDS[lang].get(qualifier or '', '')}{value}"
    code = facts.get("currency") if budget else None
    if code in _UNIT_CODE.values():
        if lang == "de":
            unit = "€" if code == "EUR" else code
            number = f"{number} {unit}"
        else:
            sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(code)
            number = _prefix_unit(number, sym) if sym else f"{code} {number}"
    return number


def _prefix_unit(number: str, sym: str) -> str:
    """EN: the symbol goes before the first digit ("about €5M", "€2M–€5M")."""
    return re.sub(r"(\d[\d.,]*\w*)", lambda m: f"{sym}{m.group(1)}", number)


def _end(phrase: str) -> str:
    """Close a sentence on a phrase that may already end in an abbreviation dot."""
    return phrase if phrase.endswith(".") else f"{phrase}."


def _entry(label: str) -> str:
    return label.replace(" @ ", ", ").strip()


def _value_number(value: Any) -> float | None:
    """A profile value as a number: an int team size, or a stored budget string
    read by the Oracle's figure reader ("ca. 6 Mio. EUR" → 6000000)."""
    number = _number(value)
    if number is not None:
        return number
    if isinstance(value, str) and value.strip():
        from applire.services.oracle.matchers.figures import extract_figures

        for f in extract_figures(value):
            if f.kind in ("number", "currency"):
                try:
                    return float(f.value)
                except ValueError:
                    continue
    return None


def _sort_key(v: dict[str, Any]) -> float:
    """L2 (MD-Q3): largest first, string budgets included; unreadable last."""
    number = _value_number(v.get("value"))
    return -number if number is not None else float("inf")


def _shown_value(value: Any, facts: dict[str, Any], lang: str) -> str:
    """L2: a bare-digit budget is shown in the question's own amount form
    ("800 Tsd."), never with a unit it does not carry; anything else verbatim."""
    if facts.get("kind") == "budget":
        number = _number(value)
        if number is None and isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
            number = float(value.strip())
        if number is not None:
            return _amount(number, lang)
    return str(value)


def _profile_line(facts: dict[str, Any], lang: str) -> tuple[str, bool]:
    """What the profile holds so far, and whether it holds anything."""
    values = sorted(facts.get("candidate_values") or [], key=_sort_key)[:2]
    if values:
        parts = [
            f"{_shown_value(v['value'], facts, lang)} ({_entry(v['entry'])})" if v.get("entry")
            else _shown_value(v["value"], facts, lang)
            for v in values
        ]
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


def current_scope_facts(
    facts: dict[str, Any], profile_json: dict[str, Any] | None, quote: Any = None
) -> dict[str, Any]:
    """The cluster's facts with the PROFILE side read from today's vault (MD-Q3
    M3): the typed values from ``collect_candidate_values``, the attested quote
    kept only while it still resolves in the vault. The posting side is the
    stored bar (it does not change between analysis and interview); a cluster
    persisted before M5 gets its unit/qualifier read from its quote here."""
    from applire.services.scope_requirements import (
        collect_candidate_values,
        verify_attested_evidence,
    )

    out = dict(facts)
    if "qualifier" not in out and quote:
        figure = _figure_facts(out.get("kind"), out.get("value"), quote)
        out["currency"], out["qualifier"] = figure["currency"], figure["qualifier"]
    if profile_json is None or out.get("kind") not in ("team_size", "budget"):
        return out
    out["candidate_values"] = [
        {"value": v.get("value"), "entry": str(v.get("entry") or "")}
        for v in collect_candidate_values(profile_json, out["kind"])
        if v.get("value") not in (None, "")
    ]
    attested = out.get("attested")
    if isinstance(attested, dict) and attested.get("quote"):
        still = verify_attested_evidence(
            {"quote": attested["quote"], "unit": "held", "entry": attested.get("entry")},
            profile_json,
        )
        out["attested"] = (
            {"quote": str(still.get("quote") or ""), "entry": str(still.get("entry") or "")}
            if still else None
        )
    return out


def scope_question(
    cluster: dict[str, Any], lang: str = "en", profile_json: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The scope cluster's one question, built from its facts. ``choices`` is
    ``None``: a scope chip would be a first-person claim about a span.

    ``profile_json`` (MD-Q3 M3): the vault as it is NOW — the question's profile
    figures are read from it, never from the analysis-time snapshot."""
    lang = "de" if str(lang or "").lower().startswith("de") else "en"
    facts = cluster.get("scope") if isinstance(cluster.get("scope"), dict) else {}
    facts = current_scope_facts(facts, profile_json, cluster.get("jd_context"))
    budget = facts.get("kind") == "budget"
    figure = _figure(facts, lang)
    held, has_value = _profile_line(facts, lang)
    if figure is None:
        # L3 (MD-Q3): no number on the bar — no figure is printed, none invented.
        if lang == "de":
            opening = ("Die Stelle nennt eine Vorgabe zur Budgetverantwortung." if budget
                       else "Die Stelle nennt eine Vorgabe zur Führungsspanne.")
            ask = ("Welches Budget hast du bisher selbst verantwortet" if budget
                   else "Wie viele Personen hast du bisher selbst geführt oder verantwortet")
            question = (f"{opening} {held} {ask} – dauerhaft oder vertretungsweise – und wie "
                        "ordnest du das gegenüber der Vorgabe der Stelle ein?")
        else:
            opening = ("The posting sets a budget responsibility." if budget
                       else "The posting sets a team scope.")
            ask = ("What budget have you been responsible for yourself so far" if budget
                   else "How many people have you led or been responsible for yourself so far")
            question = (f"{opening} {held} {ask} – permanently or as a deputy – and how does "
                        "that compare with the posting?")
        return {"question": question, "choices": None}
    if lang == "de":
        opening = (f"Die Stelle nennt eine Budgetverantwortung von {_end(figure)}" if budget
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
        opening = (f"The posting names a budget responsibility of {_end(figure)}" if budget
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


def leadership_kind_entries(profile_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Q1's predicate (``services/profile/role_facts.py``) — imported directly, so a
    broken contract after the merge fails loudly; its shape is pinned by a test."""
    from applire.services.profile.role_facts import entries_to_ask_leadership

    # Q3-2 + Q8-2 (Sorbet wave 2b): the sized roles (confirm the stored size)
    # and the roles whose own text says they lead people but hold no size (ask it).
    return list(entries_to_ask_leadership(profile_json) or [])


def is_leadership_kind_cluster(cluster: Any) -> bool:
    return isinstance(cluster, dict) and cluster.get("kind") == LEADERSHIP_KIND


def is_fact_built_cluster(cluster: Any) -> bool:
    """A cluster whose one question is built from facts and asked once — the turn
    always advances (scope cluster, leadership-kind ask, Q8-6 size follow-up)."""
    return (
        is_scope_cluster(cluster) or is_leadership_kind_cluster(cluster)
        or is_size_bullet_cluster(cluster) or is_bullet_replace_cluster(cluster)
    )


# ---------------------------------------------------------------------------
# Q8-6 (founder ruling, Sorbet wave 2b) — after a corrected team size, one
# follow-up per bullet of the role that still names the old number
# (``services/profile/size_correction.py``). Queued right after the kind ask,
# fixed closed answers, never drafted.
# ---------------------------------------------------------------------------

SIZE_BULLET = "size_bullet"
SIZE_BULLET_ID = "size-bullet"
#: Session-state key: the follow-ups already queued (``size_bullet_key``).
SIZE_BULLET_ASKED_STATE_KEY = "size_bullet_asked"


def is_size_bullet_cluster(cluster: Any) -> bool:
    return isinstance(cluster, dict) and cluster.get("kind") == SIZE_BULLET


def size_bullet_key(item: dict[str, Any]) -> str:
    return f"{item.get('entry_id')}|{item.get('new')}|{item.get('text')}"


def size_bullet_choices(lang: str, new: Any) -> list[str]:
    """The two closed answers, in this order: change, keep."""
    if _lang(lang) == "de":
        return [f"Auf {new} ändern", "So lassen"]
    return [f"Change to {new}", "Leave as is"]


def size_bullet_question(cluster: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    """"Du hast die Teamgröße auf 12 korrigiert. Dein Punkt nennt noch 14: »…«"."""
    item = cluster.get("entry") or {}
    text = " ".join(str(item.get("text") or "").split())
    if _lang(lang) == "de":
        question = (f"Du hast die Teamgröße auf {item.get('new')} korrigiert. "
                    f"Dein Punkt nennt noch {item.get('old')}: »{text}«")
    else:
        question = (f"You corrected the team size to {item.get('new')}. "
                    f"Your bullet still says {item.get('old')}: “{text}”")
    return {"question": question, "choices": size_bullet_choices(lang, item.get("new"))}


def size_bullet_decision(cluster: Any, message: str) -> str | None:
    """``change`` / ``keep`` for the EXACT closed answer (either language, case,
    spacing and a closing full stop ignored); ``None`` for anything else —
    never interpreted (the record stays open; the CV keeps the line withheld)."""
    if not is_size_bullet_cluster(cluster):
        return None
    new = (cluster.get("entry") or {}).get("new")
    norm = " ".join(str(message or "").split()).rstrip(".").casefold()
    for lang in ("de", "en"):
        change, keep = (c.casefold() for c in size_bullet_choices(lang, new))
        if norm == change:
            return "change"
        if norm == keep:
            return "keep"
    return None


def queue_size_bullet_asks(state: dict[str, Any], profile_json: Any, current_idx: int) -> int:
    """Insert one follow-up per open size-correction record right after the
    current cluster (never at an index already passed), each once per session;
    one question added to the budget per ask. Returns how many were queued."""
    from applire.services.profile.size_correction import open_corrections

    asked = set(state.get(SIZE_BULLET_ASKED_STATE_KEY) or [])
    items = [i for i in open_corrections(profile_json) if size_bullet_key(i) not in asked]
    if not items:
        return 0
    ids = list(state.get("critical_gaps") or [])
    by_id = dict(state.get("gap_clusters_by_id") or {})
    cats = dict(state.get("gap_categories") or {})
    new_ids = []
    for item in items:
        n = 1
        while f"{SIZE_BULLET_ID}-{n}" in by_id:
            n += 1
        cid = f"{SIZE_BULLET_ID}-{n}"
        by_id[cid] = {"id": cid, "kind": SIZE_BULLET, "label": cid, "category": "B",
                      "gaps": [], "jd_skills": [], "jd_context": "", "entries": [item], "entry": item}
        cats[cid] = "B"
        new_ids.append(cid)
    at = min(max(current_idx + 1, 0), len(ids))
    state["critical_gaps"] = [*ids[:at], *new_ids, *ids[at:]]
    state["gap_clusters_by_id"] = by_id
    state["gap_categories"] = cats
    state["hard_ceiling"] = int(state.get("hard_ceiling") or 0) + len(new_ids)
    state[SIZE_BULLET_ASKED_STATE_KEY] = [*asked, *(size_bullet_key(i) for i in items)]
    return len(new_ids)


# ---------------------------------------------------------------------------
# Sorbet invite-gate WP-V (rulings Q9-6, Q9-7; FOUNDER-QUESTION V-1, mock
# ``Runs/Sorbet/invite-gate/v/mock/replace-confirm.html`` option A) — a new
# bullet that rewords a held one with more detail is put to the person as ONE
# closed question, queued right after the current cluster like the Q8-6 size
# follow-up: "Ersetzen" writes the replace (with the "Ersetzt" line and its
# undo), "Beide behalten" keeps both; any other text is reconciled like any
# answer and both bullets stay. The proposal comes from the bullet-replace loop
# (``services/profile/replace_loop``); this module never drafts.
# ---------------------------------------------------------------------------

BULLET_REPLACE = "bullet_replace"
BULLET_REPLACE_ID = "bullet-replace"
#: Session-state key: the replace questions already queued (``bullet_replace_key``).
BULLET_REPLACE_ASKED_STATE_KEY = "bullet_replace_asked"


def is_bullet_replace_cluster(cluster: Any) -> bool:
    return isinstance(cluster, dict) and cluster.get("kind") == BULLET_REPLACE


def bullet_replace_key(item: dict[str, Any]) -> str:
    return f"{item.get('entry_id')}|{item.get('held')}|{item.get('new')}"


def bullet_replace_choices(lang: str) -> list[str]:
    """The two closed answers, in this order: replace, keep both."""
    if _lang(lang) == "de":
        return ["Ersetzen", "Beide behalten"]
    return ["Replace", "Keep both"]


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


#: F-1 (adv/vault, ruling V-1) — the line put above the replace question when
#: it comes back once after an answer that was not one of its two chips. The
#: shape of the confirmation re-ask (``confirmations.UNMATCHED_ANSWER_HINT``,
#: #730); "replaced" instead of "changed", because the same message may have
#: stored a new fact. Visible copy: FOUNDER-QUESTION V-F1.
BULLET_REPLACE_UNMATCHED_HINT: dict[str, str] = {
    "de": ("Deine Antwort passt zu keiner der beiden Optionen, deshalb wurde nichts "
           "ersetzt – beide Punkte bleiben. Bitte wähle eine davon."),
    "en": ("Your answer doesn't match either option, so nothing was replaced – "
           "both points stay. Please pick one of them."),
}

#: F-2 (adv/vault) — the loop paths whose proposal is offered: the check found
#: nothing, or the reviewer confirmed it with verified quotes. An ``unresolved``
#: proposal (the check still finds the new bullet dropping or changing a part of
#: the held one) is never offered as "only more precise": both bullets stay.
OFFERED_REPLACE_PATHS = frozenset({"check", "reviewer"})


def bullet_replace_question(cluster: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    item = cluster.get("entry") or {}
    held, proposal = _one_line(item.get("held")), _one_line(item.get("proposal"))
    out = _bullet_replace_question(held, proposal, lang)
    if cluster.get("reask"):
        out["question"] = f"{BULLET_REPLACE_UNMATCHED_HINT[_lang(lang)]}\n\n{out['question']}"
    return out


def _bullet_replace_question(held: str, proposal: str, lang: str) -> dict[str, Any]:
    if _lang(lang) == "de":
        question = (
            "Das klingt wie ein Punkt, der schon in deinem Profil steht – nur genauer. "
            f"Soll der neue Punkt den bisherigen ersetzen? Bisher: »{held}« Neu: »{proposal}«"
        )
    else:
        question = (
            "That sounds like a point already in your profile, only more precise. "
            f"Should the new point replace the current one? Current: “{held}” New: “{proposal}”"
        )
    return {"question": question, "choices": bullet_replace_choices(lang)}


#: Characters a closed answer may carry around the chip without changing it:
#: trailing punctuation, and quote marks around the whole chip. NF-5 (adv/vault
#: re-probe): never "?" — "Ersetzen?" asks whether to replace, it does not
#: choose to (V-1 wants a closed answer; a question gets the one re-ask).
_CHIP_TRAIL = ".!…"
_CHIP_QUOTES = "\"'„“”‚‘’»«"


def closed_answer_norm(message: str) -> str:
    """F-1 (adv/vault) — what is STRUCTURALLY certain about a closed answer: the
    chip itself, whatever its case, spacing, closing punctuation or surrounding
    quote marks. Nothing else is read — never a word list over free prose
    ("ja", "Ersetzen bitte" are not the chip; wave-1a lesson). A question mark
    anywhere makes it a question, not the chip (NF-5): returned unchanged
    apart from case and spacing, so it matches no chip."""
    norm = " ".join(str(message or "").split())
    if "?" in norm:
        return norm.casefold()
    prev = None
    while norm != prev:
        prev = norm
        norm = norm.rstrip(_CHIP_TRAIL).strip().strip(_CHIP_QUOTES).strip()
    return norm.casefold()


def bullet_replace_decision(cluster: Any, message: str) -> str | None:
    """``replace`` / ``keep`` for the closed answer (either language; case,
    spacing, closing punctuation and surrounding quotes ignored —
    :func:`closed_answer_norm`); ``None`` for anything else."""
    if not is_bullet_replace_cluster(cluster):
        return None
    norm = closed_answer_norm(message)
    for lang in ("de", "en"):
        replace, keep = (c.casefold() for c in bullet_replace_choices(lang))
        if norm == replace:
            return "replace"
        if norm == keep:
            return "keep"
    return None


def queue_bullet_replace_asks(
    state: dict[str, Any], proposals: list[dict[str, Any]] | None, current_idx: int
) -> int:
    """One question per proposal right after the current cluster, each once per
    session; one question added to the budget per ask. Returns how many."""
    asked = set(state.get(BULLET_REPLACE_ASKED_STATE_KEY) or [])
    items, seen = [], set()
    for p in proposals or []:
        if not isinstance(p, dict) or p.get("path") not in OFFERED_REPLACE_PATHS:
            continue  # F-2: an unresolved proposal is not offered — both stay
        key = bullet_replace_key(p)
        if key not in asked and key not in seen:
            seen.add(key)
            items.append(p)
    if not items:
        return 0
    ids = list(state.get("critical_gaps") or [])
    by_id = dict(state.get("gap_clusters_by_id") or {})
    cats = dict(state.get("gap_categories") or {})
    new_ids = []
    for item in items:
        n = 1
        while f"{BULLET_REPLACE_ID}-{n}" in by_id:
            n += 1
        cid = f"{BULLET_REPLACE_ID}-{n}"
        by_id[cid] = {"id": cid, "kind": BULLET_REPLACE, "label": cid, "category": "B",
                      "gaps": [], "jd_skills": [], "jd_context": "", "entries": [item], "entry": item}
        cats[cid] = "B"
        new_ids.append(cid)
    at = min(max(current_idx + 1, 0), len(ids))
    state["critical_gaps"] = [*ids[:at], *new_ids, *ids[at:]]
    state["gap_clusters_by_id"] = by_id
    state["gap_categories"] = cats
    state["hard_ceiling"] = int(state.get("hard_ceiling") or 0) + len(new_ids)
    state[BULLET_REPLACE_ASKED_STATE_KEY] = [*asked, *(bullet_replace_key(i) for i in items)]
    return len(new_ids)


def queue_bullet_replace_reask(state: dict[str, Any], cluster: Any, current_idx: int) -> bool:
    """F-1 (adv/vault; ADR-099 "ask once", precedent #730) — an answer to the
    replace question that is not one of its chips replaces nothing; the same
    question comes back ONCE, right after the current cluster, with the line
    that nothing was replaced (:data:`BULLET_REPLACE_UNMATCHED_HINT`). An
    answer to that re-ask that is again not a chip settles it: both stay.
    Returns whether a re-ask was queued."""
    if not is_bullet_replace_cluster(cluster) or cluster.get("reask"):
        return False
    ids = list(state.get("critical_gaps") or [])
    by_id = dict(state.get("gap_clusters_by_id") or {})
    cats = dict(state.get("gap_categories") or {})
    n = 1
    while f"{BULLET_REPLACE_ID}-{n}" in by_id:
        n += 1
    cid = f"{BULLET_REPLACE_ID}-{n}"
    by_id[cid] = {**cluster, "id": cid, "label": cid, "reask": True}
    cats[cid] = "B"
    at = min(max(current_idx + 1, 0), len(ids))
    state["critical_gaps"] = [*ids[:at], cid, *ids[at:]]
    state["gap_clusters_by_id"] = by_id
    state["gap_categories"] = cats
    state["hard_ceiling"] = int(state.get("hard_ceiling") or 0) + 1
    state[BULLET_REPLACE_REASK_STATE_KEY] = cid
    return True


#: The re-ask this turn queued; :func:`keep_reask_next` keeps it the very next
#: question when a later queue of the same turn (Q8-6 size follow-ups) inserts
#: right after the current cluster too.
BULLET_REPLACE_REASK_STATE_KEY = "bullet_replace_reask"


def keep_reask_next(state: dict[str, Any], current_idx: int) -> None:
    """F-1 — the re-ask answers THIS turn ("nothing was replaced"): it is the
    next question, ahead of anything queued after it in the same turn."""
    cid = state.pop(BULLET_REPLACE_REASK_STATE_KEY, None)
    ids = list(state.get("critical_gaps") or [])
    if not cid or cid not in ids or ids.index(cid) <= current_idx:
        return  # gone, or already asked: never brought back
    ids.remove(cid)
    at = min(max(current_idx + 1, 0), len(ids))
    state["critical_gaps"] = [*ids[:at], cid, *ids[at:]]


#: L5 (MD-Q3): the session-state key that records, per answered kind ask, the
#: roles it covered (:func:`kind_entry_key`). Read across the person's sessions.
KIND_ASKED_STATE_KEY = "leadership_kind_asked"


def kind_entry_key(entry: dict[str, Any]) -> str:
    """A role's identity for "already asked": its entry id, or — for an entry
    stored without one — its company and role."""
    eid = str(entry.get("entry_id") or entry.get("id") or "").strip()
    # Q8-1: the ask an entry merge's conflict re-opens has a key of its own.
    reask = str(entry.get("reask") or "").strip()
    suffix = f"@{reask}" if reask else ""
    if eid:
        return f"id:{eid}{suffix}"
    return "cr:" + _norm(entry.get("company")) + "|" + _norm(entry.get("role")) + suffix


def plan_leadership_kind(
    cluster_ids: list[str],
    categories: dict[str, str],
    clusters_by_id: dict[str, dict[str, Any]],
    profile_json: dict[str, Any] | None,
    *,
    already_asked: Any = (),
) -> tuple[list[str], dict[str, str], dict[str, dict[str, Any]]]:
    """Insert the leadership-kind ask into a full interview plan (pure).

    For EVERY role ``entries_needing_leadership_kind`` lists, whatever the posting
    (founder ruling Q-2: when the kind is unknown, the interview asks once —
    delivery re-run 2026-11-15: Katrin's posting carried no team-size scope
    cluster and no leadership emphasis, so she was never asked and her CV
    printed the neutral line). Right after the team-size scope cluster when the
    plan holds one; otherwise at the end. Nothing when no listed role needs the
    kind, or the plan already holds the ask. A Gap-Click micro-session never
    calls this. The cost is bounded: once per person and role, ever.

    ``already_asked`` (L5, MD-Q3): the :func:`kind_entry_key` of every role an
    earlier session of this person already put the ask to — such a role is not
    asked again, even when its answer left the kind unknown ("beides"); the line
    stays neutral."""
    if any(is_leadership_kind_cluster(clusters_by_id.get(cid)) for cid in cluster_ids):
        return cluster_ids, categories, clusters_by_id
    asked = {str(k) for k in already_asked or () if k}
    entries = [
        e for e in leadership_kind_entries(profile_json)
        if isinstance(e, dict) and kind_entry_key(e) not in asked
    ][:_KIND_MAX_ROLES]
    if not entries:
        return cluster_ids, categories, clusters_by_id
    anchor = next(
        (i for i, cid in enumerate(cluster_ids)
         if is_scope_cluster(clusters_by_id.get(cid))
         and ((clusters_by_id.get(cid) or {}).get("scope") or {}).get("kind") == "team_size"),
        None,
    )
    # MD-Q5 (1): ONE question per role — fixed options cannot answer two roles.
    new_ids: list[str] = []
    new_clusters: dict[str, dict[str, Any]] = {}
    for n, entry in enumerate(entries, start=1):
        cid = LEADERSHIP_KIND_ID if n == 1 else f"{LEADERSHIP_KIND_ID}-{n}"
        new_ids.append(cid)
        new_clusters[cid] = {
            "id": cid, "kind": LEADERSHIP_KIND, "label": cid, "category": "B",
            "gaps": [], "jd_skills": [], "jd_context": "", "entries": [entry], "entry": entry,
        }
    at = len(cluster_ids) if anchor is None else anchor + 1
    ids = [*cluster_ids[:at], *new_ids, *cluster_ids[at:]]
    return (ids, {**categories, **{cid: "B" for cid in new_ids}},
            {**clusters_by_id, **new_clusters})


#: MD-Q5 (1): the fixed options, in this order; the labels are the Q1 module's.
KIND_CHOICE_OPTIONS = ("disciplinary", "functional", "both")


def _lang(lang: str) -> str:
    return "de" if str(lang or "").lower().startswith("de") else "en"


def kind_choices(lang: str, team_size: Any = "unset") -> list[str]:
    """The chips. Q3-2 / Q8-2 (Sorbet wave 2b): each option label carries the
    size part (``leadership_kind.size_suffix``) — the stored size to confirm,
    editable before sending, or an open "Anzahl Personen:" the person completes.
    Without a ``team_size`` argument: the bare labels (an older caller)."""
    from applire.services.profile.leadership_kind import OPTION_LABELS, size_suffix

    labels = OPTION_LABELS[_lang(lang)]
    if team_size == "unset":
        return [labels[o] for o in KIND_CHOICE_OPTIONS]
    return [labels[o] + size_suffix(_lang(lang), team_size) for o in KIND_CHOICE_OPTIONS]


def kind_cluster_entry(cluster: dict[str, Any]) -> dict[str, Any]:
    entry = cluster.get("entry")
    if isinstance(entry, dict):
        return entry
    entries = [e for e in cluster.get("entries") or [] if isinstance(e, dict)]
    return entries[0] if entries else {}


def _people(n: int, lang: str) -> str:
    if lang == "de":
        return f"{n} Person" if n == 1 else f"{n} Personen"
    return f"{n} person" if n == 1 else f"{n} people"


def _current_size(entry: dict[str, Any], profile_json: Any) -> Any:
    """The role's team size as the profile holds it NOW (Katrin, delivery run
    on 82154267: the ask was planned unsized at session start, her scope answer
    stored 3 before the ask was shown; the shape follows the stored size)."""
    if not isinstance(profile_json, dict):
        return entry.get("team_size")
    eid = str(entry.get("entry_id") or "")
    for e in profile_json.get("work_experience") or []:
        if isinstance(e, dict) and eid and str(e.get("id") or "") == eid:
            return e.get("team_size")
    return entry.get("team_size")


def leadership_kind_question(
    cluster: dict[str, Any], lang: str = "en", profile_json: Any = None,
) -> dict[str, Any]:
    """The ask for ONE role (MD-Q5 (1)), with the fixed options as chips. When the
    role's entry carries the person's own bullet about leading (``said``), the
    question quotes it; otherwise it names the role.

    Q3-2 (founder, wave 2b): a stored team size is confirmed in the same
    question ("Im Profil stehen N Personen – stimmt das?"); each chip carries
    the number, which the person may change before sending. Q8-2: a role whose
    text says it leads people but holds no size is asked the number too; the
    chips end in "Anzahl Personen:" and the person types it."""
    lang = _lang(lang)
    entry = kind_cluster_entry(cluster)
    said = " ".join(str(entry.get("said") or "").split())
    where = str(entry.get("company") or entry.get("role") or "").strip() or "—"
    size = _current_size(entry, profile_json)
    sized = isinstance(size, int) and not isinstance(size, bool) and size > 0
    if lang == "de":
        kind = "War das disziplinarische Führung (Personalverantwortung) oder fachliche Anleitung?"
        quote = f"Du schreibst: »{said}«" if said else ""
        stop = "" if said.endswith((".", "!", "?")) else "."
        if sized:
            confirm = f"Im Profil stehen {_people(size, lang)} – stimmt das?"
            lead = f"{quote}{stop} {confirm}" if said else f"Bei {where} stehen im Profil {_people(size, lang)} – stimmt das?"
            question = f"{lead} {kind} Wenn die Zahl nicht stimmt, ändere sie in deiner Antwort."
        else:
            # RULING Q8-4 (founder wording correction).
            ask = ("Wie viele Personen waren das, und war es disziplinarische Führung "
                   "(Personalverantwortung) oder fachliche Anleitung?")
            question = (f"{quote}{stop} {ask}" if said else
                        f"Bei {where}: Wie viele Personen hast du geführt oder angeleitet, und war das "
                        "disziplinarische Führung (Personalverantwortung) oder fachliche Anleitung?")
    else:
        kind = "Was that line management (disciplinary) or functional guidance?"
        quote = f"You write: “{said}”" if said else ""
        stop = "" if said.endswith((".", "!", "?")) else "."
        if sized:
            confirm = f"Your profile shows {_people(size, lang)} – is that right?"
            lead = f"{quote}{stop} {confirm}" if said else f"At {where} your profile shows {_people(size, lang)} – is that right?"
            question = f"{lead} {kind} If the number is wrong, change it in your answer."
        else:
            ask = ("How many people was that, and was it line management (disciplinary) "
                   "or functional guidance?")
            question = (f"{quote}{stop} {ask}" if said else
                        f"At {where}: how many people did you lead or guide, and was that line "
                        "management (disciplinary) or functional guidance?")
    return {"question": question, "choices": kind_choices(lang, size if sized else None)}
