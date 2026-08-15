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

"""
Deterministic Keyword Ledger builder (ADR-048, E037/US198).

The ledger is the single source of truth for every JD expectation. Each entry
carries a ``concept`` (drives the fit score via ADR-035) and its literal
``surface_forms`` (drive ATS coverage via ADR-039), classified ``direct`` /
``partial`` / ``gap`` against the profile with supporting ``evidence``.

The LLM supplies only the status, evidence, and surface forms. Python is
authoritative for ``sources`` (which JD list each expectation came from) and the
derived ``fit_weight`` — never the LLM (mirrors ADR-035's slot-weight rule).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from applire.services.profile.reconcile.stance import (
    declared_denial_matches,
    denial_release_corpus,
    exclude_unconfirmed,
    is_denied_concept,
)

logger = logging.getLogger(__name__)

# fit_weight by the strongest source a concept belongs to.
REQUIRED_WEIGHT = 1.0
NICE_TO_HAVE_WEIGHT = 0.5
KEYWORD_ONLY_WEIGHT = 0.0

# ADR-048 amended 2026-07-27 (driven by ADR-059's amendment of the same date):
# four values, not three. ``gap`` narrowed to mean UNKNOWN — no signal, never
# asked, or asked and unanswered; ``denied`` means the candidate was asked and
# stated they do not have it. ``claimable`` is unchanged (``status in {direct,
# partial}``) so a denial is still never claimable — but "we do not know" and
# "they told us no" stop being the same value, which is what the interview, the
# writers and the ADR-060 critic each need in order to behave differently.
#
# NOTE the wire name: ``gap`` now means "unknown". The clearer rename was
# deliberately not taken mid-flavour (recorded as debt in the ADR).
_VALID_STATUS = {"direct", "partial", "gap", "denied"}

# The one place the honest marker is spelled, so the floor and every write seam
# that records a denial cite identical evidence text.
DENIED_EVIDENCE = "Candidate explicitly stated a limit here (interview)."

# ADR-059 amended 2026-08-08 (#486) — the floor's OTHER outcome, and the reason
# it needs its own marker. A concept reached only by CONTAINMENT in a denied
# compound ("CSS" inside a denied "Tailwind CSS") is floored — never claimable —
# but the candidate never named it, so writing DENIED_EVIDENCE for it would put
# testimony in the ledger that was never given (and render it verbatim into a
# letter: "THE CANDIDATE WAS ASKED AND STATED THEY DO NOT HAVE THIS"). This
# marker states exactly what is known: no evidence, and a related limit — a fact
# about the ledger, not a quotation of the candidate.
DENIAL_FLOOR_EVIDENCE = (
    "Not claimable: no vault evidence for this concept outside a related limit "
    "the candidate stated (no statement was made about this term itself)."
)


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def _matches(a_norm: str, b_norm: str) -> bool:
    """Exact or substring (either direction) — mirrors compute_match_score()."""
    if not a_norm or not b_norm:
        return False
    return a_norm == b_norm or a_norm in b_norm or b_norm in a_norm


def _fit_weight(sources: set[str]) -> float:
    if "required" in sources:
        return REQUIRED_WEIGHT
    if "nice_to_have" in sources:
        return NICE_TO_HAVE_WEIGHT
    return KEYWORD_ONLY_WEIGHT  # keyword-only


def _tokens(s: str) -> list[str]:
    """Normalised word tokens of a concept, e.g. "CI/CD pipelines" -> ["ci", "cd", "pipelines"].

    Splits on non-word-character sequences, treating Unicode letters (including umlauts
    and accented characters) as word characters. Fixes #408: German umlauts (ä, ö, ü, ß)
    and other diacritics are now correctly recognized as part of tokens, not as separators.
    """
    return [t for t in re.split(r"[^\w]+", _norm(s)) if t]


def _is_token_prefix_dup(a_tokens: list[str], b_tokens: list[str]) -> bool:
    """True when the shorter token list is a leading slice of the longer one.

    This catches the LLM emitting both a short keyword and the JD's full phrase
    ("Kubernetes" vs "Kubernetes (production at scale)" -> ["kubernetes"] is a
    prefix of ["kubernetes", "production", "at", "scale"]). It deliberately does
    NOT treat a sub-token ("java" in "javascript") or a mid-phrase token ("saas"
    inside "multi tenant saas platform scaling") as a duplicate.
    """
    if not a_tokens or not b_tokens:
        return False
    short, long = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    return long[: len(short)] == short


def _surface_norm_set(entry: dict[str, Any]) -> set[str]:
    """Normalised surface forms of an entry, including its own concept name."""
    forms = entry.get("surface_forms") or [entry.get("concept", "")]
    s = {_norm(f) for f in forms if _norm(f)}
    s.add(_norm(entry.get("concept", "")))
    s.discard("")
    return s


def _surface_forms_norm(entry: dict[str, Any]) -> set[str]:
    """Normalised surface forms only — WITHOUT the concept name folded in."""
    forms = entry.get("surface_forms") or [entry.get("concept", "")]
    return {_norm(f) for f in forms if _norm(f)}


def _is_mirror_surface_dup(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two entries are clearly the same concept via their surface forms.

    Two independent signals, both conservative:

    1. **Mutual concept membership** — each entry's canonical name appears among
       the other's surface forms (``AI`` ↔ ``Artificial Intelligence``). A
       one-directional overlap (``Collaborative Research`` lists ``Research`` but
       not vice versa) is deliberately NOT enough.
    2. **Identical surface-form sets** — the LLM emitted the same concept twice
       under different canonical names (``Artificial Intelligence (AI)`` vs
       ``AI``) where neither name is the other's surface form, but both list the
       exact same forms. Distinct concepts never share an identical multi-form
       set (``Algorithm design`` and ``Algorithm development`` do not), so this
       stays safe from false merges.
    """
    a_c, b_c = _norm(a.get("concept", "")), _norm(b.get("concept", ""))
    if not a_c or not b_c:
        return False
    if a_c in _surface_norm_set(b) and b_c in _surface_norm_set(a):
        return True
    a_sf = _surface_forms_norm(a)
    return bool(a_sf) and a_sf == _surface_forms_norm(b)


def _collapse_prefix_duplicates(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate concepts that share the same status (E037 F2 + follow-up).

    The LLM often emits the same skill twice — either as a short keyword and the
    JD's full requirement phrase (``Kubernetes`` vs ``Kubernetes (production at
    scale)``, a *token prefix*), or as an acronym and its expansion that each list
    the other as a surface form (``AI`` ↔ ``Artificial Intelligence``, a *mirror
    surface-form* duplicate). Left as two entries they clutter the gap list and
    double-count the fit slot. Merge each such pair into one entry keyed by the
    *shorter* concept (the cleaner ATS label), unioning surface forms and sources,
    recomputing ``fit_weight`` from the merged sources, and keeping the first
    non-empty evidence. Merging is confined to entries with an identical status so
    a claimable form can never absorb a gap form (or vice versa). Group order
    follows first appearance; downstream consumers count entries, never assume a
    fixed count.
    """
    groups: list[dict[str, Any]] = []
    for entry in ledger:
        toks = _tokens(entry.get("concept", ""))
        home = None
        for g in groups:
            if g["status"] != entry.get("status"):
                continue
            if _is_token_prefix_dup(toks, g["tokens"]) or any(
                _is_mirror_surface_dup(entry, m) for m in g["members"]
            ):
                home = g
                break
        if home is None:
            groups.append({"status": entry.get("status"), "tokens": toks, "members": [entry]})
        else:
            home["members"].append(entry)
            # Keep the shortest member's tokens as the group's canonical anchor.
            if len(toks) < len(home["tokens"]):
                home["tokens"] = toks

    merged: list[dict[str, Any]] = []
    for g in groups:
        members = g["members"]
        if len(members) == 1:
            merged.append(members[0])
            continue
        # Canonical concept = the member with the fewest tokens (shortest label).
        canonical = min(members, key=lambda m: len(_tokens(m.get("concept", ""))))
        surface_forms: list[str] = []
        seen_forms: set[str] = set()
        sources: set[str] = set()
        # #434 (charter run 15): keep EVERY member's evidence, not the first
        # non-empty one. The merged entry stands for all its members' surface
        # forms, so dropping a member's evidence drops support for a term the
        # entry still claims to cover. Run-15 ground truth: the classifier
        # correctly emitted `SAP` (declared basic), `SAP PP` and `SAP MM` (both
        # intermediate, from Key-User testimony); all three are `partial`, so
        # the merge collapsed them and kept whichever came first — discarding
        # the evidence for the two sub-terms the JD actually asks for. The
        # ADR-061 ceiling was honoured per entry and defeated across entries.
        # Deduped by normalized text (members frequently restate one fact) and
        # ordered by member appearance, so the result is order-stable.
        evidence_parts: list[str] = []
        seen_evidence: set[str] = set()
        for m in members:
            for sf in m.get("surface_forms") or [m.get("concept", "")]:
                if _norm(sf) and _norm(sf) not in seen_forms:
                    seen_forms.add(_norm(sf))
                    surface_forms.append(sf)
            sources |= set(m.get("sources", []))
            ev = (m.get("evidence") or "").strip()
            if ev and _norm(ev) not in seen_evidence:
                seen_evidence.add(_norm(ev))
                evidence_parts.append(ev)
        evidence = "; ".join(evidence_parts)
        # ADR-048 amended 2026-08-13 (#526): start from the canonical entry and
        # OVERRIDE. This used to be a fixed key literal, which silently dropped
        # every field outside it — `adjacent_evidence` among them, so a JD naming
        # both "Digitalisierung" and "Digitalisierung der Fertigung" collapsed an
        # adjacent partial into a below-the-bar partial and lost the over-claim
        # protection and the positioning obligation together, unlogged. A
        # whitelist here is a list that grows by construction: the field it drops
        # next is invisible in the diff.
        merged.append(
            {
                **canonical,
                "concept": canonical.get("concept", ""),
                "surface_forms": surface_forms,
                "sources": sorted(sources),
                "fit_weight": _fit_weight(sources),
                "status": g["status"],
                "evidence": evidence if canonical.get("claimable") else "",
                "claimable": canonical.get("claimable", False),
            }
        )
    return merged


def _enforce_gap_stance(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ledger's own honest-gap verdict outranks claimable aliasing (F4, blind
    PQ 2026-07-02 — trust-critical).

    The LLM classified the JD's compound requirement "Cloud environment
    qualification (AWS, Azure)" as partial on AWS-only evidence but echoed
    "Azure" among its surface forms, while the SAME response classified "Azure"
    itself as a gap. Every claimable-surface-form consumer (ATS audit buckets,
    generator/reviewer prompt blocks) then presented Azure as "supported by your
    profile" — inverting the truthfulness promise for a skill the user had
    explicitly denied. Two deterministic rules, applied after duplicate collapse:

    1. A claimable entry whose CONCEPT the ledger elsewhere classifies "gap" is
       dropped — the honest side wins a direct contradiction (never-claim
       outranks claim, ADR-040/ADR-048).
    2. A claimable entry's surface forms are stripped of any form norm-EQUAL to
       an honest-gap concept (exact match only — never substrings, so "Docker"
       survives a "Docker Swarm" gap). If nothing survives, the entry keeps its
       own concept (builder invariant: surface_forms is never empty).
    """
    gap_concepts = {
        _norm(e.get("concept", "")) for e in ledger if not e.get("claimable")
    }
    gap_concepts.discard("")
    if not gap_concepts:
        return ledger

    result: list[dict[str, Any]] = []
    for entry in ledger:
        if entry.get("claimable"):
            if _norm(entry.get("concept", "")) in gap_concepts:
                logger.warning(
                    "_enforce_gap_stance: dropped claimable duplicate of honest-gap "
                    "concept %r (gap verdict wins)",
                    entry.get("concept"),
                )
                continue
            forms = [
                f
                for f in (entry.get("surface_forms") or [entry.get("concept", "")])
                if _norm(f) not in gap_concepts
            ]
            if len(forms) != len(entry.get("surface_forms") or []):
                logger.info(
                    "_enforce_gap_stance: stripped honest-gap surface forms from "
                    "claimable concept %r",
                    entry.get("concept"),
                )
            entry = {**entry, "surface_forms": forms or [entry.get("concept", "")]}
        result.append(entry)
    return result


def _denied_concept_entries(
    denied_concepts: list[dict[str, Any]] | list[str] | None,
) -> list[tuple[str, str]]:
    """Normalise the two accepted ``denied_concepts`` shapes into
    ``(concept, denial_level)`` pairs (ADR-064).

    ``dict`` items are the raw ``DeniedConcept`` shape (``ProfileMetadata.
    denied_concepts``, #231): ``concept`` and ``denial_level`` are read off
    it, with ``denial_level`` defaulting to ``"direct"`` when the key is
    absent (a row persisted before ADR-064, or ``model_dump()``'d without the
    field) or holds anything other than the two known levels. A bare ``str``
    item (every caller/test before ADR-064) is treated as level ``"direct"``
    — back-compat path so existing callers never have to change at once.
    """
    out: list[tuple[str, str]] = []
    for d in denied_concepts or []:
        if isinstance(d, dict):
            concept = d.get("concept", "")
            level = d.get("denial_level")
        else:
            concept = d
            level = None
        if not _norm(concept):
            continue
        if level not in ("direct", "partial"):
            level = "direct"
        out.append((concept, level))
    return out


def _declared_denial_level(
    concept: str,
    forms: list[str],
    entries: list[tuple[str, str]],
) -> str | None:
    """The ``denial_level`` to assert for this ledger entry, or ``None`` when
    NO denied concept declares it (ADR-059 amended 2026-08-08, #486).

    ``None`` is the whole point: it is the answer "this entry is floored by
    containment, and the candidate never named it" — the case in which a
    denial may be *refused* but never *asserted*.

    The entry's concept and every surface form are probed against the persisted
    ``DeniedConcept.concept`` strings through
    :func:`applire.services.profile.reconcile.stance.declared_denial_matches`
    (declared branch only, longest match first). Among the declared matches
    ``"partial"`` wins over ``"direct"`` — the pre-existing
    ``_enforce_denial_stance`` tie-break, kept verbatim: the product rule is
    EXACTLY ONE PROBE PER CONCEPT and it is terminal, so once any matching
    denial was probed to exhaustion the concept is treated as exhausted. That
    rule is about elicitation, not about which term was declared, so
    longest-match-first orders the matches without overriding it.
    """
    if not entries:
        return None
    by_concept: dict[str, str] = {}
    for d_concept, level in entries:
        by_concept.setdefault(_norm(d_concept), level)
    all_denied = [d_concept for d_concept, _level in entries]
    probes = list(dict.fromkeys([concept, *(forms or [])]))
    matched: list[str] = []
    for probe in probes:
        for match in declared_denial_matches(probe, all_denied):
            if match not in matched:
                matched.append(match)
    if not matched:
        return None
    matched_levels = [by_concept.get(_norm(m), "direct") for m in matched]
    return "partial" if "partial" in matched_levels else "direct"


def _denied_row(entry: dict[str, Any], denial_level: str) -> dict[str, Any]:
    """THE one shape a denied ledger row is written in — the assert half of the
    floor (ADR-059 amended 2026-08-08 clause (b), #486).

    Three writers record a denial: the rebuild floor
    (:func:`_enforce_denial_stance`), the in-place upgrade seam
    (:func:`upgrade_ledger_for_concepts`) and #318's persist-seam heal
    (:func:`assert_claimable_backed`). They used to hold three copies of this
    dict literal, and the copies had ALREADY diverged — the heal never wrote
    ``denial_level``, so a rebuild and a heal of the same row produced
    different entries. One function, one shape, and a lockstep test that fails
    the moment a fourth copy appears.

    Never called for a containment-only match: writing :data:`DENIED_EVIDENCE`
    is testimony, and testimony needs a declared term (:func:`_floored_row` is
    that case's write).
    """
    out = {
        **entry,
        # ADR-059 amended 2026-07-27: the floor writes the STATUS, not merely
        # the flag. Forcing "gap" here discarded the reason the concept is
        # unclaimable — downstream could no longer tell a requirement nobody
        # asked about from one the candidate refused.
        "status": "denied",
        "claimable": False,
        "evidence": DENIED_EVIDENCE,
        # ADR-064 — mirror the durable denial's level onto the ledger (the
        # ledger is rebuilt from scratch every run; the DeniedConcept is the
        # durable home).
        "denial_level": denial_level,
    }
    # ADR-048 amended 2026-08-13 (#526): the adjacency pointer lives only on a
    # claimable `partial`. A denial is the candidate's own position on the term
    # itself, so there is no substitute to promote — and the letter's UNADDRESSED
    # block reads the field unconditionally.
    out.pop("adjacent_evidence", None)
    return out


def _floored_row(entry: dict[str, Any]) -> dict[str, Any]:
    """THE one shape a CONTAINMENT-floored ledger row is written in — the
    never-upgrade half without the assert half (ADR-059 amended 2026-08-08).

    The concept is reached only by containment in a denied compound and nothing
    affirms it independently, so it must not be claimed — and the candidate
    never named it, so nothing may be stated about their testimony. ``gap``
    (ADR-048's "unknown"), not claimable, with :data:`DENIAL_FLOOR_EVIDENCE`
    instead of :data:`DENIED_EVIDENCE`.

    ``denial_level`` is deliberately absent (and stripped if the row carried
    one): the level describes a denial the candidate gave, and this row records
    none. The row stays eligible for the corpus-aware vault re-evaluation,
    which keeps its own containment floor — it can only be upgraded by real
    vault evidence outside the denied compound.
    """
    out = {**entry, "status": "gap", "claimable": False, "evidence": DENIAL_FLOOR_EVIDENCE}
    out.pop("denial_level", None)
    # ADR-048 amended 2026-08-13 (#526) — same reason as `_denied_row`: the row
    # has left the claimable adjacent-partial shape, so the pointer leaves too.
    out.pop("adjacent_evidence", None)
    return out


def _enforce_denial_stance(
    ledger: list[dict[str, Any]],
    denied_concepts: list[dict[str, Any]] | list[str] | None,
    vault_corpus: str | None = None,
) -> list[dict[str, Any]]:
    """The candidate's PERSISTED denials (#231, ProfileMetadata.denied_concepts)
    are a hard floor the classifier's own adjacency inference can never
    override.

    Blind acceptance run 2026-07-23 (F8): a candidate denied hands-on
    LegalTech/embedding-model/vector-store/reranker work in testimony, the
    denial vanished (never persisted — fixed separately), and the NEXT
    ``analyze_gaps`` run upgraded the denied concept via adjacency ("RAG
    experience typically involves embeddings") from ``{gap, claimable: false}``
    to ``{partial, claimable: true}``. This is the deterministic backstop once
    the denial IS persisted: any ledger entry whose concept OR surface forms
    match a denied concept is forced to ``gap``/``claimable: false`` regardless
    of what the classifier said, its evidence replaced with an honest marker.

    Matching reuses THE SAME instrument ``enforce_stance`` uses for the
    same-turn op guard (``stance.is_denied_concept`` — alias groups,
    word-boundary, unicode-normalized) — one predicate for both the reconcile-
    time guard and this durable ledger floor, never a second matcher that
    could quietly disagree (concept-scoped, NOT topic-radius: denying
    "hands-on embeddings config" does not touch an unrelated "RAG" entry).

    TWO ACTS, TWO WIDTHS (ADR-059 amended 2026-08-08, #486). One predicate was
    serving two acts of opposite polarity:

      * **refusing the claim** (``claimable: False``) keeps ``is_denied_concept``
        whole, containment branch included. A false positive here claims LESS,
        which is ADR-062 clause 5's sanctioned failure direction;
      * **asserting the denial** (``status="denied"`` + :data:`DENIED_EVIDENCE`)
        is *testimony* — the ledger stating the candidate said they lack this,
        rendered verbatim into a letter ("THE CANDIDATE WAS ASKED AND STATED
        THEY DO NOT HAVE THIS"). It narrows to the DECLARED term
        (:func:`_declared_denial_level` → ``stance.declared_denial_matches``),
        never the compound-containment branch. A declared "Tailwind CSS" keeps
        its own row's ``denied``/``DENIED_EVIDENCE``; a "CSS" row reached only
        by containment is floored to a non-claimable ``gap`` with
        :data:`DENIAL_FLOOR_EVIDENCE` (:func:`_floored_row`) — floored, never
        asserted. Fabricated testimony about a term the candidate never named
        is the failure that closes.

    ``vault_corpus`` (#249 run-4, 2026-07-24; narrowed by #480 step 1,
    2026-08-08, and again by #480 §7.5(a), 2026-08-09): the vault's ATTESTED
    ENTITY LABELS
    (:func:`applire.services.profile.reconcile.stance.denial_release_corpus`),
    threaded through to ``is_denied_concept`` so its compound-containment rule
    ("RAG" is a whole word strictly inside the denied "RAG pipeline") can
    independently affirm a BROAD term against real vault evidence instead of
    always fail-closing (the #207 CSS/Tailwind-CSS default, correct when there
    is nothing attested to check). A broad term is downgraded only if it is
    itself denied, or has no independent ATTESTATION outside the denied
    compound — never both classified `direct` and presented as an unsupported
    claim by the ATS panel on the very same document.

    ``denied_concepts`` accepts either the raw ``DeniedConcept`` dicts (which
    carry ``denial_level``, ADR-064) or a plain ``list[str]`` (every caller
    before ADR-064, treated as level ``"direct"``) — see
    :func:`_denied_concept_entries`. Whichever denied concept DECLARES an
    entry, its level is mirrored onto ``denial_level`` on the forced row; when
    more than one denied concept declares the same ledger entry, ``"partial"``
    wins (the stronger signal — elicitation was exhausted on at least one of
    the matching denials).

    Neither an ``unconfirmed`` vault entry (#480 step 1, ADR-061 clause 3 —
    the reconciler's own inference backs nothing) nor editor-typed prose (#480
    §7.5(a) — a sentence typed into a document is not an attested vault entity)
    ever reaches ``vault_corpus``: neither may be the independent affirmation
    that releases a persisted denial.
    """
    entries = _denied_concept_entries(denied_concepts)
    if not entries:
        return ledger

    all_denied_concepts = [d_concept for d_concept, _level in entries]

    result: list[dict[str, Any]] = []
    for entry in ledger:
        concept = entry.get("concept", "")
        forms = entry.get("surface_forms") or [concept]
        # "Is this entry denied AT ALL?" is decided with ONE call against the
        # FULL joint list of denied concepts — that is what lets
        # ``_independently_affirmed`` blank every denied phrase out of the
        # vault corpus TOGETHER before checking for independent evidence.
        # Looping this call per-concept over singleton lists (regression,
        # 2026-07-29) silently fails OPEN: when two denied concepts share a
        # substring that never appears standalone in the vault, each
        # singleton call blanks only its own phrase, so the OTHER phrase's
        # leftover text reads as "independent" affirmation and neither call
        # fires — the floor stops firing at all for that entry.
        is_denied = is_denied_concept(concept, all_denied_concepts, vault_corpus) or any(
            is_denied_concept(f, all_denied_concepts, vault_corpus) for f in forms
        )
        if not is_denied:
            result.append(entry)
            continue
        if entry.get("claimable"):
            logger.warning(
                "_enforce_denial_stance: forced claimable concept %r to gap — "
                "the candidate explicitly denied it in testimony (#231, "
                "ADR-040 never-claim-beats-claim outranks adjacency inference)",
                concept,
            )
        # Second pass — the ASSERT half (ADR-059 amended 2026-08-08, #486).
        # The entry is floored either way; this decides whether the floor may
        # also state that the candidate SAID SO. Only a declared denial (the
        # persisted DeniedConcept naming this concept) may, and it also
        # supplies the ``denial_level``. A containment-only match is floored
        # and left silent about testimony that was never given.
        level = _declared_denial_level(concept, forms, entries)
        if level is None:
            logger.info(
                "_enforce_denial_stance: floored %r without asserting a denial — "
                "it is only contained in a denied compound the candidate named, "
                "and no denial names this concept (#486)",
                concept,
            )
            result.append(_floored_row(entry))
            continue
        result.append(_denied_row(entry, level))
    return result


def split_ledger_for_prompt(
    keyword_ledger: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split a Keyword Ledger into the two lists a generator prompt needs (ADR-048 §8).

    Returns ``(claimable, forbidden)`` where:
      * ``claimable`` — entries the candidate truthfully supports (``claimable`` True,
        i.e. status direct/partial). Each carries ``concept``, ``surface_forms`` and the
        profile ``evidence`` so the generator surfaces the term ONLY where supported.
      * ``forbidden`` — the honest-gap concepts (``claimable`` False). These are NOT in
        the profile and must NEVER be claimed — surfaced to the prompt as a do-not-claim
        list only.

    Pure; tolerant of ``None``/empty (legacy pre-E037 gap rows have no ledger).

    ADR-069: scope entries (:func:`is_scope_entry`) appear in NEITHER list.
    Their synthesised concept embeds the JD's own figure — rendering one as
    claimable instructs the writer to surface that number as the candidate's
    fact (the run-#7 pathology, found live by the 2026-08-01 adversarial pass:
    ``render_ledger_prompt_block``'s ``or [concept]`` fallback re-created it),
    and rendering one as forbidden burns the do-not-claim list on a label no
    writer would produce. Scope material reaches documents only through the
    testimony the interview probe elicits, never through this block.
    """
    claimable: list[dict[str, Any]] = []
    forbidden: list[str] = []
    for entry in keyword_ledger or []:
        if is_scope_entry(entry):
            continue
        if entry.get("claimable"):
            claimable.append(entry)
        else:
            concept = entry.get("concept")
            if concept:
                forbidden.append(concept)
    return claimable, forbidden


def is_positioning_only(entry: dict[str, Any] | None) -> bool:
    """True for a claimable entry the candidate does NOT actually hold (ADR-048
    amended 2026-07-27): an ADJACENT ``partial`` whose ``adjacent_evidence``
    names the capability that stands in for the JD's term.

    THE single definition of that exemption. Three instruments have to agree on
    it or they pull the document apart: the coverage reviewer must not demand
    the term (that is a demand to over-claim), the ATS panel must not grade its
    absence as a surfacing miss, and the page budget must protect the
    SUBSTITUTE rather than the term. Charter run #7 is what happens when only
    the first of the three knows (#122's "the loop that grades is the loop that
    heals", stated the other way round).

    **The status guard is load-bearing (ADR-048 amended 2026-08-13, #526).** This
    used to be a bare ``bool(entry.get("adjacent_evidence"))``, so ANY row that
    kept the pointer read as positioning-only — including rows that had left the
    claimable-partial shape and had no business claiming the exemption. That is
    not cosmetic: the exemption switches off the ADR-061 vault-evidence floor
    (:func:`_claimable_backing_violation` clause 3), the coverage demand, the
    outcome critic's presence facts and the load-bearing veto, all at once. Two
    live paths produced such rows — :func:`downgrade_ledger_for_concepts` (the
    candidate DECLINING a keyword liability) and the denial/heal writes — and a
    third silently deleted the pointer instead (:func:`_collapse_prefix_duplicates`).
    All three are fixed at the writer; this is the reader half, so a stale
    pointer arriving from anywhere is inert rather than load-bearing.
    """
    e = entry or {}
    if not e.get("claimable") or e.get("status") != "partial":
        return False
    return bool(e.get("adjacent_evidence"))


def is_unasked_requirement(entry: dict[str, Any] | None) -> bool:
    """True for a JD hard requirement Applire holds NOTHING on and never asked
    about — ADR-074's *Restfall*, and THE single definition of it.

    All of: not ``claimable`` · ``"required" in sources`` · no
    ``adjacent_evidence`` · no ``evidence`` · ``status != "denied"`` · not a
    scope entry.

    Such a row has no truthful expression in a cover letter, and that is why it
    is named rather than handled: asserting the term is ungrounded (it is on the
    clause-6 DO-NOT-CLAIM list), staying silent breaks the UNADDRESSED HARD
    REQUIREMENTS block's own instruction, and denying it is an INVENTED LIMIT —
    ``gap`` means *nobody asked*, not *the candidate said no*, so no stated limit
    grounds the denial. Gate charter run 1 spent 37 of 68 blocking issues and ten
    reviewer rounds on two rows of exactly this shape.

    Every conjunct is load-bearing:

    * ``evidence == ""`` is what separates this from the ADR-059/#486
      **containment-floored** gap, which carries :data:`DENIAL_FLOOR_EVIDENCE`
      and therefore *does* have a related stated limit to build on.
    * ``status != "denied"`` is implied today — :func:`_denied_row` always writes
      :data:`DENIED_EVIDENCE` — and is kept as an explicit fail-safe. A control's
      correctness must not depend on the spelling of a sentinel, and the failure
      it prevents is reclassifying the candidate's own testimony as "we never
      asked you".
    * :func:`is_scope_entry` is excluded because a scope row's concept is a
      synthesised label carrying the JD's own figure; ADR-070 records that a
      persistent scope gap is positioned nowhere, deliberately.

    The predicate presumes the adjacency-pointer lifecycle invariant (ADR-048
    amended 2026-08-13). Without it, :func:`downgrade_ledger_for_concepts` leaves
    a stale ``adjacent_evidence`` on a concept the candidate has just DECLINED,
    and this predicate would then exclude that row from the Restfall — letting
    the letter promote the declined capability.

    Pure; ``None``/malformed tolerant.
    """
    e = entry if isinstance(entry, dict) else None
    if not e:
        return False
    if e.get("claimable"):
        return False
    if "required" not in (e.get("sources") or []):
        return False
    if e.get("status") == "denied":
        return False
    if (e.get("adjacent_evidence") or "").strip():
        return False
    if (e.get("evidence") or "").strip():
        return False
    return not is_scope_entry(e)


def unasked_hard_requirements(
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Every :func:`is_unasked_requirement` row, in ledger order (ADR-074).

    Two consumers, one definition: the letter excludes these from generation
    (:func:`applire.services.cross_document.find_unaddressed_hard_requirements`),
    and ``GapAnalysisResponse`` derives the user-facing notice from the same rows
    — per application, on the response the gaps page and the ``analyze_gaps`` MCP
    tool already return, so the notice cannot drift from the ledger it summarises
    and needs no new endpoint or tool (the #260 ``keyword_liabilities`` pattern).
    """
    return [e for e in (keyword_ledger or []) if is_unasked_requirement(e)]


def is_scope_entry(entry: dict[str, Any] | None) -> bool:
    """True for a quantified-scope ledger entry (ADR-069 — a ``bar`` facet).

    THE single definition of the scope exemption, parallel to
    :func:`is_positioning_only`. A scope entry's concept is a synthesised
    label ("Führungsspanne ~120 MA") carrying the JD's own number — a thing
    to ASK about, never a string a writer must force into the document.
    Every coverage instrument excludes it: an empty ``surface_forms`` list
    is NOT sufficient, because every consumer falls back to the bare concept
    (``entry.get("surface_forms") or [concept]``), and
    :func:`verified_missing_claimable` would otherwise drive the ADR-021
    retry loop to insert the JD's own figure (the run-#7 pathology). Its
    status also never moves via literal corpus presence — see
    :func:`reevaluate_gap_ledger_against_vault` and
    :func:`upgrade_ledger_for_concepts`.
    """
    return bool((entry or {}).get("bar"))


def retention_forms(entry: dict[str, Any]) -> list[str]:
    """The surface forms that mark a CV bullet as carrying this entry's evidence.

    ``surface_forms ∪ {concept}`` for a normal entry. For a positioning-only
    entry the JD's own term is REPLACED by ``adjacent_evidence``: the candidate
    has no bullet containing "TOGAF", so retaining that form protects nothing,
    while the arc42 bullet it is meant to promote would otherwise score as a
    no-hit. (Corrected 2026-08-01, ADR-070 recon: since #377/ADR-067 clause 4,
    ``condense_to_budget``'s per-bullet CUT order is ``bullet_carries_figure``,
    not these forms — this function feeds the role-level relevance/tier signal
    via ``cv_budget._flatten_claimable_forms``/``_hit_count`` only.)

    Never widens what may be CLAIMED — this feeds bullet RETENTION only. The
    adjacent term deliberately stays out of ``surface_forms`` so it can never
    make the JD's term read as present (:func:`claimable_surface_forms`).
    """
    if is_positioning_only(entry):
        return [str(entry["adjacent_evidence"])]
    forms = [f for f in (entry.get("surface_forms") or []) if isinstance(f, str)]
    concept = entry.get("concept")
    if concept:
        forms.append(str(concept))
    return forms


def render_ledger_prompt_block(keyword_ledger: list[dict[str, Any]] | None) -> str:
    """Render the Keyword Ledger as a prompt fragment shared by the CV and cover-letter
    generators (ADR-048 §8 / US200/US201).

    States the grounding-outranks-coverage precedence, lists each CLAIMABLE concept with
    its literal surface forms + the profile evidence that supports it, and lists the
    honest-gap concepts under an explicit DO NOT CLAIM heading. Returns "" when the ledger
    is empty so legacy callers and pre-E037 rows add nothing.
    """
    claimable, forbidden = split_ledger_for_prompt(keyword_ledger)
    if not claimable and not forbidden:
        return ""

    lines: list[str] = [
        "KEYWORD LEDGER (ADR-048) — grounding strictly OUTRANKS coverage:",
        "Surface a claimable keyword ONLY where the listed profile evidence supports it; "
        "if surfacing it would need any stretch, drop it. These are estimates of an "
        "unknowable target, so do NOT over-stuff. NEVER claim a do-not-claim term.",
        "",
        "CLAIMABLE (supported by the profile — surface these, using the evidence as your basis):",
    ]
    if claimable:
        for entry in claimable:
            forms = ", ".join(entry.get("surface_forms") or [entry.get("concept", "")])
            evidence = entry.get("evidence", "") or "(no extra evidence given)"
            concept = entry.get("concept", "")
            adjacent = entry.get("adjacent_evidence")
            if adjacent:
                # ADR-048 am. 2026-07-27: the candidate does NOT have this one.
                # Naming the substitute is the only actionable instruction here;
                # without it the writer is simply told to "surface TOGAF".
                lines.append(
                    f"  - {concept} [forms: {forms}] — the candidate does NOT have "
                    f"{concept} itself; the profile's adjacent capability is "
                    f"{adjacent}. Give {adjacent} prominence in its own right and "
                    f"NEVER present {concept} as something the candidate has. "
                    f"evidence: {evidence}"
                )
            else:
                lines.append(f"  - {concept} [forms: {forms}] — evidence: {evidence}")
    else:
        lines.append("  (none)")

    # A denial and an unknown are both unclaimable, but they are not the same
    # thing and the document cannot treat them the same way (ADR-059 amended
    # 2026-07-27). An unknown is simply absent. A denial is a position the
    # CANDIDATE took, in their own words — which is exactly what makes it
    # positionable rather than merely forbidden.
    denied = [
        e.get("concept", "")
        for e in (keyword_ledger or [])
        if e.get("status") == "denied" and e.get("concept")
    ]
    denied_norms = {_norm(c) for c in denied}
    unknown = [c for c in forbidden if _norm(c) not in denied_norms]

    lines += [
        "",
        "DO NOT CLAIM (honest gaps — NOT in the profile; never present these as something "
        "the candidate has, has done, or knows):",
    ]
    if unknown:
        for concept in unknown:
            lines.append(f"  - {concept}")
    else:
        lines.append("  (none)")

    if denied:
        lines += [
            "",
            "EXPLICITLY DENIED BY THE CANDIDATE (they were asked and stated they do NOT "
            "have these). Never claim them, and never soften or walk back the denial. "
            "Unlike the list above these are the candidate's OWN stated position, so a "
            "cover letter MAY name one honestly and follow it with what they do bring "
            "instead — a CV simply omits them:",
        ]
        for concept in denied:
            lines.append(f"  - {concept}")

    return "\n".join(lines)


def claimable_surface_forms(
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Flatten every surface form of every CLAIMABLE ledger entry (ADR-048 / US203).

    Used by the ATS audit to decide whether a MISSING keyword is a *missing-claimable*
    (the candidate supports it per the ledger, so it should have been surfaced) or a
    *missing-honest-gap* (not in the profile — honestly absent). De-duplicated, order
    preserved. ``None``/empty tolerant (legacy pre-E037 rows have no ledger).

    A positioning-only entry is excluded (ADR-048 amended 2026-07-27,
    :func:`is_positioning_only`) — the candidate does NOT have that term, so its
    absence is an honest gap, not a surfacing miss. Without this the ATS panel
    renders the amber "structure OK, N keywords missing" state naming a term the
    coverage reviewer (:func:`verified_missing_claimable`) has already, correctly,
    decided not to write.
    """
    forms: list[str] = []
    seen: set[str] = set()
    claimable, _ = split_ledger_for_prompt(keyword_ledger)
    for entry in [
        e for e in claimable if not is_positioning_only(e) and not is_scope_entry(e)
    ]:
        for sf in entry.get("surface_forms") or [entry.get("concept", "")]:
            key = _norm(sf)
            if key and key not in seen:
                seen.add(key)
                forms.append(sf)
    return forms


def claimable_surface_form_groups(
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[list[str]]:
    """Every CLAIMABLE ledger entry's forms, ONE GROUP PER ENTRY (#386, E049).

    Same filtering as :func:`claimable_surface_forms`, but the row structure is
    preserved: each group is ``[concept, *surface_forms]`` (deduped, order kept).
    A consumer adding forms to a rendered page must treat each group as ONE
    competence — charter run 10 shipped 'Dreischichtbetrieb' AND 'Schichtbetrieb'
    (sibling forms of one ledger row) as two skill tags because the flattened
    list makes every form an independent candidate.
    """
    groups: list[list[str]] = []
    claimable, _ = split_ledger_for_prompt(keyword_ledger)
    for entry in [e for e in claimable if not is_positioning_only(e)]:
        group: list[str] = []
        seen: set[str] = set()
        for sf in [entry.get("concept", "")] + list(entry.get("surface_forms") or []):
            key = _norm(sf) if isinstance(sf, str) else ""
            if key and key not in seen:
                seen.add(key)
                group.append(sf)
        if group:
            groups.append(group)
    return groups


def unsupported_claim_surface_forms(
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Flatten every surface form of every UNKNOWN-gap ledger entry.

    Used by the ATS audit's fourth quadrant (ADR-048 amended 2026-07-03, #117): a
    keyword PRESENT in the document whose only ledger backing is an honest gap is
    an unsupported claim and gets a truthfulness warning. De-duplicated, order
    preserved. ``None``/empty tolerant (legacy pre-E037 rows have no ledger).

    ``denied`` entries are EXCLUDED (ADR-048/059 amended 2026-07-27, PO decision).
    The quadrant matches by normalised substring, and a substring cannot see
    negation — so "I have not worked in BaFin supervision", the honest positioning
    sentence the amended prompts now ASK for, is indistinguishable here from a
    claim to have it. Every instrument in the pipeline that reads for MEANING
    already exempts a denial clause: the Oracle verdict path
    (``oracle/audit.py``, ``claim.is_denial``), the Oracle's own clause splitter
    (``oracle/extract.py::_is_pure_denial_clause``), the choice-grounding
    denial→affirmation pivot, and ``cross_document``'s ``bare_denial_of_claimable``
    (scoped to CLAIMABLE concepts). This quadrant was the sole outlier, and the
    only one of the five that cannot tell direction.

    Nothing is lost by the exclusion: ``_is_pure_denial_clause`` is scoped to a
    CLAUSE, so a document that positively claims a denied concept — or smuggles an
    affirmation alongside the denial — is still audited by the Oracle, which reads
    direction. The four-status split is what makes the exclusion expressible at
    all; before it, ``denied`` and ``gap`` were the same row.

    **An ADJACENT ``partial`` IS included, despite being claimable** (ADR-048
    amended 2026-08-13, #526). The 2026-07-27 clause-4(b) exemption
    (:func:`is_positioning_only`) is scoped to ABSENCE — the term must not be
    DEMANDED, and its absence is an honest gap rather than a surfacing miss. It
    was never meant to cover PRESENCE: the row's entire meaning is "the candidate
    does NOT have this term, they have the adjacent one", so the term appearing in
    the document is an unsupported claim by the row's own definition. This
    distinction became load-bearing when the same amendment's clause 1 started
    routing differently-named support to ``partial`` + ``adjacent_evidence``
    instead of leaving it as a ``gap``: excluding on ``claimable`` alone would
    have switched this quadrant off for exactly the concepts most exposed to
    over-claiming. The forms emitted are the JD's own term and its surface forms —
    never ``adjacent_evidence``, which names what the candidate genuinely has and
    which the writer was told to give prominence.
    """
    forms: list[str] = []
    seen: set[str] = set()
    for entry in keyword_ledger or []:
        if entry.get("status") == "denied":
            continue
        if entry.get("claimable") and not is_positioning_only(entry):
            continue
        for sf in entry.get("surface_forms") or [entry.get("concept", "")]:
            key = _norm(sf)
            if key and key not in seen:
                seen.add(key)
                forms.append(sf)
    return forms


def keyword_only_honest_gaps(keyword_ledger: list[dict[str, Any]] | None) -> list[str]:
    """Honest gaps that are pure ATS keywords (US204, ADR-048 §10).

    Returns the concepts of ledger entries that are NOT claimable AND carry no fit
    weight (keyword-only). Required/nice_to_have honest gaps already reach the
    interview via ``category_c`` (compute_match_score_from_ledger), so they are
    excluded here to avoid double-routing. Pure; tolerant of ``None``/empty.

    ``denied`` entries are EXCLUDED (#383, the fit-weighted twin of the same
    seam in ``match_score``). "Honest gap" here means **unknown** — no signal
    (ADR-048 §1 amended 2026-07-27); a denial is not an absence of signal but
    the candidate's answer. This list is the enrichment interview's routing
    input (``gap.askable_gap_inputs`` → ``cluster_gaps``), and re-asking a
    denied concept is precisely what ADR-059's 2026-07-26 amendment (clause 3)
    forbids of an automatic path — a denial is reversible only by an explicit
    candidate correction. Nothing is lost: the entry keeps ``status: "denied"``
    and reaches the writers through ``split_ledger_for_prompt``'s positioning
    block. ADR-062 clause 1/6: a status-enum read is a FACT, not a judgement.
    """
    return [
        entry.get("concept", "")
        for entry in (keyword_ledger or [])
        if not entry.get("claimable")
        and entry.get("status") != "denied"
        and not entry.get("fit_weight")
        and entry.get("concept")
    ]


def render_ledger_reviewer_block(
    keyword_ledger: list[dict[str, Any]] | None,
) -> str:
    """Render the Keyword Ledger as a block appended to the REVIEWER source (ADR-048 §8 /
    US202).

    The reviewer reads this to perform two checks that the bounded ADR-047 refine loop
    then acts on (no new loop, no forced injection):
      * report which CLAIMABLE keywords are ABSENT from the draft, and
      * flag any forbidden honest-gap concept that appears as a claim.

    Grounding still strictly OUTRANKS coverage — an absent-claimable note is a *surfacing*
    suggestion, never licence to fabricate. Returns "" for an empty/legacy ledger.
    """
    claimable, forbidden = split_ledger_for_prompt(keyword_ledger)
    if not claimable and not forbidden:
        return ""

    lines: list[str] = [
        "KEYWORD LEDGER (ADR-048) — for your two ledger checks. Grounding strictly "
        "OUTRANKS coverage: an absent claimable keyword is a surfacing suggestion only, "
        "NEVER a reason to fabricate or stretch.",
        "",
        "CLAIMABLE KEYWORDS (the candidate truthfully supports these). Reference list for "
        "your grounding judgments — do NOT scan the draft for absent ones yourself; a "
        "deterministic VERIFIED COVERAGE CHECK handles absence detection (US213, #122):",
    ]
    if claimable:
        for entry in claimable:
            forms = ", ".join(entry.get("surface_forms") or [entry.get("concept", "")])
            lines.append(f"  - {entry.get('concept', '')} [forms: {forms}]")
    else:
        lines.append("  (none)")

    lines += [
        "",
        "DO NOT CLAIM (honest gaps — NOT in the profile). Flag any of these that the draft "
        "presents as something the candidate has, has done, or knows — that is a fabrication. "
        "Whose fact? Judge the SENTENCE, not the term (#420): a sentence stating the "
        "EMPLOYER's product/market from the job_description (\"Ihr Umfeld der ...\", their "
        "domain), or naming the candidate's OWN honest absence of the term, is NOT a claim "
        "to it and must never be flagged:",
    ]
    if forbidden:
        for concept in forbidden:
            lines.append(f"  - {concept}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def _draft_strings(node: Any) -> list[str]:
    """Every string value in a draft document dict, however deeply nested."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _draft_strings(v)]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _draft_strings(v)]
    return []


def verified_missing_claimable(
    draft: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Claimable ledger entries verifiably ABSENT from the draft (US213, #122).

    Runs THE shared presence predicate (US212, ats_audit.surface_present) over the
    serialised draft text — the same instrument the ATS panel grades with, so the
    pipeline can no longer ship a document its own panel will flag. Deterministic,
    no LLM. Honest-gap entries are never reported (they must stay absent).

    An ADJACENT ``partial`` is never reported either (ADR-048 amended
    2026-07-27). Such an entry means the candidate does NOT have the named
    thing and has something else that stands in for it, so demanding its JD
    term appear literally is a demand to over-claim — the adjacent capability
    belongs on the page instead. Charter run #7 exhausted the CV reviewer's
    entire retry budget on exactly this pressure (`Payments platform`,
    `Settlement pipeline`, `Payout flows`). A below-the-bar ``partial`` carries
    no pointer and is still demanded: the candidate really does have that
    skill, just less of it than the JD asked for.
    """
    return _coverage_split(draft, keyword_ledger)[1]


def claimable_present_entries(
    draft: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Claimable ledger entries the draft verifiably ALREADY surfaces (#306).

    The exact complement of :func:`verified_missing_claimable` over the same
    universe and the same presence predicate — one scan, one definition
    (ADR-066), so the "already covered" half can never disagree with the
    "still absent" half about a term.
    """
    return _coverage_split(draft, keyword_ledger)[0]


def _coverage_split(
    draft: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(present, missing)`` claimable entries for a draft — THE single
    coverage scan both halves read (see the two callers above)."""
    from applire.services.ats_audit import _norm as ats_norm, surface_present

    claimable, _ = split_ledger_for_prompt(keyword_ledger)
    # ADR-069: a scope entry's concept embeds the JD's own figure — demanding
    # it verbatim would force the number into the document (see is_scope_entry).
    claimable = [
        e for e in claimable if not is_positioning_only(e) and not is_scope_entry(e)
    ]
    if not claimable:
        return [], []
    text_norm = ats_norm("\n".join(_draft_strings(draft)))
    present: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for entry in claimable:
        forms = list(entry.get("surface_forms") or [])
        if entry.get("concept"):
            forms.append(entry["concept"])
        (present if any(surface_present(f, text_norm) for f in forms) else missing).append(entry)
    return present, missing


# ── #315 — the shared "load-bearing claim" vocabulary ──────────────────────
# Charter run #7 case 2 (operations_marcus_de, DE): the ledger concept
# "Budget- und Investitionsverantwortung" (direct, claimable, fit_weight:
# 1.0) reached the delivered CV as a bare keyword ("Budgetverantwortung" in
# the summary sentence and the skills list), never as a narrative bullet
# carrying its quantified evidence ("Budgetverantwortung ca. 6 Mio. €
# (Personal, Instandhaltung, Material-Gemeinkosten)."). Two blind hiring
# reviewers scored the requirement unmet for want of exactly that number.
#
# Root cause pinned against ground truth (backend/logs/llm/2026-07-27.jsonl,
# 12:03-12:05 UTC): the CV reviewer's OWN round-1 issue named the missing
# bullet by text; round-1's generator never added it; round-2 approved
# anyway. Neither the deterministic coverage gate (``coverage_reviewer_
# prompt_fn``) nor the #234 restoration guard (``services.cv._restore_
# ledger_bullets``) ever flagged the drop, because both key on
# ``verified_missing_claimable`` above, which scans the WHOLE serialised
# draft -- including ``skills`` and ``summary`` -- for a bare surface-form
# match. "Budgetverantwortung" satisfied that scan from the very first
# draft onward. ``cv_budget.condense_to_budget`` never ran on this bullet
# at all -- it never existed in ``tailored_data`` to condense; the
# briefed "condense cut it" hypothesis is DISPROVED.
#
# ``is_load_bearing``/``verified_missing_load_bearing`` are THE shared
# vocabulary for "a claim that must never be reduced to a bare keyword by
# any cutting/trimming/restoring step" -- reused verbatim by the cover-
# letter chain's own guard (#306) so the two document chains never grow two
# private notions of load-bearing that drift apart.


# THE shared load-bearing predicate lives in ``services/load_bearing.py`` so the
# CV chain (#315, below) and the cover-letter chain (#306, the substitution
# guard) can never grow two notions of "load-bearing" that drift apart. It is
# re-exported here because every caller on this side already reasons in ledger
# vocabulary; there is exactly ONE definition, in that module.
from applire.services.load_bearing import is_load_bearing  # noqa: E402,F401


def _tailored_narrative_texts(draft: dict[str, Any] | None) -> list[str]:
    """Every WORK-HISTORY narrative string in a tailored CV draft: role
    bullets and nested project bullets ONLY. Deliberately excludes
    ``skills``, ``summary``, contact, education, certifications, languages
    -- a bare keyword tag or a one-line elevator pitch is not a story a
    hiring reviewer will credit for a quantified claim (mirrors #260's
    ``_narrative_texts`` vault-side scoping rule, applied here to the
    DELIVERED draft instead of the vault).
    """
    if not draft:
        return []
    texts: list[str] = []
    for entry in draft.get("work_history") or []:
        if not isinstance(entry, dict):
            continue
        texts.extend(s for s in (entry.get("bullets") or []) if isinstance(s, str))
        for proj in entry.get("projects") or []:
            if not isinstance(proj, dict):
                continue
            texts.extend(s for s in (proj.get("bullets") or []) if isinstance(s, str))
    return texts


def tailored_narrative_corpus(draft: dict[str, Any] | None) -> str:
    """The tailored draft's NARRATIVE-bearing text only, flattened +
    normalised (#315) -- the DELIVERED-side twin of
    :func:`profile_narrative_corpus`."""
    if not draft:
        return ""
    from applire.services.ats_audit import _norm as ats_norm

    return ats_norm(" ".join(_tailored_narrative_texts(draft)))


def verified_missing_load_bearing(
    draft: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Load-bearing (#315) ledger entries whose backing FIGURE is absent from
    the draft's own NARRATIVE (work_history + nested project bullets).

    An entry is load-bearing precisely because its vault evidence carries a
    percent or currency figure (:func:`applire.services.load_bearing.is_load_bearing`),
    so the figure is what must be shown to have survived. Reported when NONE of
    the entry's backed figures appear in the narrative corpus -- deliberately
    permissive about partial retention (an arc that keeps one of its two
    endpoints is not the defect this guard exists for) and about phrasing (the
    candidate's number in the writer's own words has retained the claim).

    **Corrected 2026-07-28, charter run #9 -- the predicate, not just the corpus.**
    #315 correctly diagnosed that a bare keyword in ``skills``/``summary`` was
    satisfying :func:`verified_missing_claimable`, and narrowed the corpus to
    narrative text. But it kept asking ``surface_present`` over
    :func:`retention_forms`, which holds only the concept and its surface forms
    and no figure at all. So a delivered bullet reading exactly
    ``"Budgetverantwortung"`` -- with the ``6 Mio. €`` gone -- satisfied the
    guard, and the run-#7 defect reproduced verbatim in run #9 with the guard
    in place: ``is_load_bearing`` True, ``verified_missing_load_bearing`` empty.
    Moving the corpus without changing the predicate left the
    bare-token-satisfies-coverage mechanism intact one layer down. The reviewer
    had named the missing figure at attempt 1; nothing downstream could see it.

    Known limit, stated rather than hidden: :func:`figures_present` scans the
    whole narrative corpus, so a coincidentally equal figure elsewhere in the
    document (the ``14 Mitarbeitende`` / ``14 Jahre`` collision class of #214
    and #299) can mask a genuine loss. That fails toward silence, which is the
    safe direction here and strictly better than the unconditional silence it
    replaces; attribution belongs to the guards that own it.

    ADR-062 classification: **fact.** A figure is extracted by the canonical
    detector and presence is set membership over canonical ``kind:value`` keys.
    Nothing here reads prose for meaning.
    """
    load_bearing = [e for e in (keyword_ledger or []) if is_load_bearing(e)]
    if not load_bearing:
        return []
    from applire.services.load_bearing import (
        figures_present,
        load_bearing_universe_from_ledger,
    )

    present = figures_present(tailored_narrative_corpus(draft))
    missing: list[dict[str, Any]] = []
    for entry in load_bearing:
        universe = load_bearing_universe_from_ledger([entry])
        if universe and not (universe & present):
            missing.append(entry)
    return missing


# ── ADR-076 clause 6 (#543) — coverage yields to rank under the length budget ──
#
# Run A of the 2026-08-14 model comparison (letter chain, real provider):
# the coverage check demanded two NEW claimable keywords every round while the
# corrector's insertions displaced earlier ones — att1 SAP+Shopfloor -> att2
# 5S+Arbeitssicherheit -> att3 Arbeitsvorbereitung+Fuehrungserfahrung -> att4
# Budgetplanung+Supply Chain. Pure displacement churn, never convergence
# (#525's mechanism). Root cause per ADR-076/ADR-048 (amended 2026-08-15):
# coverage and the ADR-042/051 length budget were two absolutes with no
# arbiter — the ledger's OWN ``fit_weight`` (required=1.0 > nice_to_have=0.5 >
# keyword-only=0.0, ADR-048 clause 1) is the one ranking this module already
# carries, so it is reused here rather than invented: a below-rank absence
# stops being commanded once the draft has reached its length budget, because
# cutting by rank is the writer/corrector's legal move (the friend model's
# step 5, ADR-076 Context).
#
# This is deliberately NOT a new deterministic gate on ``approved`` (the
# 2026-08-13 precedent, restated by ADR-076's own consequences section): it
# only changes WHICH facts the coverage demand asserts into the prompt as
# blocking. A below-rank concept is still visible to the writer/corrector —
# it never left the CLAIMABLE KEYWORDS reference list :func:`render_ledger_reviewer_block`
# renders — only the "you MUST set approved=false" command for it is
# withheld. Nothing here overrides a reviewer verdict.


@dataclass(frozen=True)
class CoverageBudget:
    """The ADR-042/051 length budget, expressed in whatever unit the caller
    already measures its OWN draft in — bullets for the CV, words for the
    letter. This module invents no length metric of its own: ``capacity`` and
    ``measure`` are built from the existing ADR-051 budget objects by
    :func:`cv_coverage_budget` / :func:`letter_coverage_budget` below, never
    computed here.
    """

    capacity: int
    measure: Callable[[dict[str, Any]], int]

    def under_pressure(self, draft: dict[str, Any]) -> bool:
        """True once the CURRENT draft's own occupancy has reached the
        budget's capacity. Recomputed every round from the draft actually
        being reviewed — exactly like :func:`verified_missing_claimable` —
        never estimated once before generation."""
        if self.capacity <= 0:
            return True
        return self.measure(draft) >= self.capacity


def cv_coverage_budget(budget: Any) -> "CoverageBudget | None":
    """ADR-076 clause 6 — the CV's coverage budget. ``budget`` is the
    ``cv_budget.BudgetResult`` that ``compute_bullet_budgets`` already
    computes for this generation (ADR-051 §3): the SAME per-role bullet
    ceilings ``cv_budget.condense_to_budget`` enforces after render, so the
    coverage demand and the length guarantee read one number, not two
    (ADR-066). ``None`` when no budget was computed (a legacy call site) —
    the gate then stays fully open, i.e. today's behaviour: it can never make
    a coverage demand MORE aggressive than before this clause.
    """
    if budget is None:
        return None
    capacity = sum(rb.max_bullets for rb in budget.roles.values())
    return CoverageBudget(
        capacity=capacity,
        measure=lambda draft: len(_tailored_narrative_texts(draft)),
    )


def letter_coverage_budget(word_budget: int | None) -> "CoverageBudget | None":
    """ADR-076 clause 6 — the letter's coverage budget, in words: the SAME
    ``RegionNorm.letter_body_word_budget`` the word-floor/word-ceiling
    reviewer wrappers (``cover_letter_positioning``) already enforce, so
    coverage and the letter's own length guarantee read one budget.
    """
    if not word_budget:
        return None
    from applire.services.cover_letter_positioning import body_word_count

    return CoverageBudget(capacity=word_budget, measure=body_word_count)


def rank_gate_missing_claimable(
    missing: list[dict[str, Any]],
    draft: dict[str, Any],
    budget: "CoverageBudget | None",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """ADR-076 clause 6 — split verified-missing claimable entries into
    ``(blocking, below_rank)`` by the ledger's own ``fit_weight``, under the
    ADR-042/051 length budget.

    Absent a budget, or while the draft still has room in it, every missing
    claimable entry stays blocking — today's behaviour, unchanged. Once the
    draft has reached its budget, a ``required`` absence
    (``fit_weight == REQUIRED_WEIGHT``, the JD's own stated requirement)
    still blocks: rank puts it first, so it never loses the budget fight
    while there is competition for the same space — the friend model's
    "prioritise and cut" trims the LOW end, not the top. A
    ``nice_to_have``/keyword-only absence below that rank becomes
    ``below_rank``: its absence is no longer a blocking issue, because
    cutting it is the writer/corrector's legal move.
    """
    if budget is None or not budget.under_pressure(draft):
        return list(missing), []
    blocking = [e for e in missing if (e.get("fit_weight") or 0) >= REQUIRED_WEIGHT]
    below_rank = [e for e in missing if (e.get("fit_weight") or 0) < REQUIRED_WEIGHT]
    return blocking, below_rank


def render_verified_coverage_block(entries: list[dict[str, Any]]) -> str:
    """Render the verified-absent claimable entries for the REVIEWER (US213, #122).

    This replaces the reviewer's own coverage *detection* (US202) with ground truth:
    the list is the output of a deterministic literal check, not something to
    re-derive. The reviewer's only coverage judgment left is the grounding waiver
    (ADR-048 §8 — grounding strictly outranks coverage). Returns "" when empty.
    """
    if not entries:
        return ""
    lines = [
        "VERIFIED COVERAGE CHECK (deterministic literal scan — this is ground truth, do "
        "not re-derive it). The following claimable keywords are ABSENT from the draft "
        "in every known surface form:",
    ]
    for entry in entries:
        forms = ", ".join(entry.get("surface_forms") or [entry.get("concept", "")])
        evidence = entry.get("evidence", "")
        lines.append(f"  - {entry.get('concept', '')} [forms: {forms}] — profile evidence: {evidence}")
    lines += [
        "",
        "You MUST set approved=false while any term above remains both absent and "
        "un-waived, and name the terms in your issues so the writer surfaces them from "
        "the profile evidence given. EXCEPTION — the grounding waiver: if surfacing a "
        "term would stretch beyond its stated evidence, WAIVE it instead (name the term "
        "and the reason in your feedback); a waived term does not block approval. "
        "Grounding strictly outranks coverage — never ask the writer to fabricate.",
    ]
    return "\n".join(lines)


def coverage_reviewer_prompt_fn(
    base_fn,
    keyword_ledger: list[dict[str, Any]] | None,
    budget: "CoverageBudget | None" = None,
):
    """Wrap a reviewer_prompt_fn so every review sees the CURRENT draft's verified
    coverage state (US213, #122).

    review_and_refine calls reviewer_prompt_fn(source, draft) each iteration with the
    latest draft, so the verified list is recomputed per pass and the block disappears
    once the refiner has surfaced the terms — deterministic convergence signal riding
    the existing bounded ADR-047 loop (no new loop).

    ADR-076 clause 6 (#543): ``budget``, when given (build with
    :func:`cv_coverage_budget` / :func:`letter_coverage_budget`), rank-gates the
    demand under the ADR-042/051 length budget — see
    :func:`rank_gate_missing_claimable`. ``None`` (the default) reproduces
    today's behaviour exactly: every missing claimable entry blocks.
    """

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        missing = verified_missing_claimable(draft, keyword_ledger)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, budget)
        if below_rank:
            logger.info(
                "ADR-076 clause 6: %d claimable term(s) below rank under the length "
                "budget — not raised as blocking (cutting by rank is legal): %s",
                len(below_rank),
                [e.get("concept", "") for e in below_rank],
            )
        if blocking:
            logger.info(
                "verified coverage check: %d claimable term(s) absent from draft: %s",
                len(blocking),
                [e.get("concept", "") for e in blocking],
            )
            prompt = f"{prompt}\n\n{render_verified_coverage_block(blocking)}"
        return prompt

    return fn


# ── ADR-021 amended 2026-08-13, clause 4 (#531): the DO-NOT-CLAIM presence fact ──


def forbidden_terms_in_draft(
    draft: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """DO-NOT-CLAIM concepts THE shared presence predicate finds in this draft.

    The positive half of the ledger's forbidden list, computed with the same
    instrument as its claimable twin (:func:`verified_missing_claimable`):
    ``ats_audit.surface_present`` over the serialised draft (US212 / ADR-048).
    Membership of the forbidden list itself stays with
    :func:`split_ledger_for_prompt` — one definition, not two (ADR-066).

    ADR-062 classification: **FACT.** Literal presence of a surface form in a
    text is the same class the VERIFIED COVERAGE CHECK already states. Whether a
    present term is used honestly — the SUBJECT TEST, possession versus
    aspiration — is the reviewer's judgement and is deliberately not computed.

    **Direction matters, and only one direction is fact-grade here.** The fold
    behind ``surface_present`` is a conservative ENGLISH verb-form fold, so a
    German inflection or compound ("Digitalisierungsprojekte", "digitalisiert")
    can defeat it. A term this function returns IS in the draft; a term it does
    not return is one the scan did not find, which is not the same as absent —
    :func:`render_forbidden_presence_block` states exactly that, and keeps a
    missed form raisable at the price of quoting the draft.

    Why it exists (gate charter run 1, #531): 2 of the 3 DO-NOT-CLAIM findings
    named a term appearing nowhere in the graded draft. The reviewer prompt asks
    a usage-honesty question that presupposes a presence determination, while
    forbidding the model from performing literal string matching to answer it.
    A prohibition is not a substitute for supplying the answer.
    """
    from applire.services.ats_audit import _norm as ats_norm, surface_present

    _, forbidden = split_ledger_for_prompt(keyword_ledger)
    if not forbidden:
        return []
    forms_by_concept: dict[str, list[str]] = {}
    for entry in keyword_ledger or []:
        concept = entry.get("concept")
        if not concept:
            continue
        forms_by_concept.setdefault(concept, []).extend(
            f for f in (entry.get("surface_forms") or []) if f
        )
    text_norm = ats_norm("\n".join(_draft_strings(draft)))
    return [
        concept
        for concept in forbidden
        if any(
            surface_present(form, text_norm)
            for form in [concept, *forms_by_concept.get(concept, [])]
        )
    ]


def render_forbidden_presence_block(present_terms: list[str]) -> str:
    """The reviewer's DO-NOT-CLAIM PRESENCE block — the fact, then the two
    judgements that remain (ADR-021 amended 2026-08-13, clauses 4 and 5).

    Unlike the coverage block, this one is rendered even when the scan found
    NOTHING: "no forbidden term appears in this draft" is precisely the fact
    #531's two spurious findings needed. Callers gate on the ledger having a
    forbidden list at all (:func:`forbidden_presence_reviewer_prompt_fn`).
    """
    lines = [
        "DO-NOT-CLAIM PRESENCE (deterministic literal scan of THIS draft — this is "
        "ground truth, do not re-derive it). Of the DO NOT CLAIM terms above, the "
        "scan finds these in the draft:",
    ]
    if present_terms:
        lines += [f"  - {term}" for term in present_terms]
    else:
        lines.append("  (none — the scan finds no DO NOT CLAIM term in this draft.)")
    lines += [
        "",
        "Presence is therefore settled and is NOT yours to determine. For a term "
        "listed above, judge ONLY how it is used: the SUBJECT TEST decides whose "
        "fact the sentence states, and in a sentence about the CANDIDATE the term "
        "may appear as an ASPIRATION (wanting to grow into it) but never as a "
        "POSSESSION (asserting it is already held). Never file a DO-NOT-CLAIM "
        "issue about a term that is not listed above and that you cannot QUOTE "
        "from the draft.",
        "The scan folds ENGLISH verb forms only, so a German inflection or "
        "compound can defeat it. If a DO NOT CLAIM term really does appear in a "
        "form the scan missed, you may still raise it — your issue MUST then "
        "quote the exact words of the draft that carry it.",
    ]
    return "\n".join(lines)


def forbidden_presence_reviewer_prompt_fn(base_fn, keyword_ledger: list[dict[str, Any]] | None):
    """Wrap a ``reviewer_prompt_fn`` so every round carries which DO-NOT-CLAIM
    terms the CURRENT draft actually contains (ADR-021 amended 2026-08-13).

    Composes with (never replaces) the coverage, unaddressed-requirement, word-
    floor and figure-ownership wrappers, exactly the way they compose with each
    other: ``review_and_refine`` calls ``reviewer_prompt_fn(source, draft)``
    fresh each round, so the block tracks the draft the corrector just produced.
    No new LLM call, no new pass, no new loop (ADR-058 freeze).
    """

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        _, forbidden = split_ledger_for_prompt(keyword_ledger)
        if not forbidden:
            return prompt
        present = forbidden_terms_in_draft(draft, keyword_ledger)
        logger.info(
            "do-not-claim presence check (#531): %d of %d forbidden term(s) "
            "present in the draft: %s",
            len(present),
            len(forbidden),
            present,
        )
        return f"{prompt}\n\n{render_forbidden_presence_block(present)}"

    return fn


def render_coverage_retention_block(entries: list[dict[str, Any]]) -> str:
    """Render, for the CORRECTOR, the claimable terms the draft it is patching
    ALREADY surfaces (#306). Returns "" when empty.

    The reviewer half of this scan has existed since US213: every round,
    :func:`verified_missing_claimable` is computed and injected into the
    REVIEWER prompt as ground truth. The corrector was never told the other
    half, and a corrector rewrite is where coverage is lost — so the loop
    re-demanded, round after round, terms an earlier draft already carried.

    ADR-062 clause 4: this block and :func:`render_verified_coverage_block`
    reach the same loop, so they state ONE precedence in the same words —
    grounding strictly outranks coverage. A term is never kept by writing
    something untrue; the reviewer's grounding complaint about a claim always
    wins over this retention floor.
    """
    if not entries:
        return ""
    forms = []
    for entry in entries:
        surface = ", ".join(entry.get("surface_forms") or [entry.get("concept", "")])
        forms.append(f"  - {entry.get('concept', '')} [forms: {surface}]")
    return "\n".join(
        [
            "COVERAGE ALREADY ACHIEVED (deterministic literal scan of the PREVIOUS "
            "OUTPUT above — this is ground truth, do not re-derive it). The draft you "
            "are patching already surfaces these claimable keywords:",
            *forms,
            "",
            "Your corrected draft MUST still contain every one of them. Fixing what "
            "the review flagged is not a licence to drop a term that is already on "
            "the page — a rewritten sentence must carry its grounded content over. "
            "EXCEPTION — the grounding waiver: if the review shows a claim is "
            "ungrounded, correct the claim; grounding strictly outranks coverage, and "
            "no term may be kept by writing something untrue.",
        ]
    )


def coverage_corrector_prompt_fn(base_fn, keyword_ledger: list[dict[str, Any]] | None):
    """Wrap a ``generator_prompt_fn`` so every CORRECTOR retry sees the coverage
    the draft it is patching already holds (#306 — the loop's twin of
    :func:`coverage_reviewer_prompt_fn`).

    Evidence (``backend/logs/llm/2026-08-06.jsonl``, chain ``cover_letter``,
    13:57–14:05 UTC, real provider): the deterministic coverage scan reported
    ``Shopfloor-Management, Deutsch, SAP MM, Englisch`` at round 1, ``Deutsch,
    Englisch`` at round 2 — and ``SMED, KVP`` at round 3. Neither had ever been
    demanded; both were present in drafts 0 and 1. Round 2's reviewer had asked
    for an employer anchor on one sentence, and the corrector's rewrite of that
    sentence deleted the clause carrying ``KVP`` (with ``4,1 % -> 2,3 %``) and
    the sentence carrying ``SMED`` (with ``87 % -> 96 %``). Both terms were in
    the ``PREVIOUS OUTPUT`` block of that very prompt, verbatim: seeing content
    quoted back is not an instruction to keep it. Rounds 3–4 were then spent
    recovering ground draft 1 already held, and the chain exhausted at 5/5. Five
    further chains on 2026-08-02/06 show the same signature.

    ``review_and_refine`` calls ``generator_prompt_fn(previous_draft, feedback,
    source)`` on every retry, so the list is recomputed per round against the
    draft actually being patched and disappears as soon as there is nothing to
    retain. Deterministic (ADR-062 clause 1: literal presence is a FACT), no new
    LLM call and no new pass — the same instrument already computed for the
    reviewer, threaded into the prompt that can act on it (ADR-058 freeze).
    """

    def fn(previous_draft: dict[str, Any], feedback: str, source: str) -> str:
        prompt = base_fn(previous_draft, feedback, source)
        present = claimable_present_entries(previous_draft, keyword_ledger)
        if present:
            logger.info(
                "coverage retention: %d claimable term(s) already surfaced in the "
                "draft being corrected: %s",
                len(present),
                [e.get("concept", "") for e in present],
            )
            prompt = f"{prompt}\n\n{render_coverage_retention_block(present)}"
        return prompt

    return fn


def _entry_is_denied(
    concept: str,
    forms: list[str],
    denials: list[str],
    corpus: str | None,
) -> bool:
    """Is a ledger ENTRY (its concept or any surface form) denied, judged
    against ``corpus``?

    ADR-062 clause 6 classification: a **fact** under clause 5's fail-safe
    scrubbing exemption — string matching against the model's own declared
    ``denials``, never a reading of prose. It decides nothing about whether a
    sentence IS a denial (ADR-059 clause 4 keeps that with the LLM); it only
    matches and enforces what the reconciler already flagged.

    Mirrors ``_enforce_denial_stance``'s call shape exactly: ONE call against
    the FULL joint denial list per corpus, so ``_independently_affirmed`` can
    blank every denied phrase out of the corpus TOGETHER before looking for
    independent evidence.
    """
    return is_denied_concept(concept, denials, corpus) or any(
        is_denied_concept(f, denials, corpus) for f in forms
    )


def upgrade_ledger_for_concepts(
    keyword_ledger: list[dict[str, Any]] | None,
    concepts: list[str],
    evidence: str,
    *,
    status: str = "direct",
    denied_concepts: list[str] | None = None,
    upgrade: bool = True,
    vault_corpus: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Deterministically UPGRADE honest-gap entries the interview just confirmed (#188).

    A gap interview turn ADDRESSED a gap whose cluster owns ``concepts`` (the exact
    ledger concept strings — ``GapClusterSchema.gaps``). Flip each matching
    NON-claimable entry to claimable with the given ``status`` and the answer text
    as ``evidence``, so the CV and cover-letter generators (which both read this one
    persisted ``GapAnalysis.keyword_ledger`` row) surface it as a supported strength
    instead of hedging it as a growth area ("I am eager to grow into …").

    Matching reuses the same normalised substring logic ``build_keyword_ledger``
    uses (``_norm`` / ``_matches``) — a cluster concept may map to MULTIPLE entries.

    Conservative (this touches truthfulness):
      * only NON-claimable entries whose concept normalize-matches a cluster concept
        are touched — every other entry is copied through untouched;
      * NEVER creates a new entry from an unmatched concept (a reworded/translated
        cluster simply no-ops);
      * the honest-gap ``evidence`` (stripped to "" by the builder) is replaced with
        the answer text so the generator has something to ground the surfacing on.

    POLARITY (ADR-059 amended 2026-07-27 — the run-#7 blocker). "The cluster was
    addressed" is NOT "the candidate has it": an interview turn addresses a gap
    just as much by denying it. Charter run #7 persisted eight denied concepts at
    ``status="direct", claimable=True`` **with the candidate's own denial sentence
    as their backing evidence**, because this function had no notion of polarity
    and the ADR-059 floor (:func:`_enforce_denial_stance`) runs only inside
    ``build_keyword_ledger`` — never at this in-place seam. Two floors close it:

      * an entry already at ``status == "denied"`` is never touched, whatever the
        caller passes — the persisted status is authoritative on its own;
      * ``denied_concepts`` (the candidate's live ``ProfileMetadata.denied_concepts``)
        is consulted through the SAME predicate the same-turn reconciler guard and
        the durable floor use (``is_denied_concept``), so the three can never
        disagree. A matching concept is **recorded as denied** rather than merely
        skipped — a denial is a real answer and must move the requirement's status,
        which is the whole point of the amendment.

    REVERSAL (#352). Both floors used to sit BEHIND the upgrade's own eligibility
    filter — a candidate entry had to be ``not claimable`` before either could
    look at it — so the floor could stop an upgrade in flight and never reverse
    one an earlier turn had already committed. The durable floor
    (:func:`_enforce_denial_stance`) has always reversed a claimable entry (it
    logs a warning when it does), so the two instruments ADR-059 clause 3
    requires to agree disagreed: rebuilding the row produced ``denied``, this
    seam kept ``direct``. Polarity is now evaluated for EVERY concept-matching
    entry, and "already claimable" only bars the UPGRADE — never the floor.

    ``upgrade`` is the gate the doors set from their own addressed-check
    (``bool(applied.changes)``, the #188 addressed-gate). ``False`` means this
    turn applied no ops and therefore confirmed nothing, so nothing may be
    upgraded — but its denials must still be able to reverse. Without the
    separation the doors had to choose between running the floor for a
    denial-only turn (and letting it upgrade an undenied concept off no
    evidence) and not running it at all; they chose the latter, which is #352.

    GROUNDING THE CONTAINMENT BRANCH (#351). ``is_denied_concept``'s second
    branch — the ledger concept is a bounded substring of a denied compound
    ("CSS" ⊂ a denied "Tailwind CSS") — is a *containment* test standing in
    for a question no string comparison answers ("does denying the compound
    deny the head noun?"). It fail-closes to True when no grounding corpus is
    passed, and floor 2 passed none, so a candidate who affirmed CSS and
    denied Tailwind CSS in one sentence had CSS written to
    ``status="denied"`` with :data:`DENIED_EVIDENCE` — a claim about their
    testimony they never made, rendered verbatim to the letter writer
    ("THE CANDIDATE WAS ASKED AND STATED THEY DO NOT HAVE THIS",
    ``cross_document``), and terminal (floor 1 blocks any later upgrade and
    :func:`reevaluate_gap_ledger_against_vault` only re-examines ``"gap"``
    rows). ADR-064's 2026-07-29 amendment requires the carve-out to be
    applied "consistently in all three places that independently re-implement
    'never upgrade a denied concept' … or the floor becomes inconsistent by
    call path"; this was the one place it was not. Two corpora, because this
    seam is the only one of the three that writes the turn's own words as
    evidence:

      * **the turn** (``evidence`` — the answer/statement being recorded)
        decides whether this seam may UPGRADE a contained concept. Only a
        concept the candidate affirms *outside* every denied compound, in
        their own words, on this turn, may be flipped claimable with those
        words as its evidence.
      * **``vault_corpus``**
        (:func:`applire.services.profile.reconcile.stance.denial_release_corpus`,
        threaded from both doors — the same input ``_enforce_denial_stance``
        takes) decides
        whether a denial may be RECORDED (and, since #352, whether one may be
        REVERSED). Vault evidence outside the denied compound contradicts the
        containment reading, so the entry is left exactly as it stands — not
        upgraded (the turn is not evidence for it), not denied and not
        reversed (the candidate denied the compound, not the head noun). An
        open entry stays a ``gap``, still healable by the corpus-aware vault
        re-evaluation; an already-claimable one keeps the standing it earned.
        ``None`` (no profile on hand) keeps the pre-#351 fail-closed default
        for the vault half.

    The narrowing is confined to the containment branch. A denial that NAMES
    the concept, or a broader denial the concept falls under ("Azure" denying
    "Microsoft Azure"), is the candidate's own declaration and is absolute
    regardless of either corpus — including #352's reversal of a claimable
    entry (ADR-040 never-claim; ADR-062 clause 5 keeps that floor
    deterministic and deliberately over-broad).

    WHAT A CONTAINMENT-ONLY MATCH MAY WRITE (ADR-059 amended 2026-08-08, #486).
    The verdicts above are unchanged; what changes is the WRITE. Recording
    ``denied`` + :data:`DENIED_EVIDENCE` is testimony, and only the DECLARED
    denial (:func:`_declared_denial_level`) licenses it. A containment-only
    match with nothing affirming the concept is floored through
    :func:`_floored_row` instead — non-claimable, honest about being a floor,
    silent about a statement that was never made. Both writes go through the
    ONE shared helper this seam, the rebuild floor and #318's heal share, so
    the three instruments cannot drift apart (clause (b) of that amendment).

    HOW #351 AND #352 COMPOSE. #352 widened WHICH entries reach floor 2 (every
    concept-matching one, claimable included) and #351 narrowed WHAT floor 2
    concludes about them, so the two are orthogonal and both hold:

      * a declared denial reverses a claimable entry exactly as #352 requires;
      * a containment-only denial the vault contradicts reverses nothing — and
        that is what makes #352's own invariant TIGHTER, not looser, because
        ``_enforce_denial_stance`` (the rebuild this seam must agree with) has
        always judged containment against ``vault_corpus``. Before #351 this
        seam judged it corpus-blind, so a rebuild and a retraction turn could
        still disagree on exactly the CSS/Tailwind-CSS shape;
      * ``upgrade=False`` (a turn that applied no ops) and the containment
        carve-out are independent gates: a retraction turn can still reverse a
        declared denial, and still may not invent one from containment.

    Returns ``(new_ledger, changed)``; ``changed`` False means the caller should skip
    the JSONB write. Pure; tolerant of ``None``/empty.
    """
    if not keyword_ledger or not concepts:
        return list(keyword_ledger or []), False
    concept_norms = [_norm(c) for c in concepts if _norm(c)]
    if not concept_norms:
        return list(keyword_ledger), False

    upgrade_status = status if status in {"direct", "partial"} else "direct"
    ev = (evidence or "").strip()
    # ADR-064/#486 — the SAME dict-or-str normaliser the rebuild floor uses, so
    # this seam can assert the durable denial's own ``denial_level`` instead of
    # guessing "direct". A bare ``list[str]`` caller keeps working unchanged.
    denial_entries = _denied_concept_entries(denied_concepts)
    denials = [d_concept for d_concept, _level in denial_entries]

    # #351 — the two grounding corpora (see the docstring). Both are built
    # HERE, from this function's own arguments plus the one optional caller
    # input, so every door gets identical behaviour by construction and no
    # door can quietly ground the floor differently (ADR-066: one logical
    # operation, one implementation).
    from applire.services.ats_audit import _norm as ats_norm

    turn_corpus = ats_norm(ev)
    full_corpus = " ".join(p for p in (vault_corpus or "", turn_corpus) if p) or None

    new_ledger: list[dict[str, Any]] = []
    changed = False
    for entry in keyword_ledger:
        e = dict(entry)
        # ADR-069: a scope entry is excluded from this seam — "the cluster was
        # addressed" must not flip a quantified bar to claimable with a raw
        # answer as its evidence. The answer lands in the vault through the
        # normal testimony rail and the bar is re-judged at the next gap
        # analysis (the ADR-059 every-seam rule, applied to bars).
        if is_scope_entry(e):
            new_ledger.append(e)
            continue
        concept_norm = _norm(e.get("concept", ""))
        # #352 — the claimable check is NOT part of the match. An entry an
        # earlier turn upgraded must still reach both floors below; it is only
        # barred from being upgraded AGAIN (after the floors, below).
        matched = concept_norm and any(
            _matches(concept_norm, cn) for cn in concept_norms
        )
        if not matched:
            new_ledger.append(e)
            continue

        # Floor 1 — the persisted status is authoritative.
        if e.get("status") == "denied":
            new_ledger.append(e)
            continue

        concept = e.get("concept", "")
        forms = e.get("surface_forms") or [concept]
        # Floor 2 — the candidate's live denials, via the shared predicate.
        # Runs for a claimable entry too (#352): this is the seam that REVERSES
        # a prior upgrade, and it writes exactly what a rebuild of the row
        # would write (``_enforce_denial_stance``) so the two never disagree.
        # ONE call per corpus against the FULL joint denial list, never a loop
        # of singleton calls (the _enforce_denial_stance regression, 2026-07-29:
        # singletons blank only their own phrase and let a sibling denial's
        # leftover text read as independent affirmation).
        # The ASSERT half first (ADR-059 amended 2026-08-08, #486): a DECLARED
        # denial names this concept, so recording the candidate's own statement
        # is honest and absolute — corpus-independent by construction, exactly
        # as before (both containment checks below are True for a declared
        # match, so hoisting it changes no verdict, only what may be written).
        declared_level = _declared_denial_level(concept, forms, denial_entries)
        if denials and declared_level is not None:
            logger.info(
                "upgrade_ledger_for_concepts: recorded %r as denied (was %r/"
                "claimable=%r) — the turn ADDRESSED this requirement by denying "
                "it (ADR-059 clause 2)",
                concept,
                e.get("status"),
                e.get("claimable"),
            )
            denied = _denied_row(e, declared_level)
            changed = changed or denied != e
            new_ledger.append(denied)
            continue

        if denials and _entry_is_denied(concept, forms, denials, turn_corpus or None):
            if not _entry_is_denied(concept, forms, denials, full_corpus):
                # #351 — the turn denies this concept only by containment in a
                # longer denied compound, and the vault affirms it outside that
                # compound. Neither verdict is available: the turn is no
                # evidence FOR it, and the candidate denied the compound, not
                # this concept. Leave the entry exactly as it stands — which,
                # composed with #352, also means a containment-only denial
                # never REVERSES a standing upgrade. That is the same verdict
                # the rebuild reaches: `_enforce_denial_stance` has always
                # judged containment against this very corpus.
                logger.info(
                    "upgrade_ledger_for_concepts: left %r as it stands (%r/"
                    "claimable=%r) — it is only contained in a denied compound "
                    "and the vault affirms it independently (#351)",
                    concept, e.get("status"), e.get("claimable"),
                )
                new_ledger.append(e)
                continue
            # Containment only, and nothing affirms the concept outside the
            # denied compound — not the turn, not the vault. The never-upgrade
            # half fires (the entry is floored, and a standing claim is
            # reversed, #352) but the assert half does not: the candidate named
            # the compound, never this concept (#486).
            logger.info(
                "upgrade_ledger_for_concepts: floored %r (was %r/claimable=%r) "
                "without asserting a denial — it is only contained in a denied "
                "compound and nothing affirms it independently (#486)",
                concept,
                e.get("status"),
                e.get("claimable"),
            )
            floored = _floored_row(e)
            changed = changed or floored != e
            new_ledger.append(floored)
            continue

        # Nothing to upgrade: this turn confirmed nothing (``upgrade=False``),
        # or an earlier turn already made the entry claimable.
        if not upgrade or e.get("claimable"):
            new_ledger.append(e)
            continue

        e["claimable"] = True
        e["status"] = upgrade_status
        if ev:
            e["evidence"] = ev
        changed = True
        new_ledger.append(e)
    return new_ledger, changed


def reevaluate_gap_ledger_against_vault(
    keyword_ledger: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Deterministically re-check every still-open (``status == "gap"``,
    ``claimable`` False) ledger entry against the CURRENT vault (#274/#284/
    #273, PO reframing 2026-07-26).

    A requirement's status must reflect whether the VAULT answers it, not
    whether one particular interview turn happened to write something.
    ``upgrade_ledger_for_concepts`` (#188) already covers the "this turn
    addressed it" case; this covers every OTHER door the evidence could have
    arrived through — CV import, testimony intake, an earlier interview
    session, ``submit_claims`` (agent_bridge.py) — none of which run the
    interview's own addressed-gate.

    Reuses the two instruments this touches ALWAYS have to agree with:

    * :func:`applire.services.ats_audit.surface_present` — THE shared
      presence predicate (#122, US212). The loop that grades a document
      (the ATS panel, :func:`verified_missing_claimable`) and the loop that
      heals a ledger entry here must never disagree on "present".
    * :func:`upgrade_ledger_for_concepts` — the ONE write path (#188) that
      flips a ledger entry to claimable. This function only decides WHICH
      concepts qualify and what real evidence to cite; it never duplicates
      the flip logic itself.

    Truthfulness floor (ADR-059): a concept the candidate explicitly denied
    (``ProfileMetadata.denied_concepts``) is NEVER upgraded, however the
    vault or the denial's own statement phrases it. The PRESENCE corpus (the
    coverage half) is built from :func:`profile_literal_corpus`'s own
    flattening (``_strip_denial_text`` + ``_draft_strings``) — denial-testimony
    text is stripped BEFORE flattening, so a denial's own receipt can never
    satisfy this presence check and defeat the floor it is supposed to respect
    (the same class of trap ``_enforce_denial_stance``/``profile_literal_corpus``
    already close for the classifier's adjacency inference — see that
    docstring). ``is_denied_concept`` is checked independently as a second,
    belt-and-braces floor, and reads the narrower RELEASE corpus (below).

    SKIP-ONLY BY DESIGN (ADR-059 amended 2026-08-08, #486). This function never
    flips an entry to ``denied`` — it only refuses to upgrade one — so it holds
    the never-upgrade half and NOTHING of the assert half: containment matching
    stays whole here, and the declared/containment split has no work to do. Its
    delegate call to ``upgrade_ledger_for_concepts`` passes no
    ``denied_concepts`` precisely so that seam's write path stays out of reach.
    Adding an assert half here would invent testimony from a vault read.

    #480 step 1: the presence corpus is built from the CONFIRMED vault only
    (``exclude_unconfirmed``) — an unconfirmed entry backs nothing, so it may
    not heal a gap here. #480 §7.5(a) then SPLIT the two corpora this function
    was building as one: the release half (the ``is_denied_concept`` floor)
    reads
    :func:`applire.services.profile.reconcile.stance.denial_release_corpus`,
    the attested entity labels alone, while the coverage half keeps the
    flattened confirmed vault — narrowing coverage too would stop this
    function healing gap rows from bullets, which is why it exists.

    Conservative by construction (at least as conservative as #188):

    * only ``status == "gap"`` entries are eligible — a ``partial`` entry is
      already claimable and stays exactly as classified; this function never
      upgrades partial evidence to "fully answered" on a bare substring hit,
      which would be LESS conservative than the original LLM classification
      that assigned "partial" rather than "direct" in the first place;
    * a concept only upgrades when a REAL vault text node contains a surface
      form (the node itself becomes the ``evidence`` — never a synthesized
      marker, never fabricated);
    * an entry with no concept, or whose forms match nothing in the corpus,
      passes through untouched (fail-closed — never invents evidence).

    Deterministic, no LLM call. Pure; tolerant of ``None``/empty.
    """
    if not keyword_ledger:
        return list(keyword_ledger or []), False

    from applire.services.ats_audit import _norm as ats_norm
    from applire.services.ats_audit import surface_present

    # #480 step 1 — the presence corpus this floor consults is the CONFIRMED
    # vault only (ADR-061 clause 3): an `unconfirmed` entry backs nothing, so
    # it can neither heal a gap here nor release a denial through the
    # containment branch's independent-affirmation check. Same build-time
    # filter as `build_keyword_ledger`'s `vault_corpus`.
    stripped_profile = _strip_denial_text(exclude_unconfirmed(profile_json) or {})
    strings = [s for s in _draft_strings(stripped_profile) if s and s.strip()]
    corpus = ats_norm(" ".join(strings))

    # ADR-059 amended 2026-08-09 (#480 §7.5(a)) — site 2 of five, and the first
    # of the two that needed SPLITTING. One corpus was feeding two predicates
    # of opposite polarity: the RELEASE half below (`is_denied_concept`'s
    # independent-affirmation branch) and the COVERAGE half (`surface_present`,
    # which decides whether a gap row heals, and which cites a real vault text
    # node as its evidence). Only the release half narrows — narrowing coverage
    # too would stop this function healing gap rows from bullets, which is the
    # entire reason it exists.
    release_corpus = denial_release_corpus(profile_json)

    # ADR-064 — reuse the same dict-or-str normaliser _enforce_denial_stance
    # uses, so this caller's extraction can never quietly diverge from that
    # one's. This function only ever SKIPS a denied concept (never flips it
    # to status="denied" itself — that write path is _enforce_denial_stance,
    # inside build_keyword_ledger), so only the concept string is needed here;
    # is_denied_concept has no notion of denial_level.
    denials = [
        concept
        for concept, _level in _denied_concept_entries(
            ((profile_json or {}).get("metadata") or {}).get("denied_concepts")
        )
    ]

    ledger = list(keyword_ledger)
    changed = False
    for entry in keyword_ledger:
        if entry.get("claimable") or entry.get("status") != "gap":
            continue
        # ADR-069: a scope entry's status moves only via the gap-analysis
        # judgement seam or elicited testimony — literal presence of its
        # synthesised label (or its number) in unrelated vault prose is not
        # evidence about a span or a budget (the ADR-059 every-seam rule).
        if is_scope_entry(entry):
            continue
        concept = entry.get("concept", "")
        if not _norm(concept):
            continue
        forms = [f for f in (entry.get("surface_forms") or [concept]) if f]
        probes = list(dict.fromkeys(forms + [concept]))  # dedupe, keep order

        # ADR-059 floor: never upgrade a denied concept, however the vault or
        # its own denial statement phrases it (corpus already denial-stripped
        # above — this is the belt-and-braces second check).
        if denials and any(
            is_denied_concept(p, denials, release_corpus) for p in probes
        ):
            continue

        if not any(surface_present(p, corpus) for p in probes):
            continue

        # Cite the REAL vault text node the form was actually found in —
        # never a synthesized marker.
        evidence = next(
            (s for p in probes for s in strings if surface_present(p, ats_norm(s))),
            None,
        )
        if not evidence:
            continue  # presence only at a join seam between two strings — fail closed

        ledger, did_change = upgrade_ledger_for_concepts(ledger, [concept], evidence)
        changed = changed or did_change
    return ledger, changed


# ── #260 — pre-generation keyword-liability check ───────────────────────────
# The inverse of #250 (which drops a JD-echo skill TAG with no vault tie at
# all): here the concept genuinely clears the ledger's OWN claimable
# classification (it may even be a literal vault hit) — the missing thing is
# specifically NARRATIVE depth. A hard-requirement keyword sitting only in a
# bare skills-list entry, with no bullet/achievement/signature-story anywhere
# to substantiate it, will still be echoed by the generator (it's claimable)
# but reads as unsubstantiated to a human reviewer (run-4: "RAG appears once,
# as a skills-list keyword"). Deterministic — no new LLM pass; reuses THE
# shared presence predicate (ats_audit.surface_present) already used by
# `verified_missing_claimable`/`profile_literal_corpus`.
#
# Distinct vocabulary from #249's "related" state (TruthfulnessPanel.tsx):
# "related" reclassifies a POST-generation Oracle "unbacked" verdict on a
# claim that already shipped, when the ledger's own (possibly adjacency-only)
# ADJACENCY evidence backs the same concept. This is a PRE-generation check
# on a different axis — LITERAL vault presence (claimable/narrative_backed)
# vs NARRATIVE depth (challenge/mechanism/outcome, a bullet, an achievement)
# — and never touches the Oracle verdict taxonomy or #249's frontend states.
# The two are orthogonal and additive: a concept can be #249-"related" AND
# #260-"liability" at once without contradiction (see
# test_ats_audit.py::test_249_related_and_260_liability_are_orthogonal).

_NARRATIVE_EXPERIENCE_FIELDS = ("work_experience", "projects", "volunteer_activities")
_NARRATIVE_STORY_FIELDS = ("title", "challenge", "mechanism", "outcome", "benchmark")


def _narrative_texts(profile_json: dict[str, Any] | None) -> list[str]:
    """Every narrative-bearing string in the vault (#260): work/project/
    volunteer responsibilities + achievements, and signature-story fields
    (ADR-055, challenge/mechanism/outcome/benchmark/title). Deliberately
    EXCLUDES the bare skills list, technologies[], and the professional
    summary — a skill entry alone, or a one-line elevator pitch, is not a
    story. ``None``/malformed-shape tolerant.
    """
    if not profile_json:
        return []
    texts: list[str] = []
    for field in _NARRATIVE_EXPERIENCE_FIELDS:
        for entry in profile_json.get(field) or []:
            if not isinstance(entry, dict):
                continue
            texts.extend(s for s in (entry.get("responsibilities") or []) if isinstance(s, str))
            texts.extend(s for s in (entry.get("achievements") or []) if isinstance(s, str))
    for story in profile_json.get("signature_stories") or []:
        if not isinstance(story, dict):
            continue
        for field in _NARRATIVE_STORY_FIELDS:
            v = story.get(field)
            if isinstance(v, str):
                texts.append(v)
    return texts


def profile_narrative_corpus(profile_json: dict[str, Any] | None) -> str:
    """The vault's NARRATIVE-bearing text only, flattened + normalised (#260).

    Narrower than :func:`profile_literal_corpus` (which is the WHOLE profile,
    used by the denial floor) — this scopes to the fields a human reviewer
    would recognise as "a story", so a bare skills-list/technologies[] entry
    can never count as its own narrative evidence.
    """
    if not profile_json:
        return ""
    from applire.services.ats_audit import _norm as ats_norm

    return ats_norm(" ".join(_narrative_texts(profile_json)))


def _annotate_narrative_backed(
    ledger: list[dict[str, Any]],
    profile_json: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Stamp every ledger entry with ``narrative_backed`` (#260).

    ``profile_json is None`` (the argument omitted entirely) reproduces the
    pre-#260 behaviour exactly — ``narrative_backed = True`` for every entry,
    so no caller that has no profile on hand ever raises a false liability
    signal. A profile that IS given but genuinely carries no narrative text
    is the honest opposite: every entry comes back unbacked. Runs as a final
    pass over the fully-built ledger so it never has to special-case the
    duplicate-collapse / gap-stance / denial-stance orderings above.
    """
    if profile_json is None:
        return [{**e, "narrative_backed": True} for e in ledger]

    from applire.services.ats_audit import surface_present

    corpus = profile_narrative_corpus(profile_json)
    out: list[dict[str, Any]] = []
    for e in ledger:
        forms = list(e.get("surface_forms") or [e.get("concept", "")])
        if e.get("concept"):
            forms.append(e["concept"])
        backed = bool(corpus) and any(surface_present(f, corpus) for f in forms)
        out.append({**e, "narrative_backed": backed})
    return out


def keyword_liabilities(keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The #260 liability slice: JD HARD-REQUIREMENT concepts that ARE
    claimable (will be echoed by the generator) but have NO narrative
    evidence anywhere in the vault.

    Scoped to ``required`` sources only — a nice-to-have bare keyword is
    never flagged (hard requirements only, per the run-4 finding). An
    honest-gap (non-claimable) entry is never a liability either: it will
    not be echoed as a strength in the first place. ``None``/empty tolerant.
    """
    return [
        e
        for e in (keyword_ledger or [])
        if e.get("claimable")
        and "required" in (e.get("sources") or [])
        and not e.get("narrative_backed", True)
    ]


def downgrade_ledger_for_concepts(
    keyword_ledger: list[dict[str, Any]] | None,
    concepts: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Deterministically DOWNGRADE claimable entries to an honest gap (#260
    exit b — the candidate's own choice to DROP a keyword-liability concept
    rather than tell its story via ``resolve_gap``, exit a).

    Mirrors :func:`upgrade_ledger_for_concepts`'s shape/guarantees, run in
    reverse: only CLAIMABLE entries whose concept normalize-matches are
    touched; every other entry copies through untouched. Never invents or
    removes an entry — an unmatched/reworded concept simply no-ops. The
    generator's existing claimable/forbidden split
    (:func:`split_ledger_for_prompt`) then treats the downgraded concept as
    a DO-NOT-CLAIM honest gap on the next generation — never a silent echo
    of a keyword the candidate chose not to substantiate.
    """
    if not keyword_ledger or not concepts:
        return list(keyword_ledger or []), False
    concept_norms = [_norm(c) for c in concepts if _norm(c)]
    if not concept_norms:
        return list(keyword_ledger), False

    new_ledger: list[dict[str, Any]] = []
    changed = False
    for entry in keyword_ledger:
        e = dict(entry)
        concept_norm = _norm(e.get("concept", ""))
        if (
            e.get("claimable")
            and concept_norm
            and any(_matches(concept_norm, cn) for cn in concept_norms)
        ):
            e["claimable"] = False
            e["status"] = "gap"
            e["evidence"] = ""
            # ADR-048 amended 2026-08-13 (#526): the row is leaving the claimable
            # adjacent-partial shape, so the adjacency pointer leaves with it. It
            # means "the candidate does not have this term, they have that one
            # instead" — a statement the candidate has just declined to make.
            # Left standing, `render_unaddressed_hard_requirements_block` reads it
            # unconditionally and tells the letter writer to give prominence to
            # the very capability that was dropped.
            e.pop("adjacent_evidence", None)
            # No longer claimable — moot either way, but keep the flag honest
            # so a re-read never re-flags a concept the candidate just dropped.
            e["narrative_backed"] = True
            changed = True
        new_ledger.append(e)
    return new_ledger, changed


def _is_denial_receipt_change(change: Any) -> bool:
    """Is this ``FieldChange`` the durable receipt of a denial (#231)?

    Written exclusively by
    :func:`applire.services.profile.reconcile.stance.record_denials` —
    ``section == "metadata"`` and ``field == "denied_concepts"``, whatever
    the ``action``/wording (the "Noted limit: …" / "Re-confirmed limit: …"
    rationale is descriptive, not the identifying shape).
    """
    return (
        isinstance(change, dict)
        and change.get("section") == "metadata"
        and change.get("field") == "denied_concepts"
    )


def _strip_denial_text(profile_json: dict[str, Any]) -> dict[str, Any]:
    """Filtered copy of ``profile_json`` with denial-testimony text removed
    (wave-6, #270-adjacent).

    The affirmation corpus (:func:`profile_literal_corpus`) must be built
    from POSITIVE vault content only — a concept can never be "independently
    affirmed" by the text of its own denial. Removes:

    * ``metadata.denied_concepts`` entirely — both the ``concept`` label and
      the verbatim ``statement`` (you cannot deny embeddings without writing
      the word "embeddings", and the *statement* is free testimony prose, not
      just the concept phrase ``_independently_affirmed`` already blanks).
    * ``metadata.enrichment_history[].changes`` entries that ARE a denial
      receipt (see :func:`_is_denial_receipt_change`) — the durable audit
      trail carries the same concept text (``new_value``/``old_value``) plus
      the "Noted limit: …" rationale.

    Every other field — including every OTHER enrichment-history change —
    passes through untouched, so a genuinely evidenced broad concept
    elsewhere in the vault still independently affirms (#249). Tolerant of
    ``None``/malformed shapes at every level (denial testimony may be absent,
    or the section may not even be a dict).
    """
    if not isinstance(profile_json, dict):
        return profile_json
    metadata = profile_json.get("metadata")
    if not isinstance(metadata, dict):
        return profile_json

    filtered_metadata = dict(metadata)
    filtered_metadata.pop("denied_concepts", None)

    history = metadata.get("enrichment_history")
    if isinstance(history, list):
        new_history: list[Any] = []
        for record in history:
            if not isinstance(record, dict):
                new_history.append(record)
                continue
            changes = record.get("changes")
            if not isinstance(changes, list):
                new_history.append(record)
                continue
            kept = [c for c in changes if not _is_denial_receipt_change(c)]
            new_history.append(record if kept == changes else {**record, "changes": kept})
        filtered_metadata["enrichment_history"] = new_history

    return {**profile_json, "metadata": filtered_metadata}


_MIN_ADJACENT_MATERIAL_CHARS = 25


def verified_adjacent_material(evidence: str | None, corpus: str | None) -> str:
    """The part of a demoted row's ``evidence`` the VAULT itself carries, verbatim.

    ADR-074 amended 2026-08-13 (PO ruling, on captured-call replay evidence).
    When :func:`assert_claimable_backed` demotes a row for
    ``no_vault_evidence_unit``, the reason is always the same: the vault says the
    same thing in **different words**. Discarding the classifier's cited material
    along with the claim throws away the only honest positioning the letter could
    have used — the replay's ``Investitionsverantwortung`` case, where the vault
    holds *"Vorlage und Umsetzung von Investitionsentscheidungen im Rahmen der
    Industrie-4.0-Roadmap"* and German compounding is the entire reason
    ``ground_skill_claim`` (whole-token, no morphological decomposition) cannot
    reach it.

    **The model's paraphrase is not evidence; the vault's own sentence is.** So
    the evidence is split into its segments and only those appearing VERBATIM in
    ``corpus`` (normalised) survive — the ADR-070 attested-quote discipline
    applied one layer up, and a FACT under ADR-062 clause 1 (literal containment,
    never a reading of meaning). Returns ``""`` when nothing verifies, which
    leaves the row an ADR-074 Restfall: telling the candidate we hold nothing
    beats handing the writer a sentence the vault cannot back.
    """
    text = (evidence or "").strip()
    if not text or not corpus:
        return ""
    # THE SAME normaliser :func:`profile_literal_corpus` built the corpus with.
    # Using this module's `_norm` here instead silently fails every comparison:
    # it keeps hyphens ("industrie-4.0-roadmap") where the corpus has spaces
    # ("industrie 4.0 roadmap"), so the check returns "" for material that IS in
    # the vault — a control that cannot fire, found by running it.
    from applire.services.ats_audit import _norm as ats_norm

    corpus_norm = ats_norm(corpus)
    if not corpus_norm:
        return ""
    # The LONGEST word-aligned span of the evidence the corpus carries verbatim,
    # not a split on punctuation. Splitting was the first implementation and it
    # failed on the captured replay: the classifier separated its two citations
    # with a COMMA ("… Industrie-4.0-Roadmap, Investitionsvorlage für …"), so the
    # whole string was one segment and nothing verified. Punctuation is the
    # model's choice; span length is a property of the vault.
    words = text.split()
    best = ""
    for start in range(len(words)):
        for end in range(len(words), start, -1):
            if end - start < 3:
                break
            span = " ".join(words[start:end]).strip().rstrip(".,;:").strip()
            # A fragment too short to be a claim is not material — it is a word
            # that happens to occur somewhere in a large corpus.
            if len(span) < _MIN_ADJACENT_MATERIAL_CHARS or len(span) <= len(best):
                break
            if ats_norm(span) in corpus_norm:
                best = span
                break
    return best


def profile_literal_corpus(profile_json: dict[str, Any] | None) -> str:
    """The vault's OWN literal text, flattened + normalised (#249 run-4,
    2026-07-24) — POSITIVE content only (wave-6, denial testimony excluded).

    Reuses :func:`_draft_strings` (already the shared flattener the US213
    verified-coverage check scans a DRAFT document with — any dict of
    arbitrary shape) against the PROFILE instead, so the SAME flattening
    logic backs both instruments. It fed ``_enforce_denial_stance``'s
    independent-affirmation check until #480 §7.5(a) (2026-08-09) — see the
    note below; the coverage question it answers today is "does the vault
    literally carry this term anywhere at all".

    Wave-6 fix: ``metadata.denied_concepts`` and denial-receipt
    enrichment-history changes are stripped BEFORE flattening
    (:func:`_strip_denial_text`) — a denial's own verbatim statement/receipt
    must never "independently affirm" the very concept it denies (a live
    vault's testimony recording a denial of "hands-on embedding model
    configuration" necessarily contains the word "embeddings", and that word
    survived into the corpus verbatim, defeating the ADR-059 denial floor).
    ``None``/empty tolerant.

    **This is the COVERAGE corpus, not the release corpus** (ADR-059 amended
    2026-08-09, #480 §7.5(a)). Its callers — ``services/cv.py`` and
    ``services/cover_letter.py``'s ``present_unsupported`` consistency guards
    and ``services/cv_diff.py``'s US147 pre-download diff — all ask "is this
    term literally grounded in the vault", which is a coverage question and
    rightly reads the whole vault. What may RELEASE a persisted denial is a
    different and much narrower question, answered by
    :func:`applire.services.profile.reconcile.stance.denial_release_corpus`;
    this function no longer feeds that predicate at any site. A denied term's
    own statement text must not count as grounding for the coverage callers
    either, so the exclusion applies unconditionally; no parameter added.
    """
    if not profile_json:
        return ""
    from applire.services.ats_audit import _norm as ats_norm

    return ats_norm(" ".join(_draft_strings(_strip_denial_text(profile_json))))


# ── #318 / ADR-061 — THE affirmative invariant ──────────────────────────────

#: Why a claimable row failed the invariant. Ordered by precedence below:
#: polarity is decided before evidence, and evidence before vault backing
#: (ADR-059 amended 2026-07-27 clause 3 — polarity precedes; #352).
_HEAL_TO_DENIED = frozenset({"denied_evidence", "denied_concept"})


def _claimable_backing_violation(
    entry: dict[str, Any],
    denials: list[str],
    vault_corpus: str | None,
    vault_index: Any,
) -> str | None:
    """The invariant's predicate for ONE claimable row — ``None`` when the row
    is backed, otherwise the reason string (see :data:`_HEAL_TO_DENIED`).

    Every clause is a FACT under ADR-062 clause 1: a status-enum read, an
    emptiness test, a string comparison against the one spelled sentinel, a
    match against the model's own declared denials, and set membership over
    the vault's evidence units. Nothing here reads prose for meaning, and
    nothing re-judges the classifier's ``direct``/``partial`` call.
    """
    concept = entry.get("concept", "")
    forms = [f for f in (entry.get("surface_forms") or []) if isinstance(f, str) and f.strip()]
    probes = list(dict.fromkeys(([concept] if concept else []) + forms))

    # 1 — polarity first (ADR-059 am. clause 3, #352): a denial outranks every
    # affirmative signal, so it is decided before anything else is looked at.
    evidence = (entry.get("evidence") or "").strip()
    if evidence == DENIED_EVIDENCE:
        return "denied_evidence"
    if denials and probes and _entry_is_denied(concept, forms, denials, vault_corpus):
        return "denied_concept"

    # 2 — the row's own coherence.
    if entry.get("status") not in {"direct", "partial"}:
        return "status_not_claimable"
    if not evidence:
        return "no_evidence"

    # 3 — the named exemptions (ADR-069 / ADR-048 am. 2026-07-27). Both are row
    # shapes whose concept is NOT a claim about the vault: a scope entry's
    # concept is a synthesised label carrying the JD's own figure, and a
    # positioning-only entry explicitly means "the candidate does NOT have this
    # term". Demanding a vault evidence unit for either contradicts the row's
    # own definition; both remain subject to clauses 1 and 2 above.
    if is_scope_entry(entry) or is_positioning_only(entry):
        return None

    # 4 — the affirmative floor: at least one vault evidence unit resolves this
    # concept. THE Oracle's own predicate (ADR-066 clause 2 — one logical
    # operation, one implementation), the same call the #219 selection guard
    # makes before putting a name on the page. A concept this cannot ground is
    # a concept the delivered document's own truthfulness report would mark
    # `unbacked`, so the ledger may not authorise it, however it was classified.
    if vault_index is None:
        return None
    from applire.services.oracle.matchers.grounding import ground_skill_claim

    if any(ground_skill_claim(p, vault_index) is not None for p in probes):
        return None
    return "no_vault_evidence_unit"


def assert_claimable_backed(
    keyword_ledger: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
    *,
    seam: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """THE affirmative invariant of ADR-061, checked at every ledger PERSIST
    seam (#318): **a ``claimable`` row with no vault evidence must be
    impossible.**

    ADR-059's 2026-07-27 amendment made the NEGATIVE half explicit at every
    write seam — polarity is consulted before any status moves. This is its
    twin and, per #318, the one half of the doctrine that can be *asserted*
    rather than argued. Today the affirmative half is emergent: four seams
    (:func:`build_keyword_ledger`, :func:`upgrade_ledger_for_concepts`,
    :func:`reevaluate_gap_ledger_against_vault`, ``scope_requirements.
    build_scope_ledger_entries``) each apply their own local evidence rule, and
    nothing states the joint property or checks it on the row that is actually
    written.

    **The measured divergence (charter run #7 case 2, ``operations_marcus_de``,
    real provider).** The post-interview ledger gained ``MES`` and ``OEE`` as
    ``direct``/``claimable`` while the interview added *zero* skills to the
    vault — the ADR-046 stance guard had dropped the same turn's skill ops.
    Two seams read one turn and reached opposite conclusions, and nothing
    reconciled them. A CV writer acting on such a row produces a claim the
    Oracle must then mark ``unbacked``: **the pipeline generating its own
    truthfulness violation.** Run #7 case 1 is the other shape — eight denied
    concepts at ``status="direct", claimable=True`` with the candidate's own
    denial sentence stored as the backing evidence.

    **What it checks** (:func:`_claimable_backing_violation`), in precedence
    order — polarity, then coherence, then backing:

      1. ``denied_evidence`` — the row carries :data:`DENIED_EVIDENCE`;
      2. ``denied_concept`` — the concept matches a persisted denial, judged by
         ``is_denied_concept`` against :func:`profile_literal_corpus`: the SAME
         instrument ``_enforce_denial_stance`` and ``upgrade_ledger_for_
         concepts`` already share, never a fourth matcher;
      3. ``status_not_claimable`` — ``claimable`` set with a status that is not
         ``direct``/``partial``;
      4. ``no_evidence`` — empty/whitespace ``evidence``;
      5. ``no_vault_evidence_unit`` — no vault evidence unit resolves the
         concept or any surface form, via
         :func:`applire.services.oracle.matchers.grounding.ground_skill_claim`.

    **Why ``ground_skill_claim`` and not a new matcher.** It is the predicate
    the Oracle audits the finished document with, and #219 already converged the
    generator's *selection* side onto it for exactly this reason (ADR-066
    clause 2). Asking the same question one layer up is what makes the two
    instruments stop diverging; a second predicate here would recreate the
    defect in a new place. The vault is filtered through
    ``stance.exclude_unconfirmed`` first: ADR-061 clause 3 says an
    ``unconfirmed`` entry backs nothing.

    **The exemptions are the ones ADR-048/ADR-069 already name**, not new ones:
    :func:`is_scope_entry` (the concept is a synthesised label carrying the JD's
    own figure; its floor and citation check live in ``scope_requirements``) and
    :func:`is_positioning_only` (the row's *meaning* is "the candidate does NOT
    have this term" — demanding vault backing for it is the over-claim pressure
    ADR-059 clause 6 exists to remove). Both stay subject to the polarity and
    evidence clauses. ``profile_json is None`` makes the whole invariant vacuous
    — a caller with no vault on hand must never raise a false violation
    (mirrors :func:`_annotate_narrative_backed`).

    **HEAL, not raise — and never silent.** A violating row is downgraded:
    a polarity violation through :func:`_denied_row` — THE shared denied-write
    the rebuild floor and the upgrade seam also call, so the three cannot
    disagree (ADR-059 amended 2026-08-08 clause (b): this heal's "byte-identical
    to the rebuild" claim was already false — it never wrote ``denial_level``,
    which ``_enforce_denial_stance`` has always set) — and everything else to
    ``gap``/``""`` (byte-identical to what the builder writes for an
    unclassified expectation). A polarity violation reached only by CONTAINMENT
    heals through :func:`_floored_row` instead: the heal may not write testimony
    a rebuild of the same row would now refuse to write (#486). Both directions are
    away from claimable, which is ADR-040's never-claim-beats-claim direction.
    Raising was considered and rejected: this runs at a persist seam inside a
    live interview turn or gap analysis, so an exception would convert one bad
    row into a failed application-wide operation — trading a truthfulness
    defect for an availability defect, and leaving the caller no path that both
    keeps the good rows and drops the bad one. The gate criterion is "never
    deliver on a corrupt row", which downgrading satisfies exactly. Every heal
    logs at WARNING with the concept, the previous status and the reason, and
    the violation list is returned so a caller can surface it.

    Returns ``(healed_ledger, violations)``. ``violations`` is a list of
    ``{concept, status, reason}`` dicts — empty when the ledger is clean. Pure:
    the input list and its rows are never mutated. Deterministic, no LLM.
    """
    if not keyword_ledger:
        return [], []
    rows = list(keyword_ledger)
    if profile_json is None:
        return rows, []

    # ADR-061 clause 3 — an `unconfirmed` skill/language/certification cannot
    # back a CV bullet, a letter sentence or a `direct` ledger row, so it must
    # not count as an evidence unit here either.
    confirmed = exclude_unconfirmed(profile_json)
    # ADR-059 amended 2026-08-09 (#480 §7.5(a)) — site 3 of five, the path the
    # 2026-08-09 adversarial pass found: this heal's corpus is a FIFTH thread
    # into `_independently_affirmed`, and swapping four while leaving it would
    # recreate exactly the divergence the #486 amendment clause (b) documents.
    # The second SPLIT: `confirmed` fed both this corpus and `build_vault_index`
    # below. Only the polarity clause's corpus narrows — the affirmative floor
    # (clause 5) is a coverage question and keeps the whole confirmed vault, or
    # a claimable row grounded by a real bullet would be healed away.
    vault_corpus = denial_release_corpus(profile_json) or None
    # Only the affirmative floor needs the typed vault index, so only IT
    # degrades when the vault will not validate.
    try:
        from applire.services.oracle.matchers.vault import build_vault_index

        vault_index = build_vault_index(confirmed)
    except Exception as exc:
        # Fail OPEN on clause 5 ONLY, and loudly. Fail-closed would purge every
        # claimable row of a healthy ledger because one vault document would not
        # validate — a larger truthfulness loss than the check is worth, and not
        # recoverable from the candidate's side. Clauses 1-4 still run, so a
        # denial can never survive an unparseable vault. Never silent.
        vault_index = None
        logger.warning(
            "assert_claimable_backed[%s]: the vault could not be indexed (%s: %s) — "
            "the affirmative floor (clause 5) is UNCHECKED for this write; the "
            "polarity and evidence clauses still apply",
            seam or "unnamed-seam",
            type(exc).__name__,
            exc,
        )

    denial_entries = _denied_concept_entries(
        ((profile_json or {}).get("metadata") or {}).get("denied_concepts")
    )
    denials = [concept for concept, _level in denial_entries]

    healed: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict) or not entry.get("claimable"):
            healed.append(entry)
            continue
        reason = _claimable_backing_violation(entry, denials, vault_corpus, vault_index)
        if reason is None:
            healed.append(entry)
            continue
        was = entry.get("status")
        logger.warning(
            "assert_claimable_backed[%s]: healed claimable concept %r (was %r) — %s. "
            "ADR-061/#318: a claimable ledger row with no vault evidence is a "
            "truthfulness violation the pipeline would generate against itself.",
            seam or "unnamed-seam",
            entry.get("concept"),
            was,
            reason,
        )
        violations.append(
            {"concept": entry.get("concept", ""), "status": was, "reason": reason}
        )
        if reason in _HEAL_TO_DENIED:
            # ADR-059 amended 2026-08-08 clause (b) — ONE denied-write shape
            # for all three writers (:func:`_denied_row`), and the same
            # declared/containment split the floor makes: healing a
            # containment-only match to ``denied`` would write testimony a
            # rebuild of the very same row now refuses to write, which is the
            # instrument divergence this clause exists to close.
            level = _declared_denial_level(
                entry.get("concept", ""),
                [f for f in (entry.get("surface_forms") or []) if isinstance(f, str)],
                denial_entries,
            )
            if level is None and reason == "denied_evidence":
                # The row already CARRIES the denial marker (it was written by
                # a declared match at an earlier seam, or the denial has since
                # been revoked) — the incoherence is the ``claimable`` flag,
                # not the testimony. Keep the row's own level.
                level = entry.get("denial_level") if entry.get("denial_level") in (
                    "direct", "partial"
                ) else "direct"
            healed.append(
                _denied_row(entry, level) if level is not None else _floored_row(entry)
            )
        else:
            # ADR-048 amended 2026-08-13 (#526): byte-identical to what the
            # builder writes for an unclassified expectation — which, since the
            # adjacency pointer is now an invariant of the claimable-partial
            # shape, means the pointer does not survive the heal either.
            gap_row = {**entry, "status": "gap", "claimable": False, "evidence": ""}
            gap_row.pop("adjacent_evidence", None)
            # ADR-074 amended 2026-08-13 — the ONE exception, and the line it
            # draws is deliberate: a pointer survives the demotion that is about
            # VOCABULARY (clause 5 — the vault says the same thing in other
            # words), never one that is about the candidate's own POSITION. A
            # denial, a containment floor and a declined liability all still
            # strip it, because promoting material there would argue with
            # something the candidate actually said. Verified verbatim against
            # the vault, so the model's paraphrase can never reach a writer.
            if reason == "no_vault_evidence_unit":
                material = verified_adjacent_material(
                    entry.get("evidence"), profile_literal_corpus(confirmed)
                )
                if material:
                    gap_row["adjacent_evidence"] = material
                    logger.info(
                        "assert_claimable_backed[%s]: %r keeps its vault-verified "
                        "adjacent material through the demotion — not claimable, but "
                        "positionable (ADR-074 amended)",
                        seam or "unnamed-seam",
                        entry.get("concept"),
                    )
            healed.append(gap_row)
    return healed, violations


def build_keyword_ledger(
    classifications: list[dict[str, Any]],
    required_skills: list[str],
    nice_to_have_skills: list[str],
    keywords: list[str],
    *,
    denied_concepts: list[dict[str, Any]] | list[str] | None = None,
    profile_json: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the Keyword Ledger from LLM classifications + the JD's own lists.

    Args:
        classifications: LLM output, one per concept:
            ``{concept, status, evidence, surface_forms?}``.
        required_skills / nice_to_have_skills / keywords: the JD's three lists.
        denied_concepts: the candidate's own testimony-denied concepts
            (``ProfileMetadata.denied_concepts``, #231) — applied as a final
            deterministic floor (``_enforce_denial_stance``) that the
            classifier's adjacency inference can never override. Accepts
            either the raw ``DeniedConcept`` dicts (carrying ``denial_level``,
            ADR-064) or a plain ``list[str]`` of concept tokens (treated as
            level ``"direct"``, back-compat).
        profile_json: the candidate's ``MasterProfile.profile_json`` (#249
            run-4) — flattened via :func:`profile_literal_corpus` and passed
            to the denial floor so a narrow denial cannot tar a broader term
            the vault independently, literally attests. ``None`` (the
            default) reproduces the pre-fix fail-closed behaviour exactly —
            back-compat for every caller that has no profile on hand.

    Returns:
        A list of ledger-entry dicts, each:
            ``{concept, surface_forms[], sources[], fit_weight, status,
               evidence, claimable, narrative_backed}``.
        ``claimable`` is ``status in {direct, partial}``, UNLESS the concept
        was explicitly denied (see ``denied_concepts`` above), which always
        wins. ``narrative_backed`` (#260) is whether the vault's narrative
        fields (work/project/volunteer responsibilities+achievements,
        signature stories) — NOT the bare skills list — substantiate the
        concept; ``True`` when ``profile_json`` is omitted (back-compat, no
        false liability signal without data to check).
    """
    # Authoritative JD expectation set: norm_key -> {"text", "sources"}.
    union: dict[str, dict[str, Any]] = {}
    for src, items in (
        ("required", required_skills or []),
        ("nice_to_have", nice_to_have_skills or []),
        ("keyword", keywords or []),
    ):
        for raw in items:
            key = _norm(raw)
            if not key:
                continue
            entry = union.setdefault(key, {"text": raw, "sources": set()})
            entry["sources"].add(src)

    covered: set[str] = set()
    ledger: list[dict[str, Any]] = []

    for item in classifications:
        concept = item.get("concept", "")
        if not _norm(concept):
            continue
        surface_forms = item.get("surface_forms") or [concept]
        # Match the concept + each surface form against the JD union.
        probes = {_norm(concept)} | {_norm(sf) for sf in surface_forms}
        matched_keys = {
            ukey for ukey in union for p in probes if _matches(p, ukey)
        }
        if not matched_keys:
            logger.warning(
                "build_keyword_ledger: concept %r matches no JD expectation — dropped",
                concept,
            )
            continue
        covered |= matched_keys

        sources: set[str] = set()
        for ukey in matched_keys:
            sources |= union[ukey]["sources"]

        status = item.get("status", "gap")
        if status not in _VALID_STATUS:
            logger.warning(
                "build_keyword_ledger: unknown status %r for %r, treating as gap",
                status,
                concept,
            )
            status = "gap"
        claimable = status in {"direct", "partial"}

        entry = {
            "concept": concept,
            "surface_forms": list(surface_forms),
            "sources": sorted(sources),
            "fit_weight": _fit_weight(sources),
            "status": status,
            "evidence": (item.get("evidence", "") if claimable else ""),
            "claimable": claimable,
        }
        # ADR-048 amended 2026-07-27: WHAT makes this partial. `partial` covers
        # two different situations — "the candidate has an adjacent capability"
        # (JD wants TOGAF, candidate has arc42) and "the right capability below a
        # stated bar" — and only the first has something to promote. Carried
        # ONLY on a partial entry: on a direct entry it would invite the writer
        # to lead with a substitute over the real thing, and on gap/denied there
        # is nothing to point at. Absent (not empty) when the classifier gives
        # none, so "below the bar" stays distinguishable from "adjacent".
        adjacent = str(item.get("adjacent_evidence") or "").strip()
        if status == "partial" and adjacent:
            entry["adjacent_evidence"] = adjacent
        ledger.append(entry)

    # Any JD expectation the LLM did not classify defaults to a gap entry —
    # never silent credit (mirrors ADR-035's unclassified-defaults-to-gap rule).
    for ukey, info in union.items():
        if ukey in covered:
            continue
        sources = info["sources"]
        ledger.append(
            {
                "concept": info["text"],
                "surface_forms": [info["text"]],
                "sources": sorted(sources),
                "fit_weight": _fit_weight(sources),
                "status": "gap",
                "evidence": "",
                "claimable": False,
            }
        )

    ledger = _enforce_gap_stance(_collapse_prefix_duplicates(ledger))
    # ADR-059 amended 2026-08-09 (#480 §7.5(a)) — the corpus the RELEASE
    # predicate reads is the vault's ATTESTED ENTITY LABELS, not its flattened
    # text. ``_independently_affirmed`` (via the containment branch) lets vault
    # text release a denial; step 1 (2026-08-08) removed `unconfirmed` entries
    # from it, and this narrows the rest — editor-typed prose is not an
    # affirmation the candidate attested. Site 1 of the five that feed the
    # floor/release predicate; all five swap together or the divergence the
    # #486 amendment clause (b) documents recurs.
    vault_corpus = denial_release_corpus(profile_json)
    ledger = _enforce_denial_stance(ledger, denied_concepts, vault_corpus or None)
    # #260: final pass — stamp narrative_backed so downstream consumers (the
    # pre-generation summary, the agent-channel ledger surface) can single
    # out a claimable-but-unstoried hard requirement without re-deriving it.
    return _annotate_narrative_backed(ledger, profile_json)
