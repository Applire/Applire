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


# ── Sorbet WP-Q1 (b), ruling Q-2, narrowed by MD-Q5 — the KIND of leadership ──
#
# "Führungsverantwortung für 3 Personen" was printed for a person who wrote
# "Fachliche Anleitung von zwei Werkstudierenden" (#832, platform-1 delivery run):
# the furniture line said more than the person did. Q-2: the line names the kind
# of leadership the PERSON states, and says only "N Personen im
# Verantwortungsbereich" otherwise.
#
# MD-Q5 (2026-11-14): the kind is no longer read from bullet prose. Two
# adversarial rounds showed every reading of the prose either promoted a mention
# to a fact ("Disziplinarische Führung … wurde von meinem Nachfolger übernommen",
# "Aspiring to line management of 8 engineers") or demoted a true form
# ("Disziplinarischer Vorgesetzter von 12 Mitarbeitenden"). The kind is a STORED
# fact on the work entry (``WorkEntry.leadership_kind``), written only from the
# person's answer to the dedicated question (``services/profile/leadership_kind``).
# The bullet text is only SHOWN in that question (``"said"``), never interpreted.

LEADERSHIP_KINDS = ("disciplinary", "functional")

# A bullet that says the person LEADS PEOPLE — shown to them in the kind question
# so they see what they wrote. Display only: it never sets, and never withholds,
# a kind. Delivery re-run (Stefan): a bare ``\w*leitung`` quoted "Begleitung der
# jährlichen ISO-9001-Audits …" as his leadership; "Einleitung", "Umleitung",
# "Weiterleitung von Tickets" read the same. So a bullet qualifies only with a
# disciplinary kind word, or a leading word with a PEOPLE word near it.
_KIND_WORD = re.compile(
    r"\b(?:disziplinarisch\w*|personalverantwortung|disciplinar(?:y|ily)"
    r"|line[- ]manag\w*|direct reports?|dotted[- ]line)\b",
    re.I,
)
# Leading words, whole words only: no free prefix before "leitung" (Begleitung,
# Einleitung, Umleitung, Weiterleitung), only the unit nouns that name a team.
_LEAD_WORD = (
    r"(?:f(?:ü|ue)hr(?:ung|e|te|ten|t|en)|geführt|gefuehrt|leite(?:te|t|n)?|geleitet"
    r"|(?:team|gruppen|abteilungs|bereichs|schicht|projekt|standort|werks|fach|produktions)?leitung"
    r"|anleitung|angeleitet|leitete\s+\S+\s+an|betreu\w*|verantwort\w*"
    r"|lead(?:s|ing)?|led|manag(?:e|ed|es|ing)|supervis\w*|guid(?:e|ed|ing|ance)"
    r"|mentor\w*|coach\w*)"
)
_PEOPLE_WORD = (
    r"(?:mitarbeite\w*|ma|personen|person|leute|team\w*|werkstudierende\w*|studierende\w*"
    r"|auszubildende\w*|azubis?|praktikant\w*|entwickler\w*|ingenieur\w*|kolleg\w*"
    r"|schichtleiter\w*|meister\w*|controller\w*|analyst\w*|berater\w*|techniker\w*"
    r"|fachkr(?:ä|ae)fte\w*|people|employees?|engineers?|developers?|staff|reports|members?"
    r"|juniors?|analysts?|consultants?|colleagues|students?|interns?|apprentices?)"
)
_NEAR = r"(?:\W+\w+){0,6}?\W+"
_LEADS_PEOPLE = re.compile(
    rf"\b{_LEAD_WORD}\b{_NEAR}{_PEOPLE_WORD}\b|\b{_PEOPLE_WORD}\b{_NEAR}{_LEAD_WORD}\b",
    re.I,
)
_NUMBER_WORDS = {
    "ein": 1, "eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "one": 1, "two": 2,
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}
_COUNT_PEOPLE = re.compile(rf"\b(\d{{1,4}}|{'|'.join(_NUMBER_WORDS)})\b(?:\W+\w+){{0,2}}?\W+{_PEOPLE_WORD}\b", re.I)


def _says_leads_people(bullet: str) -> bool:
    return bool(_KIND_WORD.search(bullet) or _LEADS_PEOPLE.search(bullet))


def _people_counts(bullet: str) -> set[int]:
    out: set[int] = set()
    for m in _COUNT_PEOPLE.finditer(bullet):
        raw = m.group(1).lower()
        out.add(int(raw) if raw.isdigit() else _NUMBER_WORDS[raw])
    return out


def _get(e: Any, key: str) -> Any:
    return e.get(key) if isinstance(e, dict) else getattr(e, key, None)


def leadership_kind(entry: WorkEntry | dict) -> str | None:
    """THE kind of leadership of a work entry: the STORED value the person's
    answer to the kind question wrote (``"disciplinary"`` / ``"functional"``), or
    ``None`` — never read from the bullets, the title or a model (MD-Q5)."""
    kind = _get(entry, "leadership_kind")
    return kind if kind in LEADERSHIP_KINDS else None


def leadership_said(entry: WorkEntry | dict) -> str:
    """The entry's own bullet that says the person leads PEOPLE, verbatim, or
    ``""`` — for display in the question only. Preferred: the bullet counting the
    entry's ``team_size`` in people, then one with any people count, then the
    first that qualifies."""
    bullets = [
        b.strip() for f in _PROSE_FIELDS for b in (_get(entry, f) or [])
        if isinstance(b, str) and b.strip()
    ]
    qualifying = [b for b in bullets if _says_leads_people(b)]
    if not qualifying:
        return ""
    size = _get(entry, "team_size")
    for b in qualifying:
        if isinstance(size, int) and size in _people_counts(b):
            return b
    for b in qualifying:
        if _people_counts(b):
            return b
    return qualifying[0]


def _record_option(e: Any) -> str | None:
    record = _get(e, "leadership_kind_record")
    if record is None:
        return None
    return str(_get(record, "option") or "") or None


def _ask_extras(e: Any) -> dict[str, Any]:
    """Q8-1 — an entry merge that joined two different answers left a
    ``merge_conflict`` record: the role is asked once more. ``reask`` makes the
    once-per-person-and-role key of that new ask distinct from the old one
    (``scope_question.kind_entry_key``)."""
    if _record_option(e) != "merge_conflict":
        return {}
    record = _get(e, "leadership_kind_record")
    stamp = _get(record, "recorded_at")
    return {"reask": str(stamp.isoformat() if hasattr(stamp, "isoformat") else stamp or "merge")}


def _needs_kind_ask(e: Any) -> bool:
    """No stored kind and never asked — or asked, and an entry merge since
    joined two different answers (Q8-1)."""
    option = _record_option(e)
    return option == "merge_conflict" or (leadership_kind(e) is None and option is None)


def _work_entries(profile_json: dict | Any) -> list[Any]:
    return (
        profile_json.get("work_experience") if isinstance(profile_json, dict)
        else getattr(profile_json, "work_experience", None)
    ) or []


def _positive_size(size: Any) -> bool:
    return isinstance(size, int) and not isinstance(size, bool) and size > 0


def entries_needing_leadership_size(profile_json: dict | Any) -> list[dict[str, Any]]:
    """Q8-2 (founder, Sorbet wave 2b) — the work entries whose OWN text says the
    person leads people (:func:`leadership_said`, the display reader the kind
    question already quotes) but which hold no team size, no stored kind and
    were never asked. The text only TRIGGERS the question; the kind and the
    number are stored only from the person's answer to it. A false trigger costs
    one question (the person answers in own words or skips; nothing is stored);
    a missed one is the behaviour before Q8-2. An entry an entry merge left in
    conflict (Q8-1) is listed without a size too, quote or not.

    Same item shape as :func:`entries_needing_leadership_kind`, with
    ``"team_size": None``."""
    out: list[dict[str, Any]] = []
    for e in _work_entries(profile_json):
        if _positive_size(_get(e, "team_size")) or not _needs_kind_ask(e):
            continue
        said = leadership_said(e)
        if not said and _record_option(e) != "merge_conflict":
            continue
        out.append({
            "entry_id": str(_get(e, "id") or ""),
            "company": str(_get(e, "company") or ""),
            "role": str(_get(e, "role") or ""),
            "team_size": None,
            "said": said,
            **_ask_extras(e),
        })
    return out


def entries_to_ask_leadership(profile_json: dict | Any) -> list[dict[str, Any]]:
    """The interview's kind ask, in entry order: every role
    :func:`entries_needing_leadership_kind` lists (a stored size to confirm,
    Q3-2) and every role :func:`entries_needing_leadership_size` lists (a size
    to ask, Q8-2)."""
    sized = entries_needing_leadership_kind(profile_json)
    unsized = entries_needing_leadership_size(profile_json)
    order = {
        str(_get(e, "id") or "") + "|" + str(_get(e, "company") or "") + "|" + str(_get(e, "role") or ""): i
        for i, e in enumerate(_work_entries(profile_json))
    }
    both = [*sized, *unsized]
    return sorted(both, key=lambda d: order.get(f"{d['entry_id']}|{d['company']}|{d['role']}", len(order)))


def entries_needing_leadership_kind(profile_json: dict | Any) -> list[dict[str, Any]]:
    """The work entries whose line would print a headcount with no kind: a
    positive ``team_size``, no stored kind, and never asked (no
    ``leadership_kind_record``). The interview planner asks each ONCE as its own
    short question with fixed options (ruling Q-2, MD-Q1, MD-Q5); unanswered,
    skipped or answered in own words, the line stays neutral. A person who
    already wrote the kind in a bullet is asked too, and sees that bullet.

    STABLE CONTRACT (MD-Q1, built against by WP-Q2): a list, in entry order, of
    ``{"entry_id": str, "company": str, "role": str, "team_size": int}`` plus,
    since MD-Q5, ``"said": str`` (:func:`leadership_said`, ``""`` when none)."""
    out: list[dict[str, Any]] = []
    for e in _work_entries(profile_json):
        size, eid = _get(e, "team_size"), _get(e, "id")
        # Q8-1: a ``merge_conflict`` record (an entry merge joined two
        # different answers) is asked once more, like a role never asked.
        if _positive_size(size) and _needs_kind_ask(e):
            # An entry stored without an id (older profile JSON) is still asked
            # about: skipping it would print the neutral line unasked.
            out.append({
                "entry_id": str(eid or ""),
                "company": str(_get(e, "company") or ""),
                "role": str(_get(e, "role") or ""),
                "team_size": int(size),
                "said": leadership_said(e),
                **_ask_extras(e),
            })
    return out
