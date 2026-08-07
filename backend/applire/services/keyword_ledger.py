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
from typing import Any

from applire.services.profile.reconcile.stance import is_denied_concept

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
        merged.append(
            {
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

    ``vault_corpus`` (#249 run-4, 2026-07-24): the profile's own literal text
    (:func:`profile_literal_corpus`), threaded through to
    ``is_denied_concept`` so its compound-containment rule ("RAG" is a whole
    word strictly inside the denied "RAG pipeline") can independently affirm
    a BROAD term against real vault evidence instead of always fail-closing
    (the #207 CSS/Tailwind-CSS default, correct when there is no vault text
    to check). A broad term is downgraded only if it is itself denied, or has
    no independent literal vault evidence outside the denied compound —
    never both classified `direct`/technologies-backed AND presented as an
    unsupported claim by the ATS panel on the very same document.

    ``denied_concepts`` accepts either the raw ``DeniedConcept`` dicts (which
    carry ``denial_level``, ADR-064) or a plain ``list[str]`` (every caller
    before ADR-064, treated as level ``"direct"``) — see
    :func:`_denied_concept_entries`. Whichever denied concept matches an
    entry, its level is mirrored onto ``denial_level`` on the forced row; when
    more than one denied concept matches the same ledger entry, ``"partial"``
    wins (the stronger signal — elicitation was exhausted on at least one of
    the matching denials).
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
        # Second pass: which denied concept(s) contributed, for the sole
        # purpose of picking ``denial_level`` (never to decide denial itself
        # — that is already settled above). ``corpus`` is intentionally
        # omitted here: without it, the compound-containment branch of
        # ``is_denied_concept`` fail-closes to True on any containment match,
        # so a per-concept singleton call can only find MORE matches than a
        # corpus-aware one would, never fewer — safe for a tie-break whose
        # input entry is already known to be denied.
        matched_levels = [
            level
            for d_concept, level in entries
            if is_denied_concept(concept, [d_concept], None)
            or any(is_denied_concept(f, [d_concept], None) for f in forms)
        ]
        result.append(
            {
                **entry,
                # ADR-059 amended 2026-07-27: the floor writes the STATUS, not
                # merely the flag. Forcing "gap" here discarded the reason the
                # concept is unclaimable — downstream could no longer tell a
                # requirement nobody asked about from one the candidate refused.
                "status": "denied",
                "claimable": False,
                "evidence": DENIED_EVIDENCE,
                # ADR-064 — mirror the durable denial's level onto the ledger
                # (the ledger is rebuilt from scratch every run; the
                # DeniedConcept is the durable home).
                "denial_level": "partial" if "partial" in matched_levels else "direct",
            }
        )
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
    """
    return bool((entry or {}).get("adjacent_evidence"))


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
    """
    forms: list[str] = []
    seen: set[str] = set()
    for entry in keyword_ledger or []:
        if entry.get("claimable") or entry.get("status") == "denied":
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


def coverage_reviewer_prompt_fn(base_fn, keyword_ledger: list[dict[str, Any]] | None):
    """Wrap a reviewer_prompt_fn so every review sees the CURRENT draft's verified
    coverage state (US213, #122).

    review_and_refine calls reviewer_prompt_fn(source, draft) each iteration with the
    latest draft, so the verified list is recomputed per pass and the block disappears
    once the refiner has surfaced the terms — deterministic convergence signal riding
    the existing bounded ADR-047 loop (no new loop).
    """

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        missing = verified_missing_claimable(draft, keyword_ledger)
        if missing:
            logger.info(
                "verified coverage check: %d claimable term(s) absent from draft: %s",
                len(missing),
                [e.get("concept", "") for e in missing],
            )
            prompt = f"{prompt}\n\n{render_verified_coverage_block(missing)}"
        return prompt

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
      * **``vault_corpus``** (:func:`profile_literal_corpus`, threaded from
        both doors — the same input ``_enforce_denial_stance`` takes) decides
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
    denials = [d for d in (denied_concepts or []) if _norm(d)]

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
            logger.info(
                "upgrade_ledger_for_concepts: recorded %r as denied (was %r/"
                "claimable=%r) — the turn ADDRESSED this requirement by denying "
                "it (ADR-059 clause 2)",
                concept,
                e.get("status"),
                e.get("claimable"),
            )
            e["status"] = "denied"
            e["claimable"] = False
            e["evidence"] = DENIED_EVIDENCE
            changed = True
            new_ledger.append(e)
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
    vault or the denial's own statement phrases it. The presence corpus is
    built from :func:`profile_literal_corpus`'s own flattening
    (``_strip_denial_text`` + ``_draft_strings``) — denial-testimony text is
    stripped BEFORE flattening, so a denial's own receipt can never satisfy
    this presence check and defeat the floor it is supposed to respect (the
    same class of trap ``_enforce_denial_stance``/``profile_literal_corpus``
    already close for the classifier's adjacency inference — see that
    docstring). ``is_denied_concept`` is checked independently as a second,
    belt-and-braces floor on top of the stripped corpus.

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

    stripped_profile = _strip_denial_text(profile_json or {})
    strings = [s for s in _draft_strings(stripped_profile) if s and s.strip()]
    corpus = ats_norm(" ".join(strings))

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
        if denials and any(is_denied_concept(p, denials, corpus) for p in probes):
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


def profile_literal_corpus(profile_json: dict[str, Any] | None) -> str:
    """The vault's OWN literal text, flattened + normalised (#249 run-4,
    2026-07-24) — POSITIVE content only (wave-6, denial testimony excluded).

    Reuses :func:`_draft_strings` (already the shared flattener the US213
    verified-coverage check scans a DRAFT document with — any dict of
    arbitrary shape) against the PROFILE instead, so the SAME flattening
    logic backs both instruments. Feeds ``_enforce_denial_stance``'s
    independent-affirmation check: a broad concept ("RAG") with a literal
    vault tie (``work_experience[].technologies[]``) outside every denied
    compound must never be tarred by a narrow denial ("RAG pipeline") the
    way an untethered containment check would.

    Wave-6 fix: ``metadata.denied_concepts`` and denial-receipt
    enrichment-history changes are stripped BEFORE flattening
    (:func:`_strip_denial_text`) — a denial's own verbatim statement/receipt
    must never "independently affirm" the very concept it denies (a live
    vault's testimony recording a denial of "hands-on embedding model
    configuration" necessarily contains the word "embeddings", and that word
    survived into the corpus verbatim, defeating the ADR-059 denial floor).
    ``None``/empty tolerant.

    The two other callers of this function (``services/cv.py``,
    ``services/cover_letter.py``) feed the SAME class of "is this term
    literally grounded in the vault" check (the ATS/Oracle
    ``present_unsupported`` consistency guard) — a denied term's own
    statement text must not count as grounding there either, so the
    exclusion applies unconditionally for every caller; no parameter added.
    """
    if not profile_json:
        return ""
    from applire.services.ats_audit import _norm as ats_norm

    return ats_norm(" ".join(_draft_strings(_strip_denial_text(profile_json))))


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
    vault_corpus = profile_literal_corpus(profile_json)
    ledger = _enforce_denial_stance(ledger, denied_concepts, vault_corpus or None)
    # #260: final pass — stamp narrative_backed so downstream consumers (the
    # pre-generation summary, the agent-channel ledger surface) can single
    # out a claimable-but-unstoried hard requirement without re-deriving it.
    return _annotate_narrative_backed(ledger, profile_json)
