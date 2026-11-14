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
from applire.utils.budget_unit import budget_unit

# The entry's own prose. Same two nodes ADR-070 clause 1 allows an attested
# quote to resolve against (``scope_requirements._ATTESTED_PROSE_FIELDS``) —
# one real bullet, never a concatenation.
_PROSE_FIELDS = ("responsibilities", "achievements")

# ``Figure.value`` is canonical. Since #215 the magnitude is folded in as a
# NUMERIC factor ("6 Mio. €" → "6000000"), so the ``[kmb]`` suffix branch below
# is a tolerated legacy form (the extractor's fail-closed path for a degenerate
# digit run) rather than the normal case — this stays a table lookup either
# way, never a reading. The currency-token table lives in
# ``applire.utils.budget_unit`` (#382 — one implementation, ADR-066).
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
    """The currency token the bullet itself wrote, verbatim. Never invented.

    Delegates to ``utils.budget_unit`` — the SAME vocabulary the CV furniture
    line, the template filter and profile completeness ask "does this budget
    carry a unit" with (#382, ADR-066). One table, so a unit this projection
    records is exactly a unit a document is allowed to print.
    """
    return budget_unit(raw)


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


# ── Sorbet WP-Q1 (b), ruling Q-2 — the KIND of leadership a headcount is ───────
#
# "Führungsverantwortung für 3 Personen" was printed for a person who wrote
# "Fachliche Anleitung von zwei Werkstudierenden" (#832, platform-1 delivery run):
# the furniture line said more than the person did. Q-2: the line names the kind
# the person's OWN text names — functional guidance or disciplinary leadership —
# and says only "N Personen im Verantwortungsbereich" when it names neither.
#
# ADR-062 cl. 1: a FACT — whole-word presence of a kind marker in a bullet that
# also carries a leading verb/noun. Which kind the person "really" had is never
# judged; a role whose text names both kinds, or neither, has no kind (``None``)
# and the planner asks once (Q2, ``entries_needing_leadership_kind``).

# A word that leads PEOPLE — "Geschäftsführung" (the board, a recipient of
# reports) is not one, so the compound is excluded.
_PEOPLE_LEAD = (
    r"(?:(?<!geschäfts)(?<!geschaefts)f(?:ü|ue)hr(?:ung|e|t|en)|geführt|leitung|leite|geleitet"
    r"|anleitung|angeleitet|leite\w* an|betreu\w*|lead\w*|led|manag\w*|supervis\w*|guid\w*)"
)
# Functional: "fachlich" next to a leading word, either order, at most two words
# between ("Fachliche Anleitung von …", "führe … fachlich", "fachliche Führung").
_FUNCTIONAL = re.compile(
    rf"\bfachlich\w*\s+(?:\S+\s+){{0,2}}{_PEOPLE_LEAD}\b"
    rf"|\b{_PEOPLE_LEAD}\s+(?:\S+\s+){{0,2}}fachlich\w*\b"
    r"|\bfunctional(?:ly)?\s+(?:\S+\s+){0,2}(?:lead\w*|led|manag\w*|guid\w*|supervis\w*|report\w*)\b"
    r"|\bdotted[- ]line\b",
    re.I,
)
_DISCIPLINARY_TERMS = (
    r"(?:disziplinarisch\w*|personalverantwortung|disciplinar(?:y|ily)"
    r"|line[- ]manag\w*|direct reports?)"
)
_DISCIPLINARY = re.compile(rf"\b{_DISCIPLINARY_TERMS}\b", re.I)
# "ohne disziplinarische Verantwortung", "keine Personalverantwortung",
# "without disciplinary responsibility", "nicht disziplinarisch": the person says
# what the leadership is NOT — never a disciplinary marker; next to a word that
# leads people ("Teamleitung ohne Personalverantwortung") it is the functional kind.
_NEGATED_DISCIPLINARY = re.compile(
    rf"\b(?:ohne|keine?n?|nicht|without|no|non)[- ]?\s*(?:\w+\s+)?{_DISCIPLINARY_TERMS}\b",
    re.I,
)
_PEOPLE_LEAD_RE = re.compile(rf"\b\w*{_PEOPLE_LEAD}", re.I)

# Adversarial H1 (2026-11-14, MD-Q3): a disciplinary marker ANYWHERE in a bullet
# was read as the person's own kind — "Mein Vorgesetzter hatte Personalverantwortung",
# "lag beim Werksleiter", "Seminar zu Personalverantwortung", "Reported to the line
# manager", "Wer hat disziplinarische Verantwortung? Ich nicht." all printed
# "Disziplinarische Führung von N Mitarbeitenden". The stronger line is the harm
# (Q1-1); the neutral line plus one ask is the safe side. So the disciplinary kind
# now needs a POSITIVE construction of the person's own leadership, in a clause
# (``_CLAUSE_SPLIT``) that carries no cue of another holder, a topic or a question:
_DISCIPLINARY_OWN = re.compile(
    # "Disziplinarische Führung von 38 …", "disziplinarische Verantwortung für 8",
    # "disziplinarische(r) Vorgesetzte(r) von 12" — the marker opens the clause
    # (role-less subject, the CV's own voice) or follows "ich"/"als".
    r"(?:^|\b(?:ich|als)\s+(?:\w+\s+){0,2})disziplinarisch\w*\s+(?:und\s+fachlich\w*\s+)?"
    r"(?:f(?:ü|ue)hrung|leitung|verantwortung|vorgesetzt\w*)\b"
    # "Personalverantwortung für/über 12", "mit Personalverantwortung"
    r"|\bpersonalverantwortung\s*(?::|f(?:ü|ue)r|(?:ü|ue)ber)"
    r"|\bmit\s+(?:voller\s+)?personalverantwortung\b"
    # "führe/leite … disziplinarisch", "disziplinarisch geführt/verantwortlich"
    r"|\b(?:f(?:ü|ue)hr(?:e|te)|leit(?:e|ete)|geführt|geleitet)\b(?:\s+\S+){0,4}?\s+disziplinarisch\w*"
    r"|\bdisziplinarisch\w*\s+(?:geführt|geleitet|verantwortlich|unterstellt)"
    # "12 direct reports", "Managed five direct reports"
    r"|\b(?:\d{1,4}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|\w+teen|twenty|thirty|forty|fifty)\s+direct\s+reports\b"
    # "Line manager of six engineers", "line management for 38"
    r"|\bline[- ]manag(?:er|ement)\s+(?:of|for)\b"
    # "Disciplinary lead of 8", "disciplinarily led", "disciplinary responsibility for"
    r"|\bdisciplinar(?:y|ily)\s+(?:lead\w*|led|manag\w*|responsib\w*|supervis\w*)",
    re.I,
)
# Another holder, a topic, a reporting line: the marker is not the person's own.
_NOT_OWN_CUE = re.compile(
    r"\b(?:vorgesetzt\w*|chef\w*|boss|werks?leit\w*|gesch(?:ä|ae)ftsf(?:ü|ue)hr\w*"
    r"|ein(?:e|em)?\s+andere\w*|jemand\s+anders|someone\s+else|another"
    r"|my\s+manager|mein\w*\s+manager|the\s+manager|der\s+manager"
    r"|lag\s+bei\w*|liegt\s+bei\w*|oblag|obliegt|zust(?:ä|ae)ndig\s+war|war\s+\S+(?:\s+\S+){0,3}\s+zust(?:ä|ae)ndig"
    r"|handled\s+by|held\s+by|by\s+hr|bei\s+der\s+hr|personalabteilung|\bhr\b"
    r"|seminar\w*|schulung\w*|training\w*|kurs\w*|workshop\w*|weiterbildung\w*|course\w*|zertifik\w*"
    r"|report(?:ed|s|ing)?\s+(?:directly\s+)?to|berichte\w*|berichtslinie|unterstellt\s+(?:dem|der)"
    # the leadership held BY someone named: "durch den Abteilungsleiter",
    # "geführt von Frau X", "… was the CTO", "… war der Bereichsleiter"
    r"|durch\s+(?:den|die|das|meine?n?|einen?|frau|herrn?)|(?:geführt|geleitet)\s+(?:von|durch)"
    r"|(?:was|were)\s+(?:the|my|a|an|our)|war\s+(?:der|die|das|mein\w*|frau|herr|ein\w*))\b",
    re.I,
)
# A clause: the person's own statement ends at ; . ! ? or a contrasting conjunction.
_CLAUSE_SPLIT = re.compile(
    r"[;!?]|\.(?:\s|$)|\s[–—-]\s|,\s*(?:aber|jedoch|während|waehrend|but|while|whereas)\b",
    re.I,
)

LEADERSHIP_KINDS = ("disciplinary", "functional")


def _clause_kinds(clause: str, bullet_negated: bool) -> set[str]:
    """The kinds ONE clause states of the person's own leadership. A denial of the
    disciplinary kind anywhere in the bullet ("No direct reports; guided three
    juniors") makes a leading word the functional kind."""
    kinds: set[str] = set()
    negated = bullet_negated
    rest = _NEGATED_DISCIPLINARY.sub(" ", clause)
    cue = _NOT_OWN_CUE.search(rest)
    if _DISCIPLINARY_OWN.search(rest.strip()) and not cue:
        kinds.add("disciplinary")
    if not cue and (_FUNCTIONAL.search(rest) or (negated and _PEOPLE_LEAD_RE.search(rest))):
        kinds.add("functional")
    return kinds


def leadership_kind(entry: WorkEntry | dict) -> str | None:
    """THE kind of leadership a work entry's own text names: ``"disciplinary"``,
    ``"functional"`` or ``None`` (neither, or both). Read from the entry's
    responsibilities and achievements only — never the title, never a model.

    Per clause, and only of the person's OWN leadership (H1): a clause naming
    another holder, a topic (a seminar) or a reporting line counts for no kind; a
    bullet that asks a question counts for none; a marker in parentheses counts
    ("Projektteam geführt (fachlich)"). When in doubt: ``None``."""
    if isinstance(entry, dict):
        try:
            entry = WorkEntry.model_validate(entry)
        except Exception:  # noqa: BLE001 — a reader never fails a render
            return None
    found: set[str] = set()
    for bullet in _prose(entry):
        if "?" in bullet:
            continue
        text = re.sub(r"[()\[\]]", " ", bullet)
        bullet_negated = bool(_NEGATED_DISCIPLINARY.search(text))
        for clause in _CLAUSE_SPLIT.split(text):
            if clause and clause.strip():
                found |= _clause_kinds(clause.strip(), bullet_negated)
    return found.pop() if len(found) == 1 else None


def entries_needing_leadership_kind(profile_json: dict | Any) -> list[dict[str, Any]]:
    """The work entries whose line would print a headcount with no kind: a
    positive ``team_size`` and :func:`leadership_kind` ``None``. The interview
    planner asks each ONCE as its own short question (ruling Q-2, MD-Q1, Q2-1);
    unanswered, the line stays neutral.

    STABLE CONTRACT (MD-Q1, built against by WP-Q2): a list, in entry order, of
    ``{"entry_id": str, "company": str, "role": str, "team_size": int}``."""
    entries = (
        profile_json.get("work_experience") if isinstance(profile_json, dict)
        else getattr(profile_json, "work_experience", None)
    ) or []

    def _get(e: Any, key: str) -> Any:
        return e.get(key) if isinstance(e, dict) else getattr(e, key, None)

    out: list[dict[str, Any]] = []
    for e in entries:
        size, eid = _get(e, "team_size"), _get(e, "id")
        if (
            isinstance(size, int) and not isinstance(size, bool) and size > 0
            and leadership_kind(e) is None
        ):
            # An entry stored without an id (older profile JSON) is still asked
            # about: skipping it would print the neutral line unasked.
            out.append({
                "entry_id": str(eid or ""),
                "company": str(_get(e, "company") or ""),
                "role": str(_get(e, "role") or ""),
                "team_size": int(size),
            })
    return out
