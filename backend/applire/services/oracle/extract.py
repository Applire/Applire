# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243 — claim extraction for the Truthfulness Oracle (ADR-052 §2).

Structured documents (``tailored_data`` / ``letter_data``) are segmented into
claims fully deterministically — bullets are claims, prose fields are split
into sentences. Letter sentences are further split into clause-level claims
(:func:`split_clauses`, #237) and, where the sentence names exactly one known
employer/project, stamped with that role's id (:func:`extract_claims_from_letter`)
— the same ``source_experience_id`` anchor the CV path stamps from
``TailoredWorkEntry.id`` (US187), making the v2 attribution matcher (#196)
reachable for letters. The ONLY LLM touchpoint is the free-prose fallback for
raw external text whose blocks exceed :data:`ORACLE_PROSE_FALLBACK_CHARS`
without a single deterministic sentence boundary, and that call is bounded by
contract (ADR-047).
"""
from __future__ import annotations

import re
from typing import Any

from applire.constants import (
    ORACLE_MAX_SEGMENT_CALLS,
    ORACLE_PROSE_FALLBACK_CHARS,
    ORACLE_SEGMENT_MAX_TOKENS,
)
from applire.schemas.oracle import Claim
from applire.services.ats_audit import skill_tokens

# Dotted abbreviations that must not terminate a sentence (DE + EN). Matching
# is case-sensitive on purpose: "No." the abbreviation is title-cased, while a
# sentence ending in "no." is a real boundary.
_ABBREVIATIONS = (
    "z.B.", "z. B.", "d.h.", "d. h.", "u.a.", "u. a.", "bzw.", "ggf.",
    "inkl.", "ca.", "vs.", "e.g.", "i.e.", "etc.", "approx.",
    "Dr.", "Prof.", "Nr.", "No.",
)
_SENTINEL = "\x00"

_BULLET_RE = re.compile(r"^[-•*–—]\s+(.*)$")
_DECIMAL_DOT_RE = re.compile(r"(?<=\d)\.(?=\d)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_MIN_CLAIM_CHARS = 3

# ── #237 — clause-level decomposition + employer anchoring for letters ──────
# Real-model letter prose uses typographic punctuation; fold it to ASCII
# before anchor matching (the U+2019 lesson, 2026-07-11 — same pattern as
# oracle/stance.py's ``_APOSTROPHES``).
_APOSTROPHE_CHARS = "’ʼ‘‛´`"
_DASH_CHARS = "‒–—―−"


def _normalize_punct(text: str) -> str:
    out = text
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    for ch in _DASH_CHARS:
        out = out.replace(ch, "-")
    return out


# ── #248 — legal-form-suffix tolerance for LOOSE company-name matching ──────
# Independent copy of ``services.profile.reconcile.attribution``'s
# ``_LEGAL_FORM_RE`` / ``_core_company_name`` (same rationale as this module's
# own independent copy of ``_split_sentences`` there: oracle depends on the
# reconcile write path's OUTPUT, never the reverse, and neither module wants
# a cross-package import for a few dozen lines of regex). Ground truth
# (2026-07-24, generated_cover_letters 37ee8f77-...): the vault stores the
# full legal entity name ("NordPharm SE"), but real-model letter prose almost
# never repeats it ("at NordPharm") — used ONLY for the LOOSE, ambiguity-
# tolerant signals (``letter_named_experience_ids`` / ``Claim.
# sentence_named_ids``), never for the STRICT per-claim anchor
# (``_find_employer_anchor``), which keeps its existing exact-name, fail-
# open-on-ambiguity behaviour unchanged (zero regression risk there).
_LEGAL_FORM_RE = re.compile(
    r"\s+(?:SE|AG|GmbH(?:\s*&\s*Co\.?\s*KG)?|gGmbH|mbH|KG|OHG|GbR|"
    r"e\.\s?V\.?|Inc\.?|LLC|Ltd\.?|Co\.?|Corp\.?|Corporation|PLC|LLP)\.?\s*$",
    re.IGNORECASE,
)


def _core_company_name(name: str) -> str:
    """Legal-form-stripped company name for LOOSE anchor matching."""
    stripped = _LEGAL_FORM_RE.sub("", name.strip())
    return stripped.strip() or name.strip()


# ── #237 round-3 (live MCP probe residual, 2026-07-24) — employer-fact
# classification ─────────────────────────────────────────────────────────────
# A letter that engages the target employer (#255 now REQUIRES this) states
# facts ABOUT that company sourced from the JD ("ClaimFlow is a fast-growing
# InsurTech company.") — the ADR-021 reviewer already validates these against
# JD text; the deterministic vault audit can never ground them and must not
# mislabel them as a checkable-but-failed claim. A sentence naming the
# RECIPIENT company (legal-form-suffix tolerant, like the loose anchor
# signals) with NO first-person pronoun anywhere (EN+DE) is an employer fact;
# a follow-up sentence in the SAME paragraph that names nothing of its own
# but ALSO carries no first-person pronoun continues the classification
# ("Its AI platform..." pronoun-referring back) — mirrors the anchor-
# continuation mechanism (#237 run-4). A first-person sentence — even one
# that ALSO names the recipient ("I'm excited to join ClaimFlow.") — always
# breaks the run: the pronoun is a hard disqualifier, checked first.
_FIRST_PERSON_RE = re.compile(
    r"\b(?:"
    # EN
    r"i|i'm|i've|i'd|i'll|my|me|mine|myself|"
    r"we|we're|we've|our|ours|us|"
    # DE
    r"ich|mein|meine|meiner|meinem|meinen|meins|"
    r"wir|unser|unsere|unserer|unserem|unseren|uns|mir|mich"
    r")\b",
    re.IGNORECASE,
)


def _mentions_company(text: str, company: str | None) -> bool:
    if not company or not company.strip():
        return False
    core = _core_company_name(company.strip())
    pattern = re.compile(r"\b" + re.escape(_normalize_punct(core)) + r"\b", re.IGNORECASE)
    return bool(pattern.search(_normalize_punct(text)))


# ── courtesy/meta formula filter (adversarial-pass residual, 2026-07-23) ────
# An entirely honest letter still scored unverifiable-dominated because pure
# courtesy openers/closers ("I am writing to express my interest…", "Thank
# you for your time and consideration.") were extracted as claims and, having
# no vault-checkable content, piled into the unverifiable bucket. These are
# formulas, not factual claims about the candidate, so they must never be
# extracted at all — but conservatively: a clause that ALSO carries a factual
# assertion keeps its full original text as a real claim (see
# ``_is_pure_formula_clause`` below).
#
# Each pattern's own bounded tail (where present) consumes the typical
# short "for/in the {role} at {company}" framing that belongs to the SAME
# formula, not a separate fact — real multi-fact sentences bolt a second
# fact on via a clause-boundary conjunction (``split_clauses`` above already
# isolates that fact into its own clause before this filter ever runs). The
# tail is length-bounded (not ``.*``) so it cannot silently swallow an
# unrelated later fact within the same unsplit clause.
_ROLE_TAIL = r"(?:\s+(?:for|in)\s+.{0,90})?"

_FORMULA_SEED_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # EN openers
        rf"i am writing to (?:you )?(?:express my (?:strong |keen |sincere )?interest|apply){_ROLE_TAIL}",
        rf"i am (?:excited|thrilled|pleased|delighted|honou?red) to apply{_ROLE_TAIL}",
        r"with (?:great|keen|genuine|much) (?:interest|enthusiasm)",
        r"it is with (?:great|much) pleasure",
        # EN closers / availability
        r"thank you for (?:your|the) (?:time|consideration|attention)",
        r"i appreciate (?:your|the) (?:time|consideration)",
        r"please do not hesitate to contact me",
        r"i (?:remain|am) available "
        r"(?:for an interview|at your convenience|"
        r"to discuss(?: my| the)? notice period|to discuss)",
        r"at your earliest convenience",
        r"i would welcome the opportunity to "
        r"(?:discuss(?: how (?:my|our) (?:background|experience|skills)(?: in)?)?"
        r"|explore how (?:my|our) skills "
        r"(?:can|could|might) (?:support|contribute to|benefit))",
        r"i look forward to (?:discussing|the opportunity to discuss|"
        r"your response|scheduling|speaking with you|"
        r"(?:the possibility of )?contributing to)",
        # DE openers/closers
        r"sehr geehrte[rn]?",
        r"mit freundlichen gr[uü]ßen",
        r"mit (?:gro[ßs]em|gro[ßs]er|viel) interesse",
        r"vielen dank f[uü]r ihre (?:zeit|aufmerksamkeit|ber[uü]cksichtigung)",
        r"ich freue mich (?:auf ein (?:pers[oö]nliches )?gespr[aä]ch|[uü]ber die m[oö]glichkeit)",
        r"stehe ich (?:ihnen )?gerne (?:f[uü]r ein gespr[aä]ch )?zur verf[uü]gung",
    )
]

# Residual filler that survives seed removal but still carries no candidate
# fact (the reader-facing framing of the SAME formula, not the applicant's
# own evidence) — only ever subtracted from a clause a seed already matched.
_FORMULA_FRAMING_WORDS = frozenset(
    {
        "role", "position", "opportunity", "team", "company", "organisation",
        "organization", "firm", "employer", "consideration", "convenience",
        "interview", "application", "vacancy", "opening", "advertised",
        "regarding", "concerning", "this", "that", "further", "it",
        "write", "writing", "you", "your", "i", "my",
        # DE
        "stelle", "unternehmen", "gelegenheit", "bewerbung", "vorstellungsgespraech",
        "vorstellungsgespräch", "gespraech", "gespräch", "beruecksichtigung",
        "berücksichtigung", "zeit", "aufmerksamkeit", "interesse",
        "damen", "herren", "und", "sowie",
    }
)


def _is_pure_formula_clause(text: str) -> bool:
    """True when ``text`` is a courtesy/meta formula with no substantive claim.

    Conservative by construction: only clauses that match at least one known
    formula seed are considered at all, and even then only when NO content
    tokens survive after stripping the matched seed(s) and the reader-facing
    framing words (reusing ``skill_tokens`` — the shared tokenizer, never a
    fork). A clause naming no formula seed, or one that keeps a real fact
    after the formula is removed, is left untouched and extracted as usual.
    """
    normalized = _normalize_punct(text)
    residual = normalized
    matched = False
    for pattern in _FORMULA_SEED_PATTERNS:
        new_residual, n = pattern.subn(" ", residual)
        if n:
            matched = True
            residual = new_residual
    if not matched:
        return False
    return not (skill_tokens(residual) - _FORMULA_FRAMING_WORDS)


def _strip_formula_prefix(text: str) -> str:
    """Trim a RECOGNIZED courtesy/framing PREFIX from a clause, keeping any
    substantive remainder as the claim text (#237 round-3) — the partial
    counterpart of :func:`_is_pure_formula_clause`'s all-or-nothing drop.

    "I would welcome the opportunity to discuss how my background in
    backend engineering, production LLM applications, and mentoring aligns
    with your needs." carries a real, checkable competence list — but the
    courtesy preamble ("I would welcome the opportunity to discuss how my
    background in") dilutes it below the grounding coverage floor if kept
    verbatim. ANCHORED at the START of the (punctuation-normalized) clause
    only (``pattern.match``, never ``.search``) — a formula phrase
    appearing mid-clause is never trimmed, only ever a leading preamble, so
    an unrelated real sentence's own grammar is never corrupted.
    ``_normalize_punct`` is length-preserving (1 char -> 1 char
    substitutions only), so the match end index is reused directly against
    the ORIGINAL text — the returned remainder keeps the writer's own
    punctuation/casing, only the recognized prefix itself is dropped.
    Returns ``text`` unchanged when no prefix matches, or when the
    remainder would carry no real content (fails safe to the original,
    unmodified clause — the drop decision stays ``_is_pure_formula_clause``'s
    alone).
    """
    normalized = _normalize_punct(text)
    for pattern in _FORMULA_SEED_PATTERNS:
        m = pattern.match(normalized)
        if not m:
            continue
        remainder = text[m.end():].strip(" ,.;:-")
        if remainder and (skill_tokens(remainder) - _FORMULA_FRAMING_WORDS):
            return remainder
    return text


# ── #282 (wave 7) — honest gap disclaimer classification ────────────────────
# A PURE denial or third-party delegation clause ("I have not configured X
# myself"; "X was handled by our system engineer") has no positive claim to
# ground — the vault holds no "evidence of absence". Marker list mirrors
# ``choice_grounding.py``'s ``_HONESTY_MARKERS`` in SPIRIT (same "name the
# denied term, don't claim it" shape), but is an independent copy: that
# module classifies interview STARTING-POINT CHIPS (a different document
# shape and marker set — "closest experience", no delegation phrasing at
# all), and oracle/extract.py already keeps its own independent copies of
# nearby primitives (module docstring, ``_core_company_name``) for the same
# one-way-dependency reason. Deliberately EN+DE, matching every other
# classifier in this module.
_DENIAL_MARKERS: tuple[str, ...] = (
    # EN — first-person negation
    "have not", "haven't", "has not", "hasn't", "had not", "hadn't",
    "do not have", "don't have", "does not have", "doesn't have",
    "did not", "didn't",
    "nor have i", "nor did i", "nor do i", "nor has",
    "i lack", "lacking direct", "lacks direct",
    "never worked", "never configured", "never led", "never managed",
    "never set up",
    "no direct experience", "not directly",
    # EN — third-party delegation ("X was handled by someone else")
    "was handled by", "were handled by", "is handled by", "are handled by",
    "handled by our", "handled by the", "handled by a",
    "was managed by", "were managed by", "was owned by", "were owned by",
    # DE
    "habe ich nicht", "hatte ich nicht", "keine direkte erfahrung",
    "keine eigene erfahrung", "noch nie", "noch keine",
    "wurde von", "wurde durch", "wurden von", "wurden durch",
    "fehlt mir", "mir fehlt",
)

# A clause/comma-segment that STARTS with (an optional pivot word, then) a
# first-person subject pronoun is a fresh, independent clause riding along
# with the denial — the #207/#278 "attribute a negation to its own clause,
# never a co-occurring sibling" lesson. Matched against a segment that does
# NOT itself carry a denial marker (see ``_is_pure_denial_clause`` below) —
# the denial clause's OWN leading pronoun ("I have not configured...") must
# never be mistaken for a smuggled sibling.
_DENIAL_PIVOT_THEN_PRONOUN_RE = re.compile(
    r"^\s*(?:though|but|however|yet|still|nevertheless|nonetheless|while|"
    r"aber|dennoch|jedoch|allerdings)?\s*"
    r"(?:i|ich|we|wir)\b",
    re.IGNORECASE,
)

_DENIAL_SEGMENT_SPLIT_RE = re.compile(r"[;,]\s+")

# A delegation marker only distances the candidate when the work went to
# SOMEBODY ELSE. The passive voice is equally at home in an OWNERSHIP claim —
# "release planning was owned by me", "das Backend wurde von mir entwickelt" —
# and misreading one of those as a denial would route a genuine positive claim
# to ``not_applicable``, exempting it from verification entirely: a hole in the
# Oracle, exactly what this module's conservatism exists to prevent. So a
# delegation marker whose recipient is FIRST-PERSON is not a delegation at all.
# First-person singular/possessive only ("by me", "by my team", "von mir"):
# "handled by our system engineer" names a real second party and stays a
# delegation, while the genuinely ambiguous "my team" falls to the safe side
# and stays gradeable.
_SELF_DELEGATION_RE = re.compile(
    r"\b(?:by|von|durch)\s+(?:me|my|mir|mich|meine[mnrs]?|meiner)\b",
    re.IGNORECASE,
)


def _is_pure_denial_clause(text: str) -> bool:
    """True when ``text`` is ENTIRELY a denial/delegation statement — no
    smuggled positive claim riding along in the same clause (#282).

    Conservative by construction, mirroring ``_is_pure_formula_clause``'s
    shape: the clause must name at least one denial marker at all, must not
    be a passive OWNERSHIP claim in disguise (``_SELF_DELEGATION_RE``), AND
    none of its comma/semicolon-delimited segments may look like an
    independent, non-negated first-person clause. A segment that itself
    carries a denial marker is never treated as smuggled — including the
    denial's own leading "I" ("I have not configured embedding models" is
    one segment, not two). When in doubt this returns ``False`` (stay
    gradeable) — a false ``not_applicable`` is a hole in the Oracle; a false
    ``unverifiable`` is merely noise.
    """
    normalized = _normalize_punct(text).lower()
    if not any(marker in normalized for marker in _DENIAL_MARKERS):
        return False
    if _SELF_DELEGATION_RE.search(normalized):
        return False
    for segment in _DENIAL_SEGMENT_SPLIT_RE.split(normalized):
        segment = segment.strip()
        if not segment:
            continue
        if any(marker in segment for marker in _DENIAL_MARKERS):
            continue
        if _DENIAL_PIVOT_THEN_PRONOUN_RE.match(segment):
            return False
    return True


# Clause boundaries: a semicolon, a comma followed by a coordinating
# conjunction/preposition that typically introduces a bolted-on second fact
# (EN+DE), or a spaced em/en-dash. The delimiter itself is consumed — it
# never survives into either resulting clause. Deliberately NOT a bare
# " and "/" und " (no comma) — that would shred ordinary noun phrases
# ("Python and Java") the letter path never sees as bullets.
_CLAUSE_BOUNDARY_RE = re.compile(
    r";\s+"
    r"|,\s+(?:with|including|and|und|einschließlich|inklusive|mit)\s+"
    rf"|\s[{_DASH_CHARS}-]\s",
    re.IGNORECASE,
)

# #237 round-3 (live MCP probe residual, 2026-07-24): EM-DASH specifically
# (U+2014) — never the broader ``_DASH_CHARS`` set, which also carries
# en-dash (U+2013, commonly an unspaced DATE-RANGE separator, "2020–2023",
# that must never be treated as a clause boundary). Real-model prose pairs
# em-dashes, unspaced, around a parenthetical aside ("background—building
# X, Y, and Z—gives me..."). The general boundary regex above requires
# surrounding WHITESPACE and so never sees an unspaced pair at all — the
# aside's OWN internal Oxford-comma "and" (which DOES have surrounding
# whitespace) wins instead, mid-fragmenting the aside's enumeration list
# ("...workflows, audit trails" | "and validation reports...").
_EM_DASH = "—"


def split_clauses(text: str) -> list[str]:
    """Deterministic clause-level split for narrative sentences (#237).

    A multi-fact sentence — "At NordPharm, I led AI automation projects …
    with comprehensive testing, observability, and reliability practices."
    — almost never clears ``GROUNDED_MIN_COVERAGE`` as a single claim: its
    content tokens span an employer, an activity, and several unrelated
    practice areas, so no single vault evidence unit can cover 60% of them.
    Splitting on clause boundaries turns it into several smaller,
    independently checkable claims. Falls back to the whole sentence when no
    boundary is found — the common case stays a single ``sentence`` claim.

    #237 round-3: a text carrying EXACTLY TWO em-dashes is treated as a
    paired parenthetical aside FIRST — the whole aside becomes its own
    single clause (never re-split by the general boundary rules below, which
    would re-fragment its own internal enumeration) — before falling back to
    the general boundary regex for any other shape (a single, unpaired
    em-dash keeps using the pre-existing spaced-dash rule below unchanged).
    """
    t = (text or "").strip()
    if not t:
        return []
    if t.count(_EM_DASH) == 2:
        first, second = t.index(_EM_DASH), t.rindex(_EM_DASH)
        parts = [t[:first].strip(), t[first + 1 : second].strip(), t[second + 1 :].strip()]
        return [p for p in parts if p]
    parts = [p.strip() for p in _CLAUSE_BOUNDARY_RE.split(t)]
    return [p for p in parts if p]


def _profile_get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _employer_anchor_candidates(
    profile: Any | None, *, loose: bool = False
) -> list[tuple[str, str]]:
    """(surface name, experience id) pairs a letter sentence can anchor to.

    Covers work-experience company names and project names (own id, or the
    parent work id when ``associated_experience`` resolves — mirroring the
    US187 nesting rule vault.py already applies). Duck-types over a
    ``MasterProfileData`` or its raw JSONB dict — extraction runs before the
    vault index exists, so it never requires a full model coercion.

    ``loose=True`` (#248) strips common legal-form suffixes from COMPANY
    names only (never project names, which don't carry them) — used
    exclusively by the ambiguity-tolerant signals (``letter_named_
    experience_ids`` / per-sentence ``sentence_named_ids``). The STRICT
    per-claim anchor (``_find_employer_anchor``) always calls this with the
    default ``loose=False`` — its exact-name, fail-open-on-ambiguity
    behaviour is unchanged by #248.
    """
    if profile is None:
        return []
    pairs: list[tuple[str, str]] = []
    work_ids: set[str] = set()
    for w in _profile_get(profile, "work_experience") or []:
        wid = _profile_get(w, "id")
        company = _profile_get(w, "company")
        if isinstance(wid, str) and wid.strip() and isinstance(company, str) and company.strip():
            wid = wid.strip()
            work_ids.add(wid)
            name = _core_company_name(company.strip()) if loose else company.strip()
            pairs.append((name, wid))

    for pr in _profile_get(profile, "projects") or []:
        name = _profile_get(pr, "name")
        pid = _profile_get(pr, "id")
        assoc = _profile_get(pr, "associated_experience")
        target = pid.strip() if isinstance(pid, str) and pid.strip() else None
        if isinstance(assoc, str) and assoc.strip() and assoc.strip() in work_ids:
            target = assoc.strip()
        if isinstance(name, str) and name.strip() and target:
            pairs.append((name.strip(), target))

    return pairs


def _match_ids(text: str, candidates: list[tuple[str, str]]) -> frozenset[str]:
    """Every candidate id whose name appears in ``text`` (word-boundary,
    case-insensitive, punctuation-normalized) — no uniqueness constraint.

    The shared primitive behind the STRICT per-claim anchor
    (:func:`_find_employer_anchor`, which additionally requires exactly one
    result) and every LOOSE, ambiguity-tolerant signal (whole-letter
    :func:`letter_named_experience_ids`, per-sentence ``sentence_named_ids``,
    #248) — callers decide what "ambiguous" means for their own purpose.
    """
    if not candidates:
        return frozenset()
    normalized_text = _normalize_punct(text)
    found: set[str] = set()
    for name, entity_id in candidates:
        pattern = re.compile(
            r"\b" + re.escape(_normalize_punct(name)) + r"\b", re.IGNORECASE
        )
        if pattern.search(normalized_text):
            found.add(entity_id)
    return frozenset(found)


def _current_work_ids(profile: Any | None) -> frozenset[str]:
    """Ids of every ``is_current`` work-experience entry (#237 run-4
    residual) — the same-company multi-role tie-break below needs to know
    which of several ambiguous candidates is the CURRENT position."""
    if profile is None:
        return frozenset()
    ids: set[str] = set()
    for w in _profile_get(profile, "work_experience") or []:
        wid = _profile_get(w, "id")
        if (
            isinstance(wid, str)
            and wid.strip()
            and bool(_profile_get(w, "is_current"))
        ):
            ids.add(wid.strip())
    return frozenset(ids)


def _find_employer_anchor(
    sentence: str,
    candidates: list[tuple[str, str]],
    current_ids: frozenset[str] = frozenset(),
    loose_candidates: list[tuple[str, str]] | None = None,
) -> str | None:
    """The experience id a sentence anchors to, or ``None`` (fail open).

    A sentence naming EXACTLY one known employer/project stamps every claim
    derived from it — "At NordPharm," / "bei NordPharm" (DE) alike, since
    matching is plain normalized substring containment, prefix-agnostic. A
    sentence naming two or more distinct known employers stays unanchored
    rather than risk mis-anchoring (adversarial-review lesson: fail open,
    never fail wrong).

    #237 (run-4 residual, DEVIATION from the #248 "strict anchor exact-name
    behaviour is unchanged" pin — see ``test_oracle_extract.py``'s updated
    ``test_strict_anchor_now_tolerates_legal_form_suffix_via_current_role_
    tiebreak``): live-reproduced 2026-07-24 (run-4 self-audit, 10/14
    unverifiable) — the exact-name-only strict anchor NEVER fires for a
    company whose vault name carries a legal-form suffix ("NordPharm SE")
    when the letter naturally drops it ("At NordPharm"), which is the
    COMMON case, not the exception. That starves the attribution matcher of
    the anchor it exists to feed, and is the single largest reason a
    realistic multi-role-tenure letter scored near-zero discriminating
    power. Two additive widenings, both still fail-open by construction:

    1. When the EXACT candidate set matches NOTHING at all (not merely
       ambiguous), retry against ``loose_candidates`` (legal-form-suffix
       tolerant) if provided.
    2. A long tenure at ONE company held across several internal roles
       (e.g. three successive NordPharm positions) matches every one of them
       by company name — genuinely ambiguous by name alone. When the
       ambiguity is PURELY "which era at the SAME company" (every found
       candidate shares one surface name) and EXACTLY one of them is
       ``current_ids``' current position, resolve to that one: "At Company
       X, I did Y" conventionally reads as the CURRENT role absent any
       other signal.

    Both still fail open the moment names genuinely differ (a true
    multi-employer sentence) or the tie-break itself can't decide (more
    than one/none of the found candidates is current) — this narrows, never
    removes, the existing fail-open guarantee.
    """
    found = _match_ids(sentence, candidates)
    if not found and loose_candidates:
        found = _match_ids(sentence, loose_candidates)
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        names = {n for n, i in (loose_candidates or candidates) if i in found}
        if len(names) == 1:
            current_found = found & current_ids
            if len(current_found) == 1:
                return next(iter(current_found))
    return None


def letter_named_experience_ids(
    letter_data: dict[str, Any] | None, profile: Any | None
) -> frozenset[str]:
    """Every experience id whose employer/project name is named ANYWHERE in
    the letter body (#243-adjacent, oracle figure-ownership check).

    Unlike :func:`_find_employer_anchor` (per-sentence, requires EXACTLY one
    match to stamp a claim's own attribution), this scans the WHOLE letter
    and keeps every id it finds, ambiguity included — a letter legitimately
    names several employers across different paragraphs. Used only to decide
    whether an UNANCHORED claim's owned-only vault backing belongs to a
    position the letter simply never mentions (genuinely unattributable, see
    ``audit.verify_claim``'s ``letter_named_ids``) vs one it names elsewhere
    (legitimate — full per-clause attribution just isn't provable).

    Legal-form-suffix tolerant (``loose=True``, #248): the vault stores the
    full legal entity name ("NordPharm SE"); real letter prose rarely repeats
    it. A whole-document scan is where this matters most — missing it here
    silently mis-widens the "letter names exactly one employer" escape
    hatch below into treating a genuinely multi-employer letter as single-
    employer (ground truth: generated_cover_letters 37ee8f77-...).
    """
    candidates = _employer_anchor_candidates(profile, loose=True)
    if not candidates:
        return frozenset()
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    text = " ".join(p for p in (paragraphs or []) if isinstance(p, str))
    return _match_ids(text, candidates)


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence split with abbreviation and decimal guards."""
    t = (text or "").strip()
    if not t:
        return []
    protected = t
    for abbrev in _ABBREVIATIONS:
        protected = protected.replace(abbrev, abbrev.replace(".", _SENTINEL))
    protected = _DECIMAL_DOT_RE.sub(_SENTINEL, protected)
    sentences = []
    for part in _SENTENCE_SPLIT_RE.split(protected):
        restored = part.replace(_SENTINEL, ".").strip()
        if restored:
            sentences.append(restored)
    return sentences


def _sentence_claims(text: str, prefix: str) -> list[Claim]:
    return [
        Claim(text=s, location=f"{prefix}[{i}]", kind="sentence")
        for i, s in enumerate(split_sentences(text))
        if len(s) >= _MIN_CLAIM_CHARS
    ]


def _bullet_claims(
    bullets: Any, prefix: str, source_id: str | None = None
) -> list[Claim]:
    claims: list[Claim] = []
    if not isinstance(bullets, list):
        return claims
    for i, b in enumerate(bullets):
        if isinstance(b, str) and len(b.strip()) >= _MIN_CLAIM_CHARS:
            claims.append(
                Claim(
                    text=b.strip(),
                    location=f"{prefix}[{i}]",
                    kind="bullet",
                    source_experience_id=source_id,
                )
            )
    return claims


def extract_claims_from_tailored(tailored_data: dict[str, Any]) -> list[Claim]:
    """Claims from a generated CV's ``tailored_data`` — deterministic, no LLM.

    Covered surfaces: summary sentences, work/project bullets (incl. projects
    nested under positions, US187), standalone project bullets, and the skills
    list (each skill is a checkable claim — the #192 skill-dump lesson).
    Certifications/education/languages are copied deterministically from the
    vault by the pipeline (PQ F7 / ADR-040) and are not re-audited in v1.
    """
    data = tailored_data or {}
    claims: list[Claim] = []
    claims += _sentence_claims(data.get("summary") or "", "summary")

    for wi, entry in enumerate(data.get("work_history") or []):
        if not isinstance(entry, dict):
            continue
        # TailoredWorkEntry.id = the source WorkEntry.id (US187) — the rendered
        # position, anchoring the v2 role-attribution check (#196). Empty for
        # legacy/mock data → None, and the matcher stays silent.
        source_id = entry.get("id") or None
        if not isinstance(source_id, str):
            source_id = None
        claims += _bullet_claims(
            entry.get("bullets"), f"work_history[{wi}].bullets", source_id
        )
        for pi, proj in enumerate(entry.get("projects") or []):
            if isinstance(proj, dict):
                claims += _bullet_claims(
                    proj.get("bullets"),
                    f"work_history[{wi}].projects[{pi}].bullets",
                    source_id,
                )

    for pi, proj in enumerate(data.get("projects") or []):
        if isinstance(proj, dict):
            claims += _bullet_claims(proj.get("bullets"), f"projects[{pi}].bullets")

    for si, skill in enumerate(data.get("skills") or []):
        if isinstance(skill, str) and len(skill.strip()) >= _MIN_CLAIM_CHARS:
            claims.append(Claim(text=skill.strip(), location=f"skills[{si}]", kind="skill"))

    return claims


def extract_claims_from_letter(
    letter_data: dict[str, Any], profile: Any | None = None
) -> list[Claim]:
    """Claims from a cover letter's ``letter_data`` body — deterministic.

    #237 (F14): a whole narrative sentence almost never clears the grounding
    coverage floor, and letters structurally could not stamp
    ``source_experience_id`` at all — so a misattributed blend (real
    NordPharm achievement + unrelated interview evidence, blended into one
    sentence) filed as merely "unverifiable". Additive steps fix this
    without touching the frozen writer:

    1. Each sentence is decomposed into clause-level claims
       (:func:`split_clauses`) — smaller, independently checkable fragments.
       A sentence with no clause boundary stays a single ``sentence`` claim
       at the same location as before (backward compatible).
    2. When the sentence names EXACTLY one employer/project from ``profile``
       (:func:`_find_employer_anchor`), every claim derived from it is
       stamped with that role's id — making the v2 attribution matcher
       (#196) reachable for letters for the first time.
    3. (#248) When the SENTENCE itself is ambiguous (names two or more
       employers, or none) but has more than one clause, each CLAUSE is
       re-checked independently: a clause naming EXACTLY one employer gets
       its OWN anchor even though the sentence as a whole couldn't decide —
       the writer-blend signature is a sentence naming two employers with
       one clause belonging to each, and sentence-level fail-open used to
       launder exactly that case. A clause naming none stays unanchored.
    4. (#248) Every clause/claim also carries ``sentence_named_ids`` — every
       employer/project LOOSELY named (legal-form-suffix tolerant, ambiguity
       tolerated) anywhere in its OWN enclosing sentence. This is deliberately
       more permissive than the strict per-claim anchor: it lets the
       non-figure ownership check (``audit._unattributable_evidence_flag``)
       recognise "this clause's own sentence already names its true owner"
       even when the strict anchor stayed ``None`` (e.g. the vault's legal
       entity name vs. the letter's shortened mention, or same-company
       duplicate ids) — see ground truth in ``test_oracle_extract.py``'s
       #248 section and ``test_oracle_letter_nonfigure_ownership.py``.
    5. (#237 run-4 residual) PARAGRAPH-SCOPED anchor continuation: once a
       sentence anchors, that anchor CARRIES FORWARD to later sentences in
       the SAME paragraph that name no employer of their own — the common
       letter shape "At Company X, I did A. This/It also enabled B." where
       only the first sentence names the employer. A sentence that names
       something of its own (anchored or ambiguously not) neither inherits
       nor silently keeps the old anchor for ITSELF, and the carry NEVER
       crosses a paragraph boundary — a new paragraph starts fresh. Also
       narrowed by the same-company ``current_ids`` tie-break above (point
       3's `_find_employer_anchor`).
    6. (#237 round-3) Every clause/claim also carries ``is_employer_fact`` —
       True for a sentence naming the RECIPIENT company (``letter_data.
       recipient.company``, legal-form-suffix tolerant) with NO first-person
       pronoun (EN/DE) anywhere, or a same-paragraph continuation of such a
       run (mirrors point 5's anchor carry). A first-person sentence always
       breaks the run, checked first, even when it ALSO names the
       recipient. ``audit.verify_claim`` short-circuits these to
       ``not_applicable`` before any vault-grounding attempt — see this
       module's own top-of-file section for the rationale.
    """
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    candidates = _employer_anchor_candidates(profile)
    loose_candidates = _employer_anchor_candidates(profile, loose=True)
    current_ids = _current_work_ids(profile)
    recipient = (letter_data or {}).get("recipient") or {}
    recipient_company = recipient.get("company") if isinstance(recipient, dict) else None
    claims: list[Claim] = []
    for pi, para in enumerate(paragraphs or []):
        if not isinstance(para, str):
            continue
        carried_anchor: str | None = None
        in_employer_fact_run = False
        for si, sentence in enumerate(split_sentences(para)):
            if len(sentence) < _MIN_CLAIM_CHARS:
                continue
            sentence_anchor = _find_employer_anchor(
                sentence, candidates, current_ids, loose_candidates
            )
            sentence_named = _match_ids(sentence, loose_candidates)
            if sentence_anchor is not None:
                carried_anchor = sentence_anchor
                effective_anchor = sentence_anchor
            elif not (_match_ids(sentence, candidates) or sentence_named):
                # Names no employer of its own at all (exact or loose) —
                # inherit the paragraph's last established anchor, if any.
                effective_anchor = carried_anchor
            else:
                # Names something, but not resolvably (ambiguous) — never
                # guess; also never overwrite the carried anchor with this
                # sentence's own failure to resolve.
                effective_anchor = None
            # #237 round-3: employer-fact classification (module docstring
            # point 6, below) — a hard first-person disqualifier, then
            # either a fresh recipient-company mention or a same-paragraph
            # continuation of an already-established run.
            sentence_has_first_person = bool(_FIRST_PERSON_RE.search(sentence))
            sentence_is_employer_fact = not sentence_has_first_person and (
                _mentions_company(sentence, recipient_company) or in_employer_fact_run
            )
            clauses = split_clauses(sentence)
            base = f"body.paragraphs[{pi}][{si}]"
            multi = len(clauses) > 1
            # #282 (wave 7): the run carry, like the anchor carry, reflects
            # this sentence's OWN tail state for the NEXT sentence — updated
            # below as clauses are processed, defaulting to the sentence-
            # level determination when every clause is filtered out.
            run_state_for_next_sentence = sentence_is_employer_fact
            for ci, clause in enumerate(clauses):
                if len(clause) < _MIN_CLAIM_CHARS:
                    continue
                if _is_pure_formula_clause(clause):
                    continue
                clause_anchor = effective_anchor
                if clause_anchor is None and multi:
                    # #248 direction 1: the sentence itself was ambiguous
                    # (two+ employers) or named none — give this CLAUSE its
                    # own chance to anchor independently.
                    clause_anchor = _find_employer_anchor(
                        clause, candidates, current_ids, loose_candidates
                    )
                # A clause-level safety net: a multi-clause sentence could in
                # principle separate a first-person clause from an
                # employer-fact one — re-check at clause granularity too.
                clause_is_employer_fact = sentence_is_employer_fact and not (
                    multi and _FIRST_PERSON_RE.search(clause)
                )
                # #237 round-3: trim a recognized courtesy PREFIX (anchor
                # detection above already ran against the FULL, untrimmed
                # clause — the stored claim TEXT changes here).
                final_text = _strip_formula_prefix(clause)
                # #282 (wave 7): a courtesy PREFIX fused with a company-
                # descriptive tail in ONE unsplit sentence ("I am writing to
                # express my interest in X, a company whose platform serves
                # customers.") wrongly failed the employer-fact check above
                # — the "I" that disqualifies it lives entirely in the
                # PREFIX that was just stripped away, not in the RETAINED
                # claim text shown to the user. Re-classify against that
                # retained text whenever a prefix was actually removed and
                # the remainder itself carries no first-person pronoun of
                # its own — narrower than the general check above, so every
                # other path (no prefix stripped, or the remainder keeps its
                # own "I") is unaffected.
                if final_text != clause and not _FIRST_PERSON_RE.search(final_text):
                    # The company mention itself may have lived INSIDE the
                    # discarded prefix ("...interest in the ... position at
                    # Connect-AI, a company whose platform..." -> only
                    # "platform..." survives as the stored claim text, but
                    # "Connect-AI" was real signal in the trimmed part) — so
                    # check the ORIGINAL, pre-strip clause for the mention;
                    # the retained remainder's own first-person-freedom
                    # (checked above) is what actually gates this override.
                    clause_is_employer_fact = (
                        _mentions_company(clause, recipient_company)
                        or in_employer_fact_run
                    )
                run_state_for_next_sentence = clause_is_employer_fact
                # #282 (wave 7): honest gap disclaimer / third-party
                # delegation — classified against the same RETAINED text
                # (never the pre-strip clause, for the identical reason as
                # the employer-fact re-check just above).
                clause_is_denial = _is_pure_denial_clause(final_text)
                claims.append(
                    Claim(
                        text=final_text,
                        location=f"{base}.clauses[{ci}]" if multi else base,
                        kind="clause" if multi else "sentence",
                        source_experience_id=clause_anchor,
                        sentence_named_ids=sentence_named,
                        is_employer_fact=clause_is_employer_fact,
                        is_denial=clause_is_denial,
                    )
                )
            in_employer_fact_run = run_state_for_next_sentence
    return claims


_SEGMENT_PROMPT = (
    "Split the following resume/cover-letter prose into its individual factual "
    "claims (one short statement each). Return STRICT JSON: "
    '{{"claims": ["...", "..."]}}. Do not rephrase, do not add or drop content — '
    "segment only.\n\nTEXT:\n{text}"
)


async def _segment_prose_llm(text: str, provider: Any) -> list[str]:
    """ADR-047 bounded-output-by-contract prose segmentation fallback."""
    try:
        result = await provider.aparse_json(
            _SEGMENT_PROMPT.format(text=text),
            temperature=0.0,
            max_tokens=ORACLE_SEGMENT_MAX_TOKENS,
        )
    except Exception:
        return [text]
    claims = result.get("claims") if isinstance(result, dict) else None
    if not isinstance(claims, list):
        return [text]
    cleaned = [c.strip() for c in claims if isinstance(c, str) and c.strip()]
    return cleaned or [text]


async def extract_claims_from_text(text: str, provider: Any | None = None) -> list[Claim]:
    """Claims from raw external document text (US248 audit-any-document).

    Deterministic line/bullet/sentence segmentation; the LLM fallback fires
    ONLY for a block longer than ``ORACLE_PROSE_FALLBACK_CHARS`` in which the
    deterministic splitter found no sentence boundary at all — and at most
    ``ORACLE_MAX_SEGMENT_CALLS`` times per document (adversarial review
    2026-07-18 MAJOR-2: per-line fan-out on the agent-exposed tool). Once the
    budget is spent, a qualifying block degrades to a single claim.
    """
    claims: list[Claim] = []
    idx = 0
    segment_calls_left = ORACLE_MAX_SEGMENT_CALLS

    def _add(claim_text: str, kind: str) -> None:
        nonlocal idx
        if len(claim_text.strip()) >= _MIN_CLAIM_CHARS:
            claims.append(Claim(text=claim_text.strip(), location=f"text[{idx}]", kind=kind))
            idx += 1

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            _add(bullet.group(1), "bullet")
            continue
        sentences = split_sentences(line)
        if len(sentences) <= 1 and len(line) > ORACLE_PROSE_FALLBACK_CHARS:
            if provider is not None and segment_calls_left > 0:
                segment_calls_left -= 1
                segments = await _segment_prose_llm(line, provider)
            else:
                segments = [line]
            for s in segments:
                _add(s, "sentence")
        else:
            for s in sentences:
                _add(s, "sentence")

    return claims
