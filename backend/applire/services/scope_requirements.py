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
    # #562 (re-PQ 2026-08-28): the vault records the size of the team the
    # candidate LED in that role — the extraction/reconcile rule counts a team
    # they were responsible for, functional OR disciplinary — and nothing in
    # the vault says which. The previous wording ("direct reports") told the
    # writer more than the data holds: a dossier stating "fachlich ein Team
    # von 7 Personen" (and "Disziplinarische Führung hatte ich noch nicht")
    # became "meine direkte Führungsspanne umfasst sieben Personen" in the
    # delivered letter (gpt-5.6-luna, n=1). Say exactly what the vault knows.
    "team_size": (
        "size of the team the candidate led in that one role — functional or "
        "disciplinary leadership, the vault does not record which; never the "
        "organisation's total headcount, and not to be upgraded to 'direct reports'"
    ),
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


def _projection_facets(entry: dict[str, Any], field: str) -> dict[str, Any]:
    """#328 option 4 / #382 — the provenance facets of one typed value.

    Reports ``provenance`` (``derived`` = this entry's OWN bullet states the
    figure; ``uncorroborated`` = real testimony no bullet of this entry
    repeats) and, for a budget, the ``unit`` the corroborating bullet carries —
    without which "6000000" cannot be told from six million forints, which is
    the ambiguity #382 filed.

    It deliberately does NOT report the projection's ``quote``. ADR-070's
    attestation is the one quote-bearing scope channel: it is MODEL-cited,
    fail-closed verified (:func:`verify_attested_evidence`), and it alone may
    lift a row's status. Giving a code-derived value a quote channel in the
    same block would make the two indistinguishable to the judge — so the
    distinction is structural here, not a label a later prompt edit can blur.
    """
    projection = (entry.get("role_fact_projections") or {}).get(field)
    if not isinstance(projection, dict):
        return {"provenance": "uncorroborated", "unit": None}
    return {
        "provenance": (
            "derived" if projection.get("provenance") == "derived" else "uncorroborated"
        ),
        "unit": projection.get("unit"),
    }


def collect_candidate_values(
    profile_json: dict[str, Any] | None, kind: str
) -> list[dict[str, Any]]:
    """The candidate's typed vault values for one scope kind, verbatim, each
    carrying its entry label, its field semantics (#328's fields, finally
    confronted with the JD side) and its projection provenance (#328 option 4 —
    the typed value projects the candidate's own bullet, and says so)."""
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
                        **_projection_facets(entry, "team_size"),
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
                        **_projection_facets(entry, "budget_managed"),
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
    *,
    denials_taken: Any = (),
    turn_limits: dict[str, Any] | None = None,
    scope_answers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Turn the model's scope judgements into ledger entries, floor applied.

    ``scope_answers`` (WP-Q6 (b)): per scope concept (``_id_key``), the person's
    stored answer to that bar's scope question (``turn_scope_answers``); a
    ``stated_limit`` citing it binds via :func:`scope_answer_limit`.

    ``turn_limits`` (MD-Q5 (2)): per scope concept (``_id_key``), the recorded
    denial ids the turn answering that concept's scope question wrote.

    ``denials_taken``: the ``denied_by`` ids the SAME response cited for
    REQUIREMENTS rows (see :func:`verified_stated_limit`).

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

        bar_facts = {
            "kind": item.get("kind"),
            "value": item.get("jd_value"),
            "value_max": item.get("jd_value_max"),
            "concept": concept,
        }
        recorded_limit = scope_answer_limit(
            cls.get("stated_limit"), cls.get("stated_limit_quote"), concept, scope_answers,
            bar=bar_facts,
        ) or verified_stated_limit(
            cls.get("stated_limit"),
            profile_json,
            taken=denials_taken,
            bar=bar_facts,
            asked_for=(turn_limits or {}).get(_id_key(concept), ()),
        )
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
                    # ADR-070 cl. 2 / ADR-075 amended 2026-11-14 (WP-Q2 e): the
                    # candidate's OWN recorded statement of falling short of this
                    # bar, bound by the classifier's id and verified here. Only
                    # present when bound — a row without it reads as before.
                    **({"recorded_limit": recorded_limit} if recorded_limit else {}),
                },
            }
        )
    return entries


def _id_key(value: str) -> str:
    return " ".join(value.casefold().split())


#: MD-Q5 (N5, 2026-11-14) — the unit a figure must carry to be a bar's figure.
#: A currency written on, or right next to, the figure (shared with the scope
#: question's unit reader, M5) and, for a team scope, a people word right after it.
_UNIT_TOKEN = r"(EUR|CHF|USD|GBP|Euro|Eur|Franken|Dollar|Pfund|€|\$|£)"
_UNIT_IN_RE = re.compile(rf"(?<![A-Za-zÄÖÜäöüß]){_UNIT_TOKEN}(?![A-Za-zÄÖÜäöüß])")
_UNIT_BEFORE_RE = re.compile(rf"(?<![A-Za-zÄÖÜäöüß]){_UNIT_TOKEN}\s*$")
_UNIT_AFTER_RE = re.compile(rf"^\s*(?:Schweizer\s+|US-?\s*)?{_UNIT_TOKEN}(?![A-Za-zÄÖÜäöüß])")
_UNIT_CODE = {
    "€": "EUR", "EUR": "EUR", "Euro": "EUR", "Eur": "EUR", "CHF": "CHF", "Franken": "CHF",
    "$": "USD", "USD": "USD", "Dollar": "USD", "£": "GBP", "GBP": "GBP", "Pfund": "GBP",
}
_PEOPLE_AFTER_RE = re.compile(
    r"^\s*(?:mitarbeitende\w*|mitarbeiter\w*|ma\b|personen\b|köpfe\w*|leute\b|"
    r"beschäftigte\w*|menschen\b|fte\b|people\b|employees\b|staff\b|direct reports\b|"
    r"reports\b|engineers?\b|ingenieur\w*|entwickler\w*)",
    re.IGNORECASE,
)


def figure_currencies(text: str, raw: str, start: int) -> set[str]:
    """The currency codes written on a figure (``raw`` at ``start`` in ``text``)
    or right next to it — never elsewhere in the text (M5)."""
    end = start + len(raw)
    units = {_UNIT_CODE[m.group(1)] for m in _UNIT_IN_RE.finditer(raw)}
    before = _UNIT_BEFORE_RE.search(text[max(0, start - 6):start])
    after = _UNIT_AFTER_RE.match(text[end:end + 24])
    return units | {_UNIT_CODE[m.group(1)] for m in (before, after) if m}


def _bar_figures(bar: dict[str, Any]) -> set[str]:
    """The bar's own figures as the Oracle canonicalises them ("120", "5000000")."""
    out: set[str] = set()
    for key in ("value", "value_max"):
        number = _bar_number(bar.get(key))
        if number is not None and float(number).is_integer():
            out.add(str(int(number)))
    return out


def _bar_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


_SMALL_FIGURE_RE = re.compile(
    r"(?<![\w.,])([1-9])(?![\w]|[.,]\d|\s*(?:mio|mrd|million|milliard|tsd|tausend|k\b|m\b|bn|%))",
    re.IGNORECASE,
)


def small_figures(text: str) -> list[Any]:
    """The single-digit whole numbers of ``text`` ("(2 Personen)"). The Oracle's
    figure reader skips bare single digits on purpose (they are rarely claims), but
    a team-size bar CAN be 2 people — wave-1b delivery: Katrin's "Führungsspanne 2
    MA" read as unverified and was never asked. Used only beside ``extract_figures``
    for a TEAM-SIZE bar's own figure, never by the Oracle's claim check; a digit
    with a magnitude ("5 Mio.") is never a small figure."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(raw=m.group(1), kind="number", value=m.group(1))
        for m in _SMALL_FIGURE_RE.finditer(text or "")
    ]


_GROUPED_BEFORE_RE = re.compile(r"\d[.,]$")
_GROUPED_AFTER_RE = re.compile(r"[.,]\d")


def _bar_figure_hits(text: str, bar: dict[str, Any] | None):
    """(figure, start) for each figure in ``text`` equal to one of the bar's own."""
    if not isinstance(bar, dict) or not isinstance(text, str) or not text.strip():
        return
    from applire.services.oracle.matchers.figures import extract_figures

    wanted = _bar_figures(bar)
    if not wanted:
        return
    extra = small_figures(text) if bar.get("kind") == "team_size" else []
    # WP-Q6 (b): EVERY occurrence of a matching figure, in text order. The former
    # single cursor skipped the first "120 Mitarbeitenden" of a limit whose
    # statement repeats the bare "120" later (Luna E2E run 3, 2026-11-15: the
    # concept "dauerhafte Führung von 120 Mitarbeitenden" + its statement "…
    # Spanne von 120 wäre …" read as naming no figure with a unit, so the
    # person's recorded limit bound nothing).
    seen: set[int] = set()
    for f in [*extract_figures(text), *extra]:
        if f.kind not in ("number", "currency") or f.value not in wanted or not f.raw:
            continue
        start = text.find(f.raw)
        while start >= 0:
            end = start + len(f.raw)
            # MD-Q11 F5: nor inside a grouped or decimal number ("3.120",
            # "1.120", "0,120", "120,5") — a separator plus digit on either side.
            standalone = not (start > 0 and text[start - 1].isdigit()) and not (
                end < len(text) and text[end].isdigit()
            ) and not _GROUPED_BEFORE_RE.search(text[:start]) and not _GROUPED_AFTER_RE.match(text[end:])
            if standalone and start not in seen:
                seen.add(start)
                yield f, start
            start = text.find(f.raw, start + 1)


def names_bar_figure(text: str, bar: dict[str, Any] | None) -> bool:
    """True when ``text`` names one of the bar's own figures (any unit)."""
    return next(_bar_figure_hits(text, bar), None) is not None


def is_about_bar_concept(concept: str, statement: str, bar: dict[str, Any] | None) -> bool:
    """MD-Q6 R3 — a denial recorded on a scope turn is the BAR's own concept when
    its concept matches the bar's scope concept by the interview's cross-generator
    matcher (``sufficiency._concept_matches_ledger_key`` — the same attribution the
    ADR-064 probe uses for "a denial of the CURRENT cluster's member"), or its
    concept/statement names the bar's own figure. Another concept denied on the
    same turn ("SAP MM kenne ich nicht") is neither."""
    if not isinstance(bar, dict):
        return False
    from applire.services.ats_audit import _norm as ats_norm
    from applire.services.interview.sufficiency import _concept_matches_ledger_key

    own = bar.get("concept")
    if isinstance(own, str) and own.strip() and _concept_matches_ledger_key(
        ats_norm(concept), ats_norm(own)
    ):
        return True
    return names_bar_figure(f"{concept} {statement}", bar)


def names_bar_figure_with_unit(text: str, bar: dict[str, Any] | None) -> bool:
    """True when ``text`` names the bar's EXACT figure with a unit of the bar's
    kind: a people word right after it for a team scope ("120 Mitarbeitende"), a
    currency on or next to it for a budget ("5 Mio. €"). A structural fact
    (ADR-062 cl. 1, MD-Q5 (2)): kind words alone never tie a statement to a bar —
    the MD-Q3 kind-word list bound "Mitarbeitergespräche habe ich noch nie
    geführt" and "Budgetplanung in SAP" (re-check N5); the bar's number in another
    unit ("120 Tickets") is no tie either."""
    kind = bar.get("kind") if isinstance(bar, dict) else None
    if kind not in ("team_size", "budget"):
        return False
    for f, start in _bar_figure_hits(text, bar):
        if kind == "budget" and (f.kind == "currency" or figure_currencies(text, f.raw, start)):
            return True
        if kind == "team_size" and _PEOPLE_AFTER_RE.match(text[start + len(f.raw):]):
            return True
    return False


#: A sentence end inside the person's own answer: terminal punctuation, then
#: whitespace, then a capital / opening quote / digit-free start. "ca. 120" and
#: "Mio. €" never end a sentence (a digit or symbol follows).
_ANSWER_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ\"„»(])")


def answer_sentences(answer: str) -> list[str]:
    """The person's answer cut into its own sentences, whitespace-normalised
    (WP-Q6 (b) / MD-Q11 F4). Pure."""
    text = " ".join(str(answer or "").split())
    return [s.strip() for s in _ANSWER_SENTENCE_END_RE.split(text) if s.strip()]


def names_bar_figure_as_scope(sentence: str, bar: dict[str, Any] | None) -> bool:
    """The sentence names the bar's exact figure AS the bar's quantity: with a
    unit of the bar's kind (:func:`names_bar_figure_with_unit`), or — team size
    only — bare, the next word being no counted noun: no word follows, or a
    lower-case one ("Spanne von 120 wäre …", "von 120 für mich …"). A capitalised
    word after it is a noun of its own ("120 Kunden") and no tie. Every captured
    shortfall sentence wrote the figure bare (wave-1a miss, delivery-1b, Q2
    after-runs: "Spanne von 120 wäre/für"), so a unit-only rule would bind none
    of them. Structural (orthography), no kind-word list."""
    if names_bar_figure_with_unit(sentence, bar):
        return True
    if not isinstance(bar, dict) or bar.get("kind") != "team_size":
        return False
    for f, start in _bar_figure_hits(sentence, bar):
        rest = sentence[start + len(f.raw):]
        nxt = re.match(r"\s*(\S)", rest)
        if nxt is None or not nxt.group(1).isalpha() or nxt.group(1).islower():
            return True
    return False


def scope_answer_limit(
    cited: Any,
    quote: Any,
    concept: Any,
    scope_answers: dict[str, str] | None,
    *,
    bar: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """WP-Q6 (b) (ADR-075 amended 2026-11-15): the shortfall sentence of the
    person's stored answer to THIS bar's scope question, as ``{"id": <bar
    concept>, "statement": <that sentence, verbatim>, "source": "scope_answer"}``,
    when the classifier cites the answer's id (``answer: <concept>``) in this
    bar's ``stated_limit`` AND quotes the sentence in ``stated_limit_quote`` —
    else ``None``.

    The ADR-100 M-2 split again: WHICH sentence states a shortfall is the
    classifier's judgement (no word list reads it — lesson 1); that the id is the
    answer given to THIS bar's question and that the quote stands verbatim in that
    answer are the facts checked here. Another bar's answer binds nothing; a quote
    the answer does not contain binds nothing.

    Earned (prompt-first, category C): with the id alone the Luna replay of the
    captured post-interview call cited the answer 5/5 also when it said the bar was
    MET or said nothing about the size (WP-Q6 report (b)); the quote makes the
    model name the sentence and gives the letter and the Oracle only that sentence.
    Pure."""
    from applire.services.interview.scope_question import SCOPE_ANSWER_SOURCE, scope_answer_id

    if not isinstance(cited, str) or not cited.strip() or not isinstance(concept, str):
        return None
    if _id_key(cited) != _id_key(scope_answer_id(concept)):
        return None
    answer = (scope_answers or {}).get(_id_key(concept))
    if not isinstance(answer, str) or not answer.strip():
        logger.info("scope_requirements: stated_limit %r cites no stored answer of this bar", cited)
        return None
    # MD-Q11 F4 (adversarial pass 2a): the quote is ONE WHOLE sentence of this
    # bar's answer (its own sentence boundaries, whitespace-normalised equality)
    # and names the bar's figure as the bar's quantity. A substring bound before:
    # "38", a fragment cut before "nicht", a stitch of a met-bar sentence and
    # "Das wäre für mich der nächste Schritt." A continuation sentence that only
    # refers back to the figure ("… rund 120. Das wäre …") is NOT accepted: no
    # captured shortfall sentence needed it (all name the figure themselves),
    # and it is exactly the stitch shape of the probe.
    sentence = " ".join(str(quote or "").split()).strip()
    if not sentence or sentence not in answer_sentences(answer):
        logger.info(
            "scope_requirements: stated_limit %r quotes no whole sentence of the stored answer — binds nothing",
            cited,
        )
        return None
    if bar is not None and not names_bar_figure_as_scope(sentence, bar):
        logger.info(
            "scope_requirements: stated_limit %r quotes a sentence without the bar's figure — binds nothing",
            cited,
        )
        return None
    return {"id": concept.strip(), "statement": sentence, "source": SCOPE_ANSWER_SOURCE}


def verified_stated_limit(
    cited: Any,
    profile_json: dict[str, Any] | None,
    *,
    taken: Any = (),
    bar: dict[str, Any] | None = None,
    asked_for: Any = (),
) -> dict[str, str] | None:
    """The live recorded denial a scope classification's ``stated_limit`` cites, as
    ``{"id", "statement"}`` — or ``None`` (ADR-070 cl. 2 / ADR-075 amended
    2026-11-14, WP-Q2 cluster e).

    The ADR-100 M-2 split: WHICH recorded statement is the candidate's own word
    on falling short of this bar is the classifier's judgement; that the cited id
    is the ``concept`` of a LIVE ``metadata.denied_concepts`` record (a withdrawn
    one lives elsewhere and binds nothing) is the fact checked here. An unknown
    id, a record without a statement, or no profile → no binding.

    Third fact (MD-Q5 (2), structural; was a kind-word list in MD-Q3): with ``bar``
    given, the record binds only when (a) it was recorded on the turn that asked
    THIS bar's scope question (``asked_for``: the ids the scope cluster's turn
    recorded, ``scope_question.turn_bound_limits``) AND is the bar's own concept
    (:func:`is_about_bar_concept`, MD-Q6 R3), or (b) it names the bar's
    exact figure with a unit of its kind (:func:`names_bar_figure_with_unit`).
    The id test alone bound "Mit SAP habe ich noch nie gearbeitet" (p6.py); the
    kind-word list then bound "Mitarbeitergespräche …" (re-check N5).

    Second fact (earned, category C): an id the same response already cited as
    ``denied_by`` for a REQUIREMENTS row (``taken``) is that requirement's
    denial and binds no scope bar. The prompt says "never cite a denial that is
    about something else"; on the 2026-11-14 Luna after-run (Stefan, rep 1, the
    person had stated no span shortfall) the classifier bound the 120-span bar to
    "eigene Investitionsplanung mit Investitionsfreigaben" while citing the same
    id as ``denied_by`` for Investitionsplanung and Investitionsverantwortung. Pure."""
    if not isinstance(cited, str) or not cited.strip():
        return None
    if _id_key(cited) in {_id_key(t) for t in taken if isinstance(t, str)}:
        logger.info(
            "scope_requirements: stated_limit %r is already a REQUIREMENTS row's "
            "denied_by — binds no scope bar",
            cited,
        )
        return None
    meta = (profile_json or {}).get("metadata") if isinstance(profile_json, dict) else None
    records = meta.get("denied_concepts") if isinstance(meta, dict) else None
    key = _id_key(cited)
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        concept = record.get("concept")
        statement = record.get("statement")
        if (
            isinstance(concept, str)
            and _id_key(concept) == key
            and isinstance(statement, str)
            and statement.strip()
        ):
            asked = {_id_key(a) for a in asked_for or () if isinstance(a, str)}
            turn_bound = key in asked and is_about_bar_concept(concept, statement, bar)
            if (
                bar is not None
                and not turn_bound
                and not names_bar_figure_with_unit(f"{concept} {statement}", bar)
            ):
                logger.info(
                    "scope_requirements: stated_limit %r is neither the %r bar's own "
                    "concept recorded on its scope question nor names its figure "
                    "with a unit — binds nothing",
                    cited,
                    bar.get("kind"),
                )
                return None
            return {"id": concept.strip(), "statement": statement.strip()}
    logger.info("scope_requirements: stated_limit %r binds no live recorded denial", cited)
    return None


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
        "Each line below is labelled exactly what it is (#562): \"typed vault value\" is the",
        "candidate's own recorded figure, NOT independently verified; only \"attested in the",
        "vault\" is a verified quote — never call a typed value \"attested\".",
        "For a CV: EVERY line below is carried in its matching role, its figure kept",
        "verbatim. An \"attested in the vault\" line is a DIFFERENT fact from the typed",
        "team size beside it (e.g. a whole site covered as deputy) — it gets its own",
        "statement in that role, never dropped because the typed value already answers",
        "the scale question: a fact the letter states and the CV omits reads to the",
        "reader as a contradiction (786:242). For a cover letter: fold ONE honest,",
        "specific statement of this scale into the positioning/motivation content.",
        "",
    ]
    return "\n".join(header + items)
