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

"""ADR-039 ATS audit engine — deterministic, local-only (pypdf + stdlib).

Never imports an LLM provider; never touches the network. The *_text seam
exists so unit tests run without Chromium; extraction correctness itself is
enforced by tests/ats/test_roundtrip.py.
"""

import re
import unicodedata
from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from typing import Any, Literal

from pypdf import PdfReader

from applire.norms import DEFAULT_REGION, REGION_NORMS
from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport, KeywordMatch
from applire.schemas.cv import TailoredCVData


def extract_text_and_pages(pdf_bytes: bytes) -> tuple[str, int]:
    """Extracted text plus page count from a single PdfReader pass (#171a)."""
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = reader.pages
    text = "\n".join((page.extract_text() or "") for page in pages)
    return text, len(pages)


def extract_page_texts(pdf_bytes: bytes) -> list[str]:
    """The extracted text of each page, in order (letter v2 sizing, RULING W3-3)."""
    return [(page.extract_text() or "") for page in PdfReader(BytesIO(pdf_bytes)).pages]


def extract_text(pdf_bytes: bytes) -> str:
    return extract_text_and_pages(pdf_bytes)[0]


def _norm(s: str) -> str:
    s = s.replace("­", "")  # soft hyphens from PDF line-breaking
    s = unicodedata.normalize("NFKC", s)
    # US212 (#122): fold hyphens/dashes to spaces so "Code-Review" ≡ "code review".
    # Applied to needle and haystack alike, so matching stays symmetric.
    s = re.sub(r"[-‐-―−]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


#: #415 follow-up (2026-09-17, adversarial pass on PR #720): the boundary between two
#: FRAGMENTS of a matching corpus (a bullet, a structured-section field value, …) must
#: survive `_norm`. A bare `"\n"` does not — `_norm`'s `re.sub(r"\s+", " ", s)` collapses
#: EVERY run of whitespace, including the join itself, to one space, so two unrelated
#: fragments ("…ISO" / "9001…") read as one continuous phrase ("…iso 9001…") once joined
#: and normalised — proven on `structured_section_texts` (two certifications) and on
#: `load_bearing.stringify_draft` (the pre-#415 whole-document corpus, same shape).
#: `SYMBOL FOR RECORD SEPARATOR` (U+241E): survives NFKC unchanged, is not `\s`-matched
#: (verified), and — being neither whitespace nor a letter/digit — cannot appear inside
#: any real surface form, so neither `surface_present`'s direct substring check nor its
#: "loose" extra-space-tolerant fallback (`_find`'s `r" *".join(...)`, which only ever
#: inserts SPACES between the needle's own characters) can bridge it.
_CORPUS_FRAGMENT_BOUNDARY = "␞"


def join_corpus_fragments(parts: Sequence[str]) -> str:
    """Join independent fragments (bullets, structured-section field values, …) into
    ONE string for `_norm` + `surface_present` matching, with a boundary between
    fragments that survives `_norm`'s whitespace collapse (#415 follow-up).

    THE single join for every matching corpus assembled from more than one fragment —
    callers stop hand-rolling `"\\n".join(...)`, which silently drops the boundary once
    `_norm` runs (ADR-066: one implementation, so the fix cannot drift out of sync
    across its call sites).
    """
    return _CORPUS_FRAGMENT_BOUNDARY.join(parts)


def _find(needle: str, haystack_norm: str) -> int:
    """First index of normalised needle in pre-normalised haystack; -1 if absent or empty.

    #399: pypdf's ``extract_text()`` can insert a spurious ASCII space inside a
    kerned run of adjacent glyphs (observed on repeated-letter pairs like "ff" and
    "11" — "katrin.hoffmann@..." extracted as "katrin.hof fmann@...", "711" as
    "71 1") that poppler's ``pdftotext`` does not; the text genuinely reached the
    PDF's text layer, pypdf just mis-split it. An exact match is tried first
    (cheap, the common case); on a miss, retry allowing any number of extra spaces
    at every character boundary of the needle — this is the ONE shared match
    primitive every check (name/email/phone-adjacent/keyword/body-paragraph) goes
    through, so the tolerance applies uniformly rather than per template
    (ADR-066: one logical operation, one implementation).
    """
    n = _norm(needle)
    if not n:
        return -1
    idx = haystack_norm.find(n)
    if idx >= 0:
        return idx
    loose = r" *".join(re.escape(ch) for ch in n)
    m = re.search(loose, haystack_norm)
    return m.start() if m else -1


# US212 minimum sizes for the morphological fold: strip a trailing "s" only when the
# remaining stem keeps ≥ 4 chars ("reviews" → "review", but never "SaaS" → "saa" or
# "K8s" → "k8"); append an "s" only to an alphabetic-final token of ≥ 4 chars.
_FOLD_MIN_STEM = 4


#: #783 line L35 / #787 line L43 (2026-10-20) — closed suffix pairs of the
#: final token, each a FACT about the token's own letters (ADR-062 cl. 1).
#: Applied in both directions; the stem left in front of the suffix must keep
#: ≥ ``_FOLD_MIN_STEM`` chars. Every pair produces a SUBSTRING needle, so the
#: shorter side of a pair also reaches the inflected forms after it
#: ("zertifizier" in "zertifizierte", "produzier" in "produzierten").
#:
#: * EN ``-y`` ↔ ``-ies`` ("methodology" ↔ "methodologies");
#: * DE noun plurals ``-ung``/``-ion``/``-tät``/``-heit``/``-keit``/``-schaft``
#:   + ``en`` ("Zertifizierung" ↔ "Zertifizierungen");
#: * DE ``-ierung`` ↔ the verb stem ``-ier`` ("Zertifizierung" ↔ "zertifiziert",
#:   "Optimierung" ↔ "optimieren") and ``-ktion`` ↔ ``-zier``
#:   ("Produktion" ↔ "produziert", "Reduktion" ↔ "reduzieren").
#: Deliberately absent: any bare "-e"/"-n"/"-er" fold — too short to be a fact
#: about the word rather than a coincidence of letters. (The adjective
#: declension fold below is anchored on an adjective-forming suffix and a
#: minimum stem, and reaches non-final tokens only.)
_FOLD_SUFFIX_PAIRS: tuple[tuple[str, str], ...] = (
    ("y", "ies"),
    ("ungen", "ung"),
    ("ionen", "ion"),
    ("täten", "tät"),
    ("heiten", "heit"),
    ("keiten", "keit"),
    ("schaften", "schaft"),
    ("ierungen", "ier"),
    ("ierung", "ier"),
    ("ktionen", "zier"),
    ("ktion", "zier"),
)


def _suffix_pair_variants(last: str) -> list[str]:
    """The final token's counterparts under ``_FOLD_SUFFIX_PAIRS`` (both ways)."""
    out: list[str] = []
    for a, b in _FOLD_SUFFIX_PAIRS:
        for src, dst in ((a, b), (b, a)):
            if last.endswith(src) and len(last) - len(src) >= _FOLD_MIN_STEM:
                out.append(last[: -len(src)] + dst)
    return out


#: Bug #797 / build-2 adversarial F-C (ruling R3-b): a German ATTRIBUTIVE
#: adjective declines by case and gender in front of its noun —
#: "industrieller Mittelstand" / "im industriellen Mittelstand" — and the ending
#: never changes what the phrase states. The fold reaches it for a NON-final
#: token only (the final token keeps the pairs above), and only when the stem
#: before the ending carries an adjective-forming suffix and has at least
#: ``_ADJ_FOLD_MIN_STEM`` letters ("neuer"/"neuen" stays unfolded; "Quellen"
#: is too short to be read as an adjective).
_ADJ_DECLENSION_ENDINGS: tuple[str, ...] = ("en", "em", "er", "es", "e")
_ADJ_STEM_SUFFIXES: tuple[str, ...] = (
    "ell", "isch", "lich", "ig", "al", "iv", "ar", "bar", "haft", "los", "sam",
)
_ADJ_FOLD_MIN_STEM = 6
#: At most this many declinable tokens are folded in one phrase (bounds the variants).
_ADJ_FOLD_MAX_TOKENS = 2


def _adjective_declensions(token: str) -> list[str]:
    """The other declension forms of ``token`` when it is an inflected
    adjective under the closed rule above; ``[]`` otherwise."""
    for ending in _ADJ_DECLENSION_ENDINGS:
        if not token.endswith(ending):
            continue
        stem = token[: -len(ending)]
        if len(stem) >= _ADJ_FOLD_MIN_STEM and stem.isalpha() and stem.endswith(_ADJ_STEM_SUFFIXES):
            return [stem + e for e in _ADJ_DECLENSION_ENDINGS if e != ending]
        return []
    return []


def _with_adjective_declensions(phrase: str) -> list[str]:
    """``phrase`` plus every combination of its non-final tokens' declension
    forms (:func:`_adjective_declensions`), the phrase itself first."""
    tokens = phrase.split(" ")
    slots = [(i, _adjective_declensions(t)) for i, t in enumerate(tokens[:-1])]
    slots = [(i, alts) for i, alts in slots if alts][:_ADJ_FOLD_MAX_TOKENS]
    out = [tokens]
    for i, alts in slots:
        out = out + [t[:i] + [a] + t[i + 1:] for t in out for a in alts]
    return [" ".join(t) for t in out]


def _fold_variants(needle_norm: str) -> list[str]:
    """Deterministic singular/plural variants of a normalised phrase (final token only).

    US212 (#122, ADR-048 amended 2026-07-04): generosity lives in the matching
    layer — "Code reviews" must match a document that says "code review standards".
    Purely morphological, guarded, no LLM. #783 line L35 / #787 line L43: plus
    the closed suffix pairs of ``_FOLD_SUFFIX_PAIRS`` (EN y↔ies, DE plurals and
    noun↔verb-stem forms). Bug #797 / F-C: plus the declension endings of a
    non-final German attributive adjective (:func:`_with_adjective_declensions`).
    """
    variants = [needle_norm]
    head, _, last = needle_norm.rpartition(" ")
    if last.endswith("s") and len(last) - 1 >= _FOLD_MIN_STEM:
        variants.append(needle_norm[:-1])
    elif not last.endswith("s") and len(last) >= _FOLD_MIN_STEM and last[-1].isalpha():
        variants.append(needle_norm + "s")
    prefix = head + " " if head else ""
    for v in _suffix_pair_variants(last):
        candidate = prefix + v
        if candidate not in variants:
            variants.append(candidate)
    if head:
        for v in list(variants):
            for candidate in _with_adjective_declensions(v)[1:]:
                if candidate not in variants:
                    variants.append(candidate)
    return variants


# Friction finding (#234-adjacent): conservative English verb-form fold, added
# to `surface_present`'s TOKEN-LEVEL FALLBACK only — it must never touch
# `_fold_variants` (the phrase-level substring fold `surface_present` tries
# first) nor `skill_tokens`/`skills_near_dupe` (the ADR-046 dedupe instruments,
# deliberately strictness-hardened). Checked longest-suffix-first so "mentoring"
# strips to "mentor" via "-ing", not a shorter/wrong suffix.
#
# "ship" (#244-adjacent, live-reproduced 2026-07-24): a CV skill claim
# "Mentoring" audited unbacked against a vault carrying only "...Mentorship"
# (a same-stem derivational NOUN form, not a plural or a verb tense) — the
# keyword ledger, built by the LLM, happily cited the connection, but neither
# `skills_near_dupe` (single differing tokens, containment needs >= 2 tokens,
# Jaccard 0) nor the pre-existing -ing/-ed/-es/-s fold could see it. Same
# guard rail (min stem length, single-token forms only, ADD-only) as the rest
# of this fallback — never touches skill_tokens/skills_near_dupe either.
_VERB_SUFFIXES = ("ship", "ing", "ed", "es", "s")


def _verb_stem(token: str) -> str:
    """Strip one trailing verb-form/derivational-noun suffix (-ship/-ing/-ed/
    -es/-s), only when the remaining stem keeps >= ``_FOLD_MIN_STEM`` chars —
    same guard rail as the plural fold, so short tokens ('AI', 'SaaS',
    'airship' -> 'air') never fold. Longest suffix checked first (order
    above) so "mentorship" -> "mentor" via "-ship", not a partial/wrong
    match. No suffix applies -> unchanged."""
    for suf in _VERB_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= _FOLD_MIN_STEM:
            return token[: -len(suf)]
    return token


def _verb_form_present(form_norm: str, text_norm: str) -> bool:
    """Token-level verb-form fallback (bounded, conservative, English only):
    True only when ``form_norm`` is a SINGLE token that shares a stem — after
    stripping -ing/-ed/-es/-s, both directions, stem length >= 4 — with some
    single token in ``text_norm``. Multi-token forms are refused outright:
    reordering/POS-shuffling a phrase ("test automation" vs "automated tests")
    is paraphrase-level matching, explicitly out of scope (do not attempt it
    here — see ``surface_present`` docstring).
    """
    form_tokens = form_norm.split()
    if len(form_tokens) != 1:
        return False
    needle_stem = _verb_stem(form_tokens[0])
    return any(_verb_stem(word) == needle_stem for word in text_norm.split())


def surface_present(form: str, text_norm: str) -> bool:
    """THE presence predicate (US212): is this surface form in this normalised text?

    Single shared instrument for the ATS panel, the gap hints (#117), and the
    generation-time coverage check (US213) — consumers may never disagree on
    presence by construction (ADR-048 amended 2026-07-04, #122).

    Falls back to a conservative same-stem ENGLISH VERB-FORM fold (#234-adjacent
    friction finding, extended #244-adjacent) when the direct phrase-level
    substring/plural check misses: "Mentoring" (keyword) is present in text
    that only says "Mentored" (and vice versa) — or, via the "-ship"
    derivational-noun suffix, only says "Mentorship". Bounded to single-token
    forms — this can only ever ADD a match, never remove one, and never
    attempts multi-token paraphrase matching ("performance optimization" vs
    "improving ... performance" stays a true miss, by design).
    """
    n = _norm(form)
    if not n:
        return False
    if any(text_norm.find(v) >= 0 for v in _fold_variants(n)):
        return True
    return _verb_form_present(n, text_norm)


def surface_present_whole_token(form: str, text_norm: str) -> bool:
    r"""Is this surface form in this normalised text AS A WHOLE TOKEN?

    The word-boundary sibling of :func:`surface_present`, added for the ONE
    caller whose question is "does the vault actually name this COMPETENCE"
    rather than "does this string occur anywhere" — the Oracle's skill
    grounding (``oracle/matchers/grounding.ground_skill_claim``). Blind Kaile
    probe, 2026-09-19 (``generated_cvs`` 1b581b82…): the delivered skills
    section carried the bare job-ad nouns "Gruppe", "Vertrieb" and
    "Produktion" as chips and the Oracle graded all three grounded — by bare
    substring, against a vault that says "Diehl-Gruppe",
    "Vertriebsreporting" and "Produktionsstandorte" and never claims any of
    the three as a skill. A blind hiring manager read the result as
    "ATS-Keyword-Stuffing ohne Beleg".

    The same closed, mechanical normalisation as ``surface_present``
    (``_norm``, ``_fold_variants``) — the needle is only required to sit on
    word boundaries in the haystack. Boundaries are ``\w``-based, so a
    token-internal symbol still matches ("SAP CO" inside "SAP CO/FI"), and
    the ``#415`` RECORD SEPARATOR fragment boundary is not a word character
    either, so it keeps separating fragments here exactly as it does there.
    The ``_verb_form_present`` token-level fallback is reused unchanged — it
    was always whole-token by construction.

    Deliberately NOT a change to ``surface_present`` itself: that predicate
    answers the ATS panel's COVERAGE question ("would a keyword scanner see
    this term on the page"), where substring reach is correct and is pinned
    by ADR-048's own amendments. This one answers a grounding question, where
    it is not.
    """
    n = _norm(form)
    if not n:
        return False
    for v in _fold_variants(n):
        if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text_norm):
            return True
    return _verb_form_present(n, text_norm)


# ── #172: near-duplicate skill detection ─────────────────────────────────────
# ONE shared instrument for the reconciler (merge on import, apply.py), the
# render-side CV skill dedup (cv.py), and the ATS "skills-near-dupe" audit — so
# the three layers can never disagree on what counts as the same skill by another
# name (the coverage-vs-heal lesson, #122: the loop that grades is the loop that
# heals). Deterministic, no LLM.

_SKILL_STOPWORDS = frozenset(
    {"and", "or", "the", "of", "for", "with", "a", "an", "to", "in", "&"}
)
# Punctuation stripped only from token EDGES, so "(gxp," → "gxp" and "csv)" → "csv"
# while token-internal symbols survive ("C#", "CI/CD", "C++").
_SKILL_EDGE_PUNCT = "()[]{},;:.\"'`"
_NEAR_DUPE_JACCARD = 0.75


def _skill_stem(token: str) -> str:
    """Guarded singular fold, consistent with ``_fold_variants``: drop a trailing
    "s" only when the stem keeps ≥ ``_FOLD_MIN_STEM`` chars (never "SaaS" → "saa").

    Only *purely-alphabetic* tokens are folded — a token with internal punctuation
    ('node.js', 'ci/cd') is a proper noun / identifier, not an English plural, so
    stripping its trailing 's' would corrupt it ('node.js' → 'node.j')."""
    if token.isalpha() and token.endswith("s") and len(token) - 1 >= _FOLD_MIN_STEM:
        return token[:-1]
    return token


def skill_tokens(name: str) -> frozenset[str]:
    """The normalised content-token set of a skill name (#172).

    ``_norm`` (NFKC, dash→space, casefold, whitespace collapse) then edge-punctuation
    stripping, conjunction/article removal, and a guarded plural fold — so
    formatting and morphological variants ('Code-Review', 'code reviews') land on
    one set. Token-internal symbols (C#, CI/CD, .NET→net) are preserved.
    """
    tokens: set[str] = set()
    for raw in _norm(name).split():
        t = raw.strip(_SKILL_EDGE_PUNCT)
        if not t or t in _SKILL_STOPWORDS:
            continue
        tokens.add(_skill_stem(t))
    return frozenset(tokens)


# #308 (E049/US271, ADR-066/ADR-067) — shared-parenthetical-abbreviation shape.
# 'X (ABBR)' / 'Y (ABBR)' is a translation/synonym pair sharing its canonical
# abbreviation, e.g. the vault's 'MES (Manufacturing Execution System)' and a
# German writer's own 'Fertigungsleitsysteme (MES)'. Extracted from the RAW
# names (before ``_norm``/``_SKILL_EDGE_PUNCT`` fold case and unwrap parens —
# by the time a name reaches ``skill_tokens`` the parenthetical is just more
# bare, lowercased tokens, and the pairing is unrecoverable).
_PAREN_ABBR_RE = re.compile(r"\(([^()]*)\)")
_PAREN_ABBR_MIN_LEN = 2
_PAREN_ABBR_MAX_LEN = 6
_PAREN_ABBR_MIN_UPPER = 2


def _looks_like_abbreviation(segment: str) -> bool:
    """Shape guard for a candidate abbreviation segment (#308).

    A single token (no internal whitespace), 2–6 characters, with at least 2
    uppercase letters in its raw form. This is what separates a true abbreviation
    ('MES', 'GxP', 'CI/CD') from a qualifier ('Advanced': 8 chars, 1 uppercase) or
    a full expansion ('Manufacturing Execution System': not a single token).
    """
    token = segment.strip()
    if not token or any(ch.isspace() for ch in token):
        return False
    if not (_PAREN_ABBR_MIN_LEN <= len(token) <= _PAREN_ABBR_MAX_LEN):
        return False
    return sum(1 for ch in token if ch.isupper()) >= _PAREN_ABBR_MIN_UPPER


def _paren_abbreviation(name: str) -> str | None:
    """The abbreviation-shaped segment of a skill name of the form
    ``ABBR (Expansion)`` or ``Expansion (ABBR)`` (#308), or ``None`` when the
    name carries no parenthetical, or the shape is absent/ambiguous.

    Looks at BOTH the parenthetical content and the text outside it (the
    'head') — the abbreviation may sit on either side ('MES (Manufacturing
    Execution System)' has it in the head; 'Fertigungsleitsysteme (MES)' has
    it inside the parens). Exactly one of the two must pass the shape guard;
    if neither does (no abbreviation present) or both do (ambiguous — no way
    to tell which is canonical), this returns ``None`` rather than guess.
    """
    match = _PAREN_ABBR_RE.search(name)
    if match is None:
        return None
    inner = match.group(1)
    head = name[: match.start()] + name[match.end() :]
    candidates = [seg for seg in (inner, head) if _looks_like_abbreviation(seg)]
    if len(candidates) != 1:
        return None
    return candidates[0].strip()


def _shared_paren_abbreviation(a: str, b: str) -> bool:
    """True when both raw skill names carry a parenthetical abbreviation and the
    two abbreviations match case-insensitively (#308). One-sided parentheticals
    (only one name has any) are deliberately NOT handled here — they stay
    governed by the existing containment rules (:func:`skills_near_dupe`,
    :func:`skills_single_token_containment`). Symmetric by construction.
    """
    abbr_a = _paren_abbreviation(a)
    abbr_b = _paren_abbreviation(b)
    if abbr_a is None or abbr_b is None:
        return False
    return abbr_a.lower() == abbr_b.lower()


def skills_near_dupe(a: str, b: str) -> bool:
    """Are two skill names safe to AUTO-merge as the same skill? (#172, strict)

    True only when EITHER:

    * token-set containment where the *contained* side has ≥ 2 tokens — a modifier
      refinement of a real multi-word skill ('Team Leadership' ⊂ 'Team Leadership
      and Mentorship', 'GxP Compliance' ⊂ 'Regulatory Compliance … (GxP, CSV)'), OR
    * token overlap (Jaccard) reaches ``_NEAR_DUPE_JACCARD``, OR
    * both names carry a parenthetical abbreviation and the abbreviations match
      (#308, E049/US271, ADR-066/067) — a translation/synonym pair sharing its
      canonical abbreviation, e.g. 'MES (Manufacturing Execution System)' and
      'Fertigungsleitsysteme (MES)' (:func:`_shared_paren_abbreviation`). This is
      the ONE shared predicate five call sites rely on (cv.py, profile/reconcile/
      {import_bridge,dedupe,apply}.py, oracle/matchers/grounding.py) — fixed here
      once, never at a call site (ADR-066: doors are adapters).

    **Bare single-token containment is NOT a near-dupe.** One token strictly inside
    a larger set ('React' ⊂ 'React Native', 'Docker' ⊂ 'Docker & Kubernetes') names
    a *distinct* skill, and auto-merging would silently drop it or rename the atom
    into a compound (persisted corruption, UAT 2026-07-15). The reconciler routes
    such pairs to a user confirmation via :func:`skills_single_token_containment`.
    A ONE-SIDED parenthetical (only one name has any, e.g. 'MES' vs
    'Fertigungsleitsysteme (MES)') is untouched by the new disjunct and stays
    governed by this same containment rule.

    Token-level, so 'Java' ≠ 'JavaScript'. Symmetric; empty token sets never match.
    """
    if _shared_paren_abbreviation(a, b):
        return True
    ta, tb = skill_tokens(a), skill_tokens(b)
    if not ta or not tb:
        return False
    # Containment counts only when the contained (smaller/equal) side is itself a
    # multi-token name — never a bare single token inside a larger set.
    if ta <= tb and len(ta) >= 2:
        return True
    if tb <= ta and len(tb) >= 2:
        return True
    union = ta | tb
    return len(ta & tb) / len(union) >= _NEAR_DUPE_JACCARD


def skills_single_token_containment(a: str, b: str) -> bool:
    """Do two skill names relate ONLY by bare single-token containment? (#172)

    True when one token set is a *strict* subset of the other and the contained
    side is a single token — 'React' vs 'React Native', 'Docker' vs 'Docker &
    Kubernetes'. These are deliberately excluded from :func:`skills_near_dupe`
    (never auto-merged); the reconciler surfaces them as a user confirmation.
    Symmetric; empty token sets never match; equal sets are not containment.
    """
    ta, tb = skill_tokens(a), skill_tokens(b)
    if not ta or not tb:
        return False
    if ta < tb and len(ta) == 1:
        return True
    if tb < ta and len(tb) == 1:
        return True
    return False


# #386 (E049 / ADR-067 clause 6) — German compound page-duplicates. skill_tokens
# cannot see that 'Schichtbetrieb' is the head of 'Dreischichtbetrieb' or
# 'Führung' the head of 'Mitarbeiterführung': the compounds are single disjoint
# tokens, so no token-set rule ever relates them. Character-suffix is the only
# deterministic signal, and it is safe ONLY at page scope (a rendered skills
# list), never for vault merging.
_COMPOUND_SUFFIX_MIN_LEN = 6


def _compound_suffix_dupe(ta: frozenset[str], tb: frozenset[str]) -> bool:
    """BOTH sides are bare single-token tags, one token a strict suffix of the
    other — the German-compound shape ('Schichtbetrieb' / 'Dreischichtbetrieb',
    'Führung' / 'Mitarbeiterführung'). Requires the shorter token to be
    ≥ _COMPOUND_SUFFIX_MIN_LEN chars so a generic word ending can never collapse
    two unrelated names.

    ADR-072 clause 2 — **both** sides must be single-token, not just one.
    As originally shipped this required only ``min(len(ta), len(tb)) == 1``, so
    a bare compound was compared against every token of a MULTI-word tag: German
    compounds are head-final, so ``Verpackungsindustrie`` genuinely is a kind of
    ``Industrie`` — but ``Industrie 4.0`` is not the bare head noun, it is a
    different named concept that merely BEGINS with one. Charter run D
    (2026-08-02) dropped ``Verpackungsindustrie`` — the candidate's only
    packaging-domain skill, against a packaging manufacturer's JD — as a page
    duplicate of ``Industrie 4.0``, silently and with no trace. Verified to
    generalise to ``Lebensmittelindustrie``, ``Automobilindustrie`` and
    ``Qualitätsmanagement``/``Management``.

    The extra token IS the discriminator: a head-noun relation holds between two
    bare tags, and a tag carrying further tokens has narrowed itself to
    something else. #386's founding pairs are single-token on both sides and are
    unaffected (pinned in ``tests/unit/test_skills_page_dupe.py``)."""
    if len(ta) != 1 or len(tb) != 1:
        return False
    (t,) = ta
    (u,) = tb
    if u == t:
        return False
    shorter, longer = (t, u) if len(t) <= len(u) else (u, t)
    return len(shorter) >= _COMPOUND_SUFFIX_MIN_LEN and longer.endswith(shorter)


def _page_token_set(name: str) -> frozenset[str]:
    """Page-scope token set: :func:`skill_tokens` plus the parts of any
    slash-compound token ('pp/mm' also yields 'pp' and 'mm', ≥ 2 chars).
    Page-side ONLY — ``skill_tokens`` itself deliberately preserves
    token-internal symbols (CI/CD) for the vault-merge scope."""
    toks: set[str] = set()
    for t in skill_tokens(name):
        toks.add(t)
        if "/" in t:
            toks.update(p for p in t.split("/") if len(p) >= 2)
    return frozenset(toks)


def skills_page_dupe(a: str, b: str) -> bool:
    """Would ``a`` and ``b`` read as a DUPLICATE on the rendered skills list? (#386)

    Strictly wider than :func:`skills_near_dupe`, and scoped to a different job:
    ``skills_near_dupe`` decides what is safe to auto-MERGE in the vault (where
    'React' and 'React Native' may be two real, distinct skills — #172 persisted-
    corruption incident); this predicate decides what a human reader sees as the
    same competence twice on one PAGE, where 'MES' next to
    'MES (Maschinendaten- und Betriebsdatenerfassung)' has no second meaning
    (charter run 10, #386: six such clusters shipped). Union of:

    * :func:`skills_near_dupe` (the vault-merge notion — anything mergeable is
      certainly a page dupe),
    * bare single-token containment (:func:`skills_single_token_containment` —
      'MES' ⊂ 'MES (…)', 'Lean' ⊂ 'Lean Management'),
    * slash-compound containment over :func:`_page_token_set` ('SAP PP' ⊂
      'SAP PP/MM' — charter run 11: the JD's own 'SAP (PP/MM)' phrasing makes
      this pair otherwise invisible to token rules, and both shipped),
    * the German-compound suffix shape ('Schichtbetrieb'/'Dreischichtbetrieb',
      'Führung'/'Mitarbeiterführung' — see :func:`_compound_suffix_dupe`).

    ONE definition, used by every page-side skills pass in services/cv.py
    (ADR-066: fixed here once, never at a call site). NEVER use this for vault
    merging or reconciliation — the reconciler's confirmation flow for
    single-token containment exists precisely because that scope must not
    auto-merge these pairs.
    """
    if skills_near_dupe(a, b):
        return True
    if skills_single_token_containment(a, b):
        return True
    pa, pb = _page_token_set(a), _page_token_set(b)
    if pa and pb and (pa <= pb or pb <= pa):
        return True
    return _compound_suffix_dupe(skill_tokens(a), skill_tokens(b))


# ── ADR-082 (#659, #424): the third member of the near-dupe family — PROSE ────
#
# `skills_near_dupe` decides what is safe to auto-MERGE in the vault (strict,
# because merging is destructive). `skills_page_dupe` decides what a reader sees
# as one competence twice on a page (wider, because the blast radius is smaller).
# Both are calibrated for short NAMES. This one decides whether two delivered
# BULLET SENTENCES state the same achievement, and it is a separate predicate
# rather than a widening of either because the calibration does not transfer:
# measured over the #659 document, `skills_near_dupe` returns False on all 15
# pairs, and the two bullets that issue calls "nearly identical
# sentence-for-sentence" score Jaccard 0.594 against its 0.75 threshold. Sharing
# 3 of 4 tokens is decisive in a skill name; over 30 tokens the non-shared prose
# dominates the union. Re-pointing an instrument at a new population is a
# re-calibration, not a reuse (ADR-082 clause 6).
#
# DETECTION ONLY. ADR-082 clauses 1-3: whether two sentences state the same
# achievement is a JUDGEMENT (ADR-062 clause 1), and no threshold separates the
# classes on real data — the best zero-false-positive cut catches 9 of 15
# labelled pairs. That recall is sufficient to surface a cluster to the candidate
# (one flagged pair is enough) and insufficient to delete a line from a delivered
# CV, which is why nothing may consume this predicate to cut content.

#: Fraction of the SHORTER bullet's content tokens that must also appear in the
#: longer one. Asymmetric on purpose: a short restatement folded into a longer
#: sentence is the shape #659 delivered, and symmetric Jaccard cannot see it.
_PROSE_DUPE_CONTAINMENT = 0.40
#: Length of a contiguous shared token run that reads as a copied clause.
_PROSE_DUPE_MIN_RUN = 3
#: Below this, a bullet is too short for either signal to mean anything — two
#: 4-token bullets naming different standards ("ISO 9001 verantwortet" /
#: "ISO 45001 vorbereitet") share most of their tokens while stating distinct
#: facts. Short bullets fall back to exact equality.
#:
#: **8 → 6 on 2026-09-13 (#659), and the two constants are coupled.** This floor
#: counts tokens AFTER stopword removal, so widening :data:`_PROSE_STOPWORDS` to
#: German function words shortens every German bullet and silently pushed
#: real bullets under the old floor — #424's LucaNet pair (7 content tokens on the
#: shorter side) stopped being detected at all. Swept over the whole labelled
#: population: 6 and 7 both keep every positive, and 6 is the one chosen, because
#: at 7 the measured false positive is suppressed by the FLOOR (its shorter bullet
#: has exactly 6 content tokens) rather than by the stoplist — a right answer for
#: the wrong reason, and a reason that stops working on the next sentence. At 6 the
#: short-bullet negatives still fall back to exact equality with margin
#: ("ISO 9001 verantwortet" = 3 content tokens, "SAP PP für Disposition
#: eingesetzt." = 4).
_PROSE_DUPE_MIN_TOKENS = 6

#: German closed-class function words, stripped from PROSE tokens only (#659,
#: ADR-082 clause 5 amendment 2026-09-13).
#:
#: `_SKILL_STOPWORDS` is eleven ENGLISH words. It was calibrated for the
#: population `skill_tokens` serves — short, mostly English/technical skill NAMES
#: — and on a 2–5-token name a stray German article changes nothing. Over a
#: 30-token German sentence it changes everything, because
#: `_PROSE_DUPE_CONTAINMENT` divides by the SHORTER bullet's token count: a short
#: bullet whose tokens are one-third articles and conjunctions reaches 0.40
#: containment against any other German sentence, on function words alone.
#:
#: Measured on the delivered CV of the 2026-09-11 delivery run
#: (`operations_marcus_de`): `work_history[1].bullets[0]` ("Führung einer Schicht
#: mit 14 Mitarbeitenden …") and `work_history[2].bullets[0]` ("Als Facharbeiter
#: in der Instandhaltung …") — two different roles at two different employers —
#: were flagged at containment 0.444 on the shared token set
#: {`als`, `der`, `tätig`, `und`}. Nothing about those four tokens is an
#: achievement. `duplicate-bullets` FAILED on that delivered document, so this is
#: a false accusation the candidate actually received.
#:
#: **Scope: PROSE only.** `skill_tokens` and therefore `skills_near_dupe` keep the
#: English list unchanged — that predicate is the reconciler's entity-identity
#: primitive at six vault-merge call sites (ADR-082 Context 1), and re-calibrating
#: it here would silently move what the vault auto-merges. Two predicates over one
#: normaliser (ADR-066), two stoplists over two populations.
#:
#: **Closed class only.** Articles, determiners, conjunctions/particles,
#: prepositions, pronouns, auxiliaries and modals — no content word is on this
#: list, so no achievement vocabulary can be silently discarded. The light verbs
#: that also appeared in the measured false positive ("tätig", "erfolgt") were
#: tried and are NOT needed: the closed-class set alone removes the false positive
#: (`Documents/Runs/Nougat/build-3/w/659-calibration.md`).
_PROSE_STOPWORDS = _SKILL_STOPWORDS | frozenset({
    # articles and determiners
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "eines", "einem", "einen",
    "dieser", "diese", "dieses", "diesen", "diesem",
    "jeder", "jede", "jedes", "alle", "allen", "aller",
    "kein", "keine", "keinen", "keiner", "keinem",
    # conjunctions, subjunctions and particles
    "und", "oder", "aber", "sowie", "sowohl", "auch", "denn", "dass", "ob",
    "wenn", "wie", "als", "dabei", "dadurch", "damit", "dazu", "somit",
    "daher", "deshalb", "zudem", "ferner", "bzw",
    "nicht", "noch", "nur", "mehr", "sehr", "schon", "bereits", "dann",
    "hier", "dort", "so",
    # prepositions
    "in", "im", "ins", "an", "am", "ans", "auf", "aus", "bei", "beim",
    "mit", "nach", "von", "vom", "vor", "zu", "zum", "zur",
    "für", "fuer", "über", "ueber", "unter", "durch", "gegen", "ohne",
    "um", "bis", "seit", "während", "waehrend", "wegen", "innerhalb",
    "pro", "je", "per", "gemäß", "gemaess", "laut", "ab",
    "hinter", "neben", "zwischen", "entlang", "samt", "inkl", "inklusive",
    # pronouns
    "sich", "es", "sie", "er", "ihn", "ihm", "ihr", "ihre", "ihrer",
    "ihres", "ihren", "ihrem", "sein", "seine", "seiner", "seines",
    "seinen", "seinem", "man", "wir", "uns", "unsere", "unserer",
    "dessen", "deren", "welche", "welcher", "welches", "was", "wer",
    # auxiliaries, copulas and modals
    "ist", "sind", "war", "waren", "wurde", "wurden", "werden", "wird",
    "worden", "sei", "seien", "hat", "haben", "hatte", "hatten", "habe",
    "kann", "können", "koennen", "konnte", "konnten",
    "soll", "sollen", "muss", "müssen", "muessen", "wollte", "will",
} | {
    # English closed-class function words (adversarial pass, Nougat build 3,
    # area B follow-up on #659). The 2026-09-13 recalibration fixed HALF the
    # population: `bullets_prose_dupe` audits documents in BOTH supported
    # output languages (ADR-068 clause 2a), and `_SKILL_STOPWORDS` — eleven
    # words calibrated for short skill NAMES — never stripped an English
    # pronoun, demonstrative or auxiliary from PROSE either. Measured false
    # positive, mirroring the German one this ADR-082 clause already fixed:
    # "This is the position that was created for the team that needed the
    # most support in the region." vs "...role...designed...group...help...
    # district." — two candidates, two genuinely different sentences — shared
    # exactly {this, is, that, needed, most}, five function words, reaching
    # containment 0.45 against the 0.40 threshold before this addition.
    # Closed class only, same discipline as the German list: pronouns and
    # demonstratives, auxiliary/modal verbs, conjunctions/particles and
    # prepositions — no content word, so no achievement vocabulary is
    # silently discarded.
    "this", "that", "these", "those", "it", "its", "he", "she", "they",
    "them", "their", "we", "you", "who", "which", "what", "whom", "whose",
    "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "will", "would", "can", "could", "should",
    "shall", "may", "might", "do", "does", "did",
    "but", "so", "because", "although", "though", "while", "whereas",
    "also", "further", "furthermore", "moreover", "therefore", "thus",
    "on", "at", "by", "from", "into", "onto", "upon", "over", "under",
    "between", "among", "through", "during", "before", "after", "about",
    "against", "without", "within", "as", "than", "then",
})


@lru_cache(maxsize=2048)
def _prose_tokens(text: str) -> tuple[str, ...]:
    """Ordered content tokens of a bullet, using THE shared normaliser.

    Memoized: :func:`bullets_prose_dupe` is called once per PAIR of delivered
    bullets, so tokenizing inside it made tokenization O(n^2) in the number of
    bullets when O(n) suffices. Measured on a 216-bullet document before the
    cache: 1,993 ms per audit. Returns a tuple so the cached value cannot be
    mutated by a caller.

    Same `_norm` (NFKC, dash->space, casefold, whitespace collapse) and the same
    edge-punctuation treatment as :func:`skill_tokens`, so "Code-Review" and
    "code review" tokenise alike here exactly as they do there — one
    normalisation for the module (ADR-066), two predicates over it.

    The STOPLIST is the one thing that is deliberately not shared: this population
    is German prose sentences, not English-leaning skill names — see
    :data:`_PROSE_STOPWORDS`.
    """
    out: list[str] = []
    for raw in _norm(text).split():
        t = raw.strip(_SKILL_EDGE_PUNCT)
        if t and t not in _PROSE_STOPWORDS:
            out.append(_skill_stem(t))
    return tuple(out)


def _longest_shared_run(a: Sequence[str], b: Sequence[str]) -> int:
    """Longest contiguous run of tokens common to both sequences."""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def bullets_prose_dupe(a: str, b: str) -> bool:
    """Do two delivered bullets state the same achievement? (ADR-082, #659/#424)

    True when EITHER the shorter bullet's content tokens are ``>=
    _PROSE_DUPE_CONTAINMENT`` covered by the longer one, OR the two share a
    contiguous run of ``>= _PROSE_DUPE_MIN_RUN`` tokens — a copied clause.
    Bullets shorter than ``_PROSE_DUPE_MIN_TOKENS`` content tokens compare by
    exact normalised equality only.

    **Measured, not assumed** (2026-09-03, ADR-082 Context 7). Positives: the
    real delivered document behind #659 — 9 of 15 human-labelled redundant pairs
    caught, including every pair the issue names, with the one genuinely distinct
    project bullet never flagged. Negatives: 0 of 139 distinct-bullet pairs drawn
    from all four `tests/files/panel_review_case` CVs.

    **Re-measured 2026-09-13 (#659, build 3) on the population the 2026-09-03 note
    predicted would move it** — two further real delivered documents, from the
    2026-09-10 and 2026-09-11 delivery runs. Both shipped with
    `duplicate-bullets: fail`. Three pairs were flagged across them; two are true
    (a verbatim copied clause, and a bullet restating its neighbour's Lean/KVP/SMED
    content) and **one was a pure false positive driven by German function words**,
    which is what :data:`_PROSE_STOPWORDS` now removes. After that change:
    positives 2/2 and 5/5 on the #659 fixture pairs, false positives 0 — over the
    two delivered documents, the module's nine negative controls, and 147
    distinct-bullet pairs from the four `panel_review_case` CVs.

    Symmetric. This is the ONE implementation for prose redundancy (ADR-066): the
    three exact-match passes of arc42 §5.3.23's matrix converge onto it rather
    than each growing their own. It is a DETECTOR — see the block comment above
    for why no caller may cut content with it.
    """
    ta, tb = _prose_tokens(a), _prose_tokens(b)
    if not ta or not tb:
        return False
    if min(len(ta), len(tb)) < _PROSE_DUPE_MIN_TOKENS:
        return _norm(a) == _norm(b)
    sa, sb = set(ta), set(tb)
    shared = sa & sb
    if len(shared) / min(len(sa), len(sb)) >= _PROSE_DUPE_CONTAINMENT:
        return True
    # A contiguous shared run of length >= 1 requires at least one shared token,
    # so an empty intersection settles it without the quadratic scan. Exact, not
    # a heuristic: this cannot change any verdict, only skip work.
    if not shared:
        return False
    return _longest_shared_run(ta, tb) >= _PROSE_DUPE_MIN_RUN


def _field(obj, name):
    """Read a field from either a `TailoredCVData`-shaped model or its `model_dump`.

    The audit holds the typed model; the review loop's signal holds the same document
    as a plain dict (``_subject_for(draft).model_dump(mode="json")``). One enumeration
    serves both (ADR-066) rather than the signal growing a second copy that drifts.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def delivered_bullets(tailored) -> list[tuple[str, str]]:
    """Every bullet the document DELIVERS, as ``(location, text)``, in render order.

    Within each role's own list, within each project's own list (nested and
    standalone), across the role/nested-project boundary — one flat set. Enumerating
    the whole delivered set, rather than the handful of axes someone thought to name,
    is what makes `duplicate-bullets`' scope equal to its name, and it is what reaches
    #424's shape at all: one project entity rendered BOTH nested under its role and
    standalone puts its bullets in two different containers, which no per-axis scan
    compares.

    The ``location`` string is the finding's only way of being actionable — it is read
    by a human in the ATS report and by the corrector in the review loop.
    """
    out: list[tuple[str, str]] = []

    def _collect(where: str, bullets) -> None:
        for b in bullets or []:
            if isinstance(b, str) and b.strip():
                out.append((where, b))

    for w in _field(tailored, "work_history") or []:
        where = f"{_field(w, 'company') or '?'} / {_field(w, 'role') or '?'}"
        _collect(where, _field(w, "bullets"))
        for proj in (_field(w, "projects") or []):
            _collect(f"{where} > {_field(proj, 'name') or '?'}", _field(proj, "bullets"))
    for proj in (_field(tailored, "projects") or []):
        _collect(f"Projekte > {_field(proj, 'name') or '?'}", _field(proj, "bullets"))
    return out


def delivered_projects(tailored) -> list[tuple[str, str]]:
    """Every project the document RENDERS, as ``(location, name)``, in render order.

    Nested under each work entry, then the standalone top-level section. Same shape and
    the same reason as :func:`delivered_bullets`: one enumeration of what the reader
    actually sees, rather than a per-axis scan that never crosses the two containers.
    """
    out: list[tuple[str, str]] = []
    for w in _field(tailored, "work_history") or []:
        where = f"{_field(w, 'company') or '?'} / {_field(w, 'role') or '?'}"
        for proj in (_field(w, "projects") or []):
            name = (_field(proj, "name") or "").strip()
            if name:
                out.append((where, name))
    for proj in (_field(tailored, "projects") or []):
        name = (_field(proj, "name") or "").strip()
        if name:
            out.append(("Projekte", name))
    return out


def foreign_language_items(tailored, document_language: str) -> list[tuple[str, str]]:
    """Every delivered text item NOT written in the document's own language, as
    ``(location, text)`` (#724).

    THE one enumeration for this question, in the same shape and for the same
    reason as :func:`delivered_bullets`: one list of what the reader actually
    sees, rather than a per-axis scan. Covered surfaces are exactly the ones a
    deterministic pass can put vault text into after the ADR-038 language pass has
    run — the summary, every work and project bullet (nested and standalone), and
    every per-role ``industry_context`` furniture line.

    The predicate is ``utils.language_detection.item_language_mismatch``, which
    wraps THE detector (ADR-066) with a measured evidence floor. It UNDER-reports
    on short items by construction — an English noun phrase carrying no English
    function word reads as German — so this list is a floor on the mixing, never a
    ceiling, and that is the right direction for a check that reports.

    DETECTION ONLY. Nothing here removes or rewrites an item; the repair is the
    #724 language preseed, one pipeline stage earlier.
    """
    from applire.utils.language_detection import item_language_mismatch

    out: list[tuple[str, str]] = []
    summary = _field(tailored, "summary")
    if isinstance(summary, str) and item_language_mismatch(summary, document_language):
        out.append(("Profil", summary))
    for w in _field(tailored, "work_history") or []:
        where = f"{_field(w, 'company') or '?'} / {_field(w, 'role') or '?'}"
        industry = _field(w, "industry_context")
        if isinstance(industry, str) and item_language_mismatch(industry, document_language):
            out.append((f"{where} > Branche", industry))
    for where, text in delivered_bullets(tailored):
        if item_language_mismatch(text, document_language):
            out.append((where, text))
    return out


def duplicate_project_pairs(tailored) -> list[tuple[str, str, str, str]]:
    """Project ENTITIES rendered twice, as ``(location_a, name_a, location_b, name_b)``.

    #424: one real project reached both the nested section and the standalone section,
    "leicht abweichend formuliert" as the blind hiring manager put it, and shipped twice.
    `_nest_projects` deduplicates by normalised name WITHIN each destination bin and
    never ACROSS them — and more narrowly still on the nested side, where the check is
    scoped to one role's own list, so two nested copies under different roles also
    survive. This enumerates the whole rendered set instead.

    The predicate is :func:`skills_near_dupe`, deliberately, and this is the ADR-082
    clause-1 line doing its job rather than being worked around: "is this project the
    same entity as that one" is a question about NAMES, which is a FACT the deterministic
    layer may compute — it is already the reconciler's entity-identity predicate for
    project names (`reconcile/apply.py`) — whereas "do these two bullets state the same
    achievement" is a judgement and gets the prose sibling. Name vs prose, one predicate
    each.

    DETECTION ONLY. ADR-082 clause 3 and ADR-058 clause 4: dropping one of two same-named
    project entries risks deleting bullets only the dropped copy carries, which is the
    `triage:document-harm` outcome #424 is labelled with. The double WRITE is the
    reconciler's ONE CONTAINER rule to prevent; this makes the residue visible on every
    delivered document, at both doors.
    """
    rendered = delivered_projects(tailored)
    pairs: list[tuple[str, str, str, str]] = []
    for i in range(len(rendered)):
        for j in range(i + 1, len(rendered)):
            (wa, a), (wb, b) = rendered[i], rendered[j]
            if _norm(a) == _norm(b) or skills_near_dupe(a, b):
                pairs.append((wa, a, wb, b))
    return pairs


def redundant_bullet_pairs(tailored) -> list[tuple[str, str, str, str]]:
    """Delivered bullet pairs that state the same achievement, as
    ``(location_a, text_a, location_b, text_b)``.

    THE one enumeration for prose redundancy on a delivered CV (ADR-066). Two readers:
    the `duplicate-bullets` ATS check, which reports it to the candidate, and the
    review loop's deterministic redundancy signal, which hands it to the corrector.
    Both are detection — neither may cut content (ADR-082 clauses 1-3, narrowed
    2026-09-05 to the DETERMINISTIC layer: the LLM corrector may repair, a threshold
    may not delete).
    """
    delivered = delivered_bullets(tailored)
    pairs: list[tuple[str, str, str, str]] = []
    for i in range(len(delivered)):
        for j in range(i + 1, len(delivered)):
            (wa, a), (wb, b) = delivered[i], delivered[j]
            if bullets_prose_dupe(a, b):
                pairs.append((wa, a, wb, b))
    return pairs


# ── #391 interim (PO-ruled 2026-08-15, ADR-076 amendment 4 point 6): a
# measurement-only advisory over skills_page_dupe's weakest disjunct ─────────
#
# Ground truth (#391, ADR-076 2026-08-15 amendment): the JD requirement string
# "5 Jahre Controlling-Erfahrung" was attested as vault-tied to the profile
# skill "Controlling" solely via skills_page_dupe's single-token-containment
# disjunct ('controlling' is the ONE token the two share) — attestation too
# loose; the blind HR reviewer flagged the resulting CV/letter inconsistency
# as the top risk signal. ADR-076 clause 4 forbids two things as the fix:
# tightening skills_single_token_containment itself (a second deterministic
# threshold on an equivalence decision is adjudication, not detection — clause
# 4-illegal), and changing what services/cv.py's _vault_tied (and therefore
# _drop_ungrounded_jd_echo_skills) actually decides. What stays legal under
# ADR-062 clause 5 is VISIBILITY: the ATS report — a measurement-only consumer
# — may flag which rendered skills have no stronger vault tie than this one
# weak predicate, without touching the drop/keep decision at all.


def _weak_single_token_tie(skill: str, vault_form: str) -> bool:
    """True when ``skill`` and ``vault_form`` relate ONLY through
    :func:`skills_single_token_containment` — none of :func:`skills_page_dupe`'s
    other three disjuncts also hold for this exact pair.

    Given ``skills_single_token_containment`` is already True (the contained
    side has exactly 1 token, the other side is a *strict* superset of it —
    hence at least 2 tokens), two of the other three disjuncts are structurally
    UNREACHABLE for this pair, not merely unlikely — proved, not assumed, and
    pinned by ``tests/unit/test_ats_audit.py``'s dedicated unreachability
    tests so a future edit to any of these predicates re-proves it:

    * :func:`_compound_suffix_dupe` requires BOTH sides to be a single bare
      token; the non-contained side here has >= 2 tokens by construction.
    * the slash-compound containment over :func:`_page_token_set` requires
      ``pa`` NOT already contained in ``pb`` (or vice versa) at the plain
      ``skill_tokens`` level — but single-token containment already
      establishes exactly that plain-token containment, so the extra
      page-scope check can never independently add a stronger tie.

    Only :func:`skills_near_dupe` can — in principle — still be True: its
    containment and Jaccard branches are also excluded by the same size
    argument, but its shared-parenthetical-abbreviation branch does not
    depend on token-set size at all, so a pathological bare single-token
    name that is ITSELF a parenthetical, e.g. ``'(MES)'`` vs vault's
    ``'MES (Manufacturing Execution System)'``, can share an abbreviation
    while also being single-token-contained. Checked for completeness, not
    because it is expected to matter on real data.
    """
    if not skills_single_token_containment(skill, vault_form):
        return False
    return not skills_near_dupe(skill, vault_form)


def skills_weak_vault_tie(skills: list[str], vault_forms: list[str]) -> list[tuple[str, str]]:
    """Rendered skills whose ONLY tie to ANY vault form is the weak single-
    shared-token predicate (#391, ADR-076 amendment 4 point 6) — measurement
    only, no adjudication, no effect on which skills ship. A skill with ANY
    stronger vault tie (multi-token containment, a shared parenthetical
    abbreviation, Jaccard overlap, an exact match, or the German-compound
    suffix shape) to ANY vault form is never flagged, even when it ALSO
    weak-ties a different vault form — one real tie is enough to earn silence.

    Returns (skill, matched vault form) pairs — the first weak match per
    skill, in ``skills`` order. Pure; never mutates ``skills``/``vault_forms``.
    """
    flagged: list[tuple[str, str]] = []
    for s in skills:
        weak_match: str | None = None
        has_strong_tie = False
        for v in vault_forms:
            if not skills_page_dupe(s, v):
                continue
            if _weak_single_token_tie(s, v):
                if weak_match is None:
                    weak_match = v
            else:
                has_strong_tie = True
                break
        if not has_strong_tie and weak_match is not None:
            flagged.append((s, weak_match))
    return flagged


def _entry_norms(entry: dict[str, Any]) -> set[str]:
    forms = entry.get("surface_forms") or [entry.get("concept", "")]
    return {_norm(f) for f in forms} | {_norm(entry.get("concept", ""))}


def _keyword_search_forms(keyword: str, ledger: list[dict[str, Any]] | None) -> list[str]:
    """The search set of :func:`keyword_present`: {keyword literal} ∪ owning entry
    surface_forms ∪ concept, in that order, de-duplicated on ``_norm``.

    Ownership honours the F4 gap stance: if any NON-claimable entry owns the keyword,
    only non-claimable owners widen the search — a foreign claimable entry's forms must
    never make an honest-gap keyword read as covered (ADR-048 §8 / #122).
    """
    k_norm = _norm(keyword)
    entries = ledger or []
    gap_owners = [e for e in entries if not e.get("claimable") and k_norm in _entry_norms(e)]
    owners = gap_owners or [e for e in entries if e.get("claimable") and k_norm in _entry_norms(e)]
    forms: list[str] = [keyword]
    for e in owners:
        forms.extend(e.get("surface_forms") or [])
        if e.get("concept"):
            forms.append(e["concept"])
    out: list[str] = []
    seen: set[str] = set()
    for f in forms:
        if not isinstance(f, str):
            continue
        n = _norm(f)
        if n and n not in seen:
            seen.add(n)
            out.append(f)
    return out


def keyword_matches(
    keyword: str, text_norm: str, ledger: list[dict[str, Any]] | None = None
) -> list[KeywordMatch]:
    """ADR-090 clause 2 — every form of :func:`keyword_present`'s search set that
    passes :func:`surface_present` on ``text_norm``, with ``stem=True`` when the hit
    came ONLY through the token-stem fallback (``_verb_form_present``).

    THE decision point: :func:`keyword_present` is ``bool(keyword_matches(...))``,
    so the recorded forms are, by construction, exactly the forms that decided
    presence — never a second, drifting search.
    """
    out: list[KeywordMatch] = []
    for f in _keyword_search_forms(keyword, ledger):
        n = _norm(f)
        if any(text_norm.find(v) >= 0 for v in _fold_variants(n)):
            out.append(KeywordMatch(form=f, stem=False))
        elif _verb_form_present(n, text_norm):
            out.append(KeywordMatch(form=f, stem=True))
    return out


def keyword_present(keyword: str, text_norm: str, ledger: list[dict[str, Any]] | None = None) -> bool:
    """Presence per keyword = any of {keyword literal} ∪ owning entry surface_forms ∪ concept
    passes :func:`surface_present` (see :func:`_keyword_search_forms` for ownership)."""
    return bool(keyword_matches(keyword, text_norm, ledger))


def _grounded_through_matched_forms(matches: list[KeywordMatch], vault_index: Any) -> bool:
    """ADR-090 clause 4 — a flagged keyword is grounded when EVERY form that made it
    present on the document is backed in the vault by the Oracle's own skill
    instrument (``ground_skill_claim``, whole-token / near-dupe, vault-only: no
    ledger sibling arm, or the ledger row that flagged the keyword could back
    itself). No matches, or no index → not grounded (the finding stays open)."""
    if not matches or vault_index is None:
        return False
    from applire.services.oracle.matchers.grounding import ground_skill_claim

    return all(ground_skill_claim(m.form, vault_index) is not None for m in matches)


def _years(date_str: str | None) -> list[str]:
    return re.findall(r"\d{4}", date_str or "")


def _check(checks: list[ATSCheck], cid: str, ok: bool, details: str | None = None) -> None:
    checks.append(ATSCheck(id=cid, status="pass" if ok else "fail", details=None if ok else details))


def _document_language_check(
    checks: list[ATSCheck], foreign: list[tuple[str, str]], document_language: str
) -> None:
    """The ADR-039 ``document-language`` row from a list of ``(where, text)`` items
    not written in ``document_language`` — ONE row shape for both documents
    (ADR-066): the CV (#724) and, since ADR-094 clause L, the v2 letter.

    #759 (ADR-081 amended 2026-10-08): the review surface shows a FAILED
    document-language check in its "before you send it" block, in the reader's UI
    language — so the row carries a machine-readable variant. ``details`` stays
    the EN diagnostic (the agent door and legacy readers)."""
    details = (
        f"items not written in the document language ({document_language}): "
        + "; ".join(f"[{where}] '{text[:60]}'" for where, text in foreign[:5])
        + (f" (+{len(foreign) - 5} more)" if len(foreign) > 5 else "")
    )
    if not foreign:
        _check(checks, "document-language", True)
        return
    checks.append(ATSCheck(
        id="document-language", status="fail", details=details,
        details_key="document-language-mixed",
        details_params={
            "language": document_language,
            "count": len(foreign),
            "examples": "; ".join(
                text if len(text) <= 60 else f"{text[:60].rstrip()}…"
                for _where, text in foreign[:3]
            ),
        },
    ))


def _page_band_not_applicable(checks: list[ATSCheck]) -> None:
    """Emit the page-length band as an explicit ``not_applicable`` (ADR-079 cl. 4).

    A document kind without intrinsic pagination — a ``.docx``, which has no
    pages until a word processor lays it out — cannot be measured against the
    ADR-051 length norms. The band is therefore neither a pass nor a fail, and
    it must NOT simply be omitted: an absent check is invisible to ``passed``
    and ``failed`` alike, so the report would read as a clean, complete audit
    of something never evaluated. That is the #634 failure shape, and clause 4
    exists to forbid it ("never silently omitted").

    Kept here, beside the two real bands, deliberately. E057 task 1.1 first
    froze ``_audit_cv_text``/``_audit_letter_text`` to keep the audit one
    implementation (ADR-066), then discovered the freeze and clause 4 were not
    simultaneously satisfiable. The founder amended the freeze rather than let
    a second module start constructing ``page-length`` rows (PO 2026-09-01):
    three construction sites for one check id, hand-synced, is precisely what
    ADR-066 forbids — and any future ``page_count``-less caller would have
    reproduced the silent-absence bug with nothing to stop it.
    """
    checks.append(ATSCheck(
        id="page-length", status="not_applicable",
        details="not applicable — this document has no fixed pagination until "
                "a word processor lays it out",
        details_key="page-length-not-applicable",
        # Empty but PRESENT, deliberately. ATSChecksPanel localises only when
        # `details_params` is truthy, because next-intl renders the raw key
        # path instead of throwing on a missing ICU variable — so a keyed check
        # whose params are absent (a partially-migrated persisted report) must
        # take the EN fallback. This message has no placeholders, so `{}` says
        # "params measured, and there are none" rather than "params missing".
        details_params={},
    ))


#: A trailing gender marker on a German/English job title — "(m/w/d)", "(m/f/d)",
#: "(w/m/d)", "(all genders)" is left alone (it never appears inside a claim form).
_GENDER_MARKER_RE = re.compile(
    r"\s*\((?:(?:[mwfdx]|div)(?:\s*/\s*(?:[mwfdx]|div))+|all genders|alle geschlechter|gn\*?)\)\s*$"
)
#: R-4 (main-session default 2026-09-26, adversarial finding 1): a title variant is
#: masked only when it has at least this many tokens — a one-word title
#: ("Engineer") is also the candidate's own wording for a role they claim.
_TITLE_MIN_TOKENS = 2
#: R-4 (adversarial finding 2): generic first words of company names that never
#: open an employer clause on their own ("Deutsche" is also the language). The
#: full name and the name without legal form still anchor.
_GENERIC_FIRST_WORDS = frozenset({
    "deutsche", "deutscher", "neue", "neuer", "erste", "allgemeine", "vereinigte",
    "europäische", "internationale", "nationale", "bayerische", "berliner",
    "international", "global", "united", "general", "national", "european",
    "first", "new", "american", "british", "royal", "bank", "group", "gruppe",
    "stadt", "stadtwerke", "institut", "university", "universität", "hochschule",
})
#: A legal-form suffix on an employer name, matched on the `_norm`ed name.
_LEGAL_FORM_RE = re.compile(
    r"\s+(?:gmbh\s*&\s*co\.?\s*kg(?:aa)?|gmbh|ggmbh|mbh|ag|se|kg|kgaa|ohg|gbr|ug(?:\s*\(haftungsbeschränkt\))?"
    r"|e\.\s?v\.|ltd\.?|limited|inc\.?|llc|plc|corp\.?|s\.a\.|b\.v\.|n\.v\.)$"
)


@dataclass(frozen=True)
class NonClaimNames:
    """The posting's names the unsupported-claim decision must not read as claims."""

    titles: tuple[str, ...] = ()
    employers: tuple[str, ...] = ()
    #: R-2 / R-3: mask the employer-business clause too. Set by the LETTER audit
    #: only — a CV bullet carries no first-person word, so a clause opened by an
    #: employer name there would run through the candidate's own bullet.
    employer_clause: bool = False
    #: adv #2 (main-session ruling 2026-09-26): the posting's keyword / ledger
    #: forms, normalised. An employer variant that IS one of them (or a whole word
    #: inside one) is a skill, not only a name — "MongoDB Inc." must not hide
    #: "sharded MongoDB clusters", "Software AG" must not mask "software".
    skill_forms: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.titles or self.employers)

    def all_names(self) -> tuple[str, ...]:
        return self.titles + self.employers

    def plus_employers(self, *names: str | None) -> "NonClaimNames":
        """The same names plus further employer names (e.g. the letter's own
        ``recipient.company``), normalised the same way."""
        extra = non_claim_names(None, names).employers
        return dataclasses.replace(
            self, employers=self.employers + tuple(e for e in extra if e not in self.employers),
        )

    def with_employer_clause(self) -> "NonClaimNames":
        """The same names, with the employer-business clause masked too (letters)."""
        return dataclasses.replace(self, employer_clause=True)

    def excluding_skills(self, forms: Sequence[str]) -> "NonClaimNames":
        """adv #2: drop every employer variant that is a skill form (or a whole
        word inside one); the full name with its legal form normally survives, so
        the recipient block's "MongoDB Inc." is still masked. Titles are
        untouched (a skill-bearing title is Strawberry scope, adv #6)."""
        skills = tuple(dict.fromkeys(n for n in (_norm(f) for f in forms if isinstance(f, str)) if n))
        return dataclasses.replace(
            self,
            employers=tuple(e for e in self.employers if not _is_skill_word(e, skills)),
            skill_forms=skills,
        )


def _is_skill_word(word: str, skill_forms: Sequence[str]) -> bool:
    """``word`` equals a skill form or stands inside one as whole words."""
    if not word:
        return False
    pat = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)")
    return any(f == word or pat.search(f) for f in skill_forms)


def non_claim_names(role_title: str | None, employer_names: Sequence[str | None] = ()) -> NonClaimNames:
    """ADR-090 amended 2026-09-26 (WP-R) — the posting's own names, normalised.

    ``titles``: the target job title as analysed and without its trailing gender
    marker. ``employers``: each employer name as given and without its legal form.
    Both are FACTS about the posting (ADR-062 cl. 1): what is masked is exactly
    the literal string, never a judgement about what a sentence means."""
    titles: list[str] = []
    employers: list[str] = []

    def _add(out: list[str], s: str) -> None:
        if s and s not in out:
            out.append(s)

    t = _norm(role_title or "")
    if t:
        for variant in (t, _GENDER_MARKER_RE.sub("", t).strip()):
            if len(variant.split()) >= _TITLE_MIN_TOKENS:
                _add(titles, variant)
    for e in employer_names or ():
        n = _norm(e or "")
        if not n:
            continue
        _add(employers, n)
        _add(employers, _LEGAL_FORM_RE.sub("", n).strip())
    return NonClaimNames(titles=tuple(titles), employers=tuple(employers))


def _mask_intervals(text_norm: str, intervals: list[tuple[int, int]]) -> str:
    if not intervals:
        return text_norm
    merged: list[list[int]] = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out: list[str] = []
    pos = 0
    for a, b in merged:
        out.append(text_norm[pos:a])
        out.append(f" {_CORPUS_FRAGMENT_BOUNDARY} ")
        pos = b
    out.append(text_norm[pos:])
    return "".join(out)


def _name_pattern(name: str) -> str:
    """A whole-word pattern for a normalised name that tolerates pypdf's spurious
    kerning space at every character boundary — the same tolerance as
    :func:`_find` (#399), so a title extracted as "of fice manager" is masked."""
    return r"(?<!\w)" + r" *".join(re.escape(ch) for ch in name) + r"(?!\w)"


def _name_intervals(text_norm: str, names: Sequence[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for n in sorted({x for x in names if x}, key=len, reverse=True):
        for m in re.finditer(_name_pattern(n), text_norm):
            spans.append((m.start(), m.end()))
    return spans


def non_claim_names_for_job(job: Any, application: Any | None = None) -> NonClaimNames | None:
    """The four audit seams' one entry point: the analysed posting's title and
    employer (``JobAnalysis.role_title`` / ``company_name``). ``None`` without a job,
    which keeps the full-text decision.

    ADR-092 cl. 5(f) (Strawberry): the posting is shared and immutable; what THIS
    user calls the role and the employer lives on their ``applications`` row
    (``effective_posting_labels``). Before Strawberry an agent's override was
    written onto the posting itself, so the audit masked the override; with
    ``application`` given, the effective labels are masked as well as the
    posting's own — both are names of the posting, never a claim."""
    if job is None:
        return None
    posting = non_claim_names(getattr(job, "role_title", None), [getattr(job, "company_name", None)])
    if application is None:
        return posting
    from applire.services.posting_labels import effective_posting_labels

    role_title, company_name = effective_posting_labels(job, application)
    effective = non_claim_names(role_title, [company_name])

    def _union(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(a + b))

    return NonClaimNames(
        titles=_union(effective.titles, posting.titles),
        employers=_union(effective.employers, posting.employers),
    )


#: R-2 (founder, 2026-09-26): the words that end an employer clause — the first
#: first-person word (the candidate speaking again), a sentence end, a salutation
#: (the PDF text has no sentence break between the recipient block and "Sehr
#: geehrte …," — without this stop the opening claim was masked on 4 of 16
#: captured letters), or a masked fragment boundary.
_CLAUSE_END_BASE = (
    r"\b(?:i|me|my|mine|myself|ich|mich|mir|mein|meine|meinen|meinem|meiner|meines)\b"
    # adv #4 (main-session ruling): a comma also ends the clause — "for NovaPay,
    # seven years of Kubernetes …" is the candidate speaking. Dashes cannot be a
    # stop: `_norm` folds every dash to a space before this runs.
    r"|[.!?;:](?=\s|$)|,"
    rf"|{_CORPUS_FRAGMENT_BOUNDARY}"
)


@lru_cache(maxsize=1)
def _clause_end_re() -> "re.Pattern[str]":
    """The clause stop, with the salutations taken from the letter producer's own
    list (``cover_letter._SALUTATION_OPENERS``, adv #5) — one list, reconciled with
    its producer, so every salutation the pipeline writes or keeps stops a clause.
    An opener the producer spells with a trailing space ("dear ", "hi ", "sg ")
    must end on a word boundary; the others match as prefixes ("liebe" → "lieber")."""
    from applire.services.cover_letter import _SALUTATION_OPENERS

    alts: list[str] = []
    for opener in _SALUTATION_OPENERS:
        n = _norm(opener)
        if not n:
            continue
        alts.append(r"(?<!\w)" + re.escape(n) + (r"(?!\w)" if opener.endswith(" ") else ""))
    return re.compile(_CLAUSE_END_BASE + "|" + "|".join(alts))
#: The shortest first word of an employer name that anchors a clause on its own.
_CLAUSE_ANCHOR_MIN_FIRST_WORD = 4


def _employer_clause_anchors(employers: Sequence[str], skill_forms: Sequence[str] = ()) -> list[str]:
    """R-2: the employer names as given and without legal form (already in
    ``employers``) plus each name's first word when it has ≥ 4 letters and is
    not a generic company-name word (R-4, :data:`_GENERIC_FIRST_WORDS`)."""
    out: list[str] = []
    for e in employers:
        for a in (e, (e.split() or [""])[0]):
            if not a or a in out:
                continue
            if _is_skill_word(a, skill_forms):
                continue  # adv #2: a skill word never anchors a clause
            if a == e or (len(a) >= _CLAUSE_ANCHOR_MIN_FIRST_WORD and a not in _GENERIC_FIRST_WORDS):
                out.append(a)
    return out


def _employer_clause_intervals(
    text_norm: str, employers: Sequence[str], skill_forms: Sequence[str] = (),
) -> list[tuple[int, int]]:
    """R-2 (founder ruling, option A): the clause that describes the employer's
    business — from a whole-word mention of the employer's name to the first
    first-person word, sentence end or salutation. Anchored on the name ONLY:
    "Sie/Ihr/your" was measured to mask the candidate's own intent sentences.
    Measured on 16 captured synthetic letters: 0 of ~27 clauses masked a
    candidate claim; 7 of 17 placeable group-1 rows were such clauses."""
    anchors = _employer_clause_anchors(employers, skill_forms)
    if not anchors:
        return []
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(a) for a in sorted(anchors, key=len, reverse=True)) + r")(?!\w)"
    )
    spans: list[tuple[int, int]] = []
    for m in pattern.finditer(text_norm):
        end = _employer_clause_end(text_norm, m.end())
        if end > m.start():
            spans.append((m.start(), end))
    return spans


#: #803 (R2-7): an ITEM of an enumeration — one to four words, no punctuation.
_LIST_ITEM = r"[^\s,.;:!?␞]+(?:\s+[^\s,.;:!?␞]+){0,3}"
#: #803: what follows a LIST comma — up to three more items, then the coordinator
#: ("checkout, settlement, and payout rails", "Konsumgüter, Lebensmittel und …").
_LIST_CONTINUATION_RE = re.compile(
    rf"\s*(?:(?:{_LIST_ITEM})\s*,\s*){{0,3}}(?:{_LIST_ITEM}\s*,?\s*)?"
    r"(?:and|und|or|oder|sowie|as well as)\b"
)
#: #803: a possessive right after the employer's name ("NovaPay’s", "NovaPay's").
_POSSESSIVE_RE = re.compile(r"[’']?s\b")
#: #803: the coordinator straight after a comma (", and payout rails").
_COORDINATOR_NEXT_RE = re.compile(r"\s*(?:and|und|or|oder|sowie|as well as)\b")


def _is_list_comma(text_norm: str, anchor_end: int, comma_at: int, *, in_list: bool = False) -> bool:
    """#803 (Bug, R2-7; ADR-090 amended 2026-10-24): is the comma at ``comma_at`` a
    LIST comma inside the employer clause, so the clause runs on?

    adv #4's comma stop stays the default — "for NovaPay, seven years of Kubernetes
    …" is the candidate speaking. But the stop also cut the employer's own
    enumeration of its business ("NovaPay’s work on checkout, settlement, and payout
    rails for European e-commerce merchants …"), which left "settlement", "payout
    rails" and "e-commerce" in group 1 with an add-to-profile exit: 8 of the 10
    employer-sentence rows still flagged on the captured letters (measured,
    WP-T build 4). A comma continues the clause only when ALL hold:

    1. the employer clause already said something before it — at least one word
       between the name (a possessive "’s" does not count) and the comma; a comma
       straight after the name is adv #4's case and still stops;
    2. what follows is an enumeration that reaches its coordinator (and / und / or /
       oder / sowie) within up to four short items;
    3. a comma followed DIRECTLY by the coordinator (", and …") continues only an
       enumeration this clause is already in — "NovaPay’s team, and would be pleased
       to …" is a new clause, "checkout, settlement, and payout rails" is not.
    """
    between = text_norm[anchor_end:comma_at].strip()
    if _POSSESSIVE_RE.match(between):
        between = _POSSESSIVE_RE.sub("", between, count=1).strip()
    if not between:
        return False
    if _COORDINATOR_NEXT_RE.match(text_norm, comma_at + 1):
        return in_list
    return bool(_LIST_CONTINUATION_RE.match(text_norm, comma_at + 1))


def _employer_clause_end(text_norm: str, anchor_end: int) -> int:
    """Where the employer clause opened at ``anchor_end`` stops (R-2, adv #4, #803)."""
    pos = anchor_end
    in_list = False
    while True:
        end_m = _clause_end_re().search(text_norm, pos)
        if end_m is None:
            return len(text_norm)
        if end_m.group(0) == "," and _is_list_comma(
            text_norm, anchor_end, end_m.start(), in_list=in_list
        ):
            in_list = True
            pos = end_m.end()
            continue
        return end_m.start()


def mask_non_claim_spans(text_norm: str, names: NonClaimNames | None) -> str:
    """``text_norm`` with the posting's own words replaced by the corpus fragment
    boundary (``␞``, which no surface form can bridge — see
    :data:`_CORPUS_FRAGMENT_BOUNDARY`): every occurrence of the target job title and
    of the employer's name (:func:`non_claim_names`, ruling R-1), and — when
    ``names.employer_clause`` is set, i.e. on a cover letter (R-3) — the clause
    that describes the employer's business (:func:`_employer_clause_intervals`,
    ruling R-2). ``None``/empty → unchanged."""
    if not names:
        return text_norm
    intervals = _name_intervals(text_norm, names.all_names())
    if names.employer_clause:
        intervals += _employer_clause_intervals(text_norm, names.employers, names.skill_forms)
    return _mask_intervals(text_norm, intervals)


def _keyword_coverage(
    text_norm: str,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    vault_text_norm: str | None = None,
    vault_index: Any = None,
    non_claim: NonClaimNames | None = None,
    *,
    document_language: str | None = None,
) -> ATSKeywordCoverage:
    seen: set[str] = set()
    unique: list[str] = []
    for k in keywords:
        if k and k.lower() not in seen:
            seen.add(k.lower())
            unique.append(k)
    # US212 (#122): presence via the shared predicate — surface-form union over the
    # keyword's owning ledger entry plus the morphological fold, not the literal alone.
    # ADR-090 clause 2: the forms that decide presence are recorded at the decision.
    matches_by_kw = {k: keyword_matches(k, text_norm, ledger) for k in unique}
    present = [k for k in unique if matches_by_kw[k]]
    missing = [k for k in unique if k not in set(present)]

    # US203 (ADR-048): split missing into "claimable" (the candidate supports it per the
    # ledger — a surfacing miss) vs "honest gap" (not in the profile). No ledger → all
    # missing are honest gaps (back-compat; never silently claimable). The audit stays
    # deterministic and local — no LLM, no synthetic score.
    from applire.services.keyword_ledger import (
        claimable_surface_forms,
        keyword_liabilities,
        unsupported_claim_surface_forms,
    )

    claimable_concepts = claimable_surface_forms(ledger)
    claimable_norm = {_norm(f) for f in claimable_concepts}
    missing_claimable = [k for k in missing if _norm(k) in claimable_norm]
    missing_honest_gap = [k for k in missing if _norm(k) not in claimable_norm]

    # ADR-048 amended 2026-07-03 (#117), fourth quadrant: a PRESENT keyword the ledger
    # marks unsupported (honest gap) is a truthfulness warning — it reached the document
    # without profile evidence (e.g. typed in via the section editor). Claimable always
    # wins on alias collisions; without a ledger we cannot judge, so nothing is flagged.
    # ADR-048/059 am. 2026-07-27: scoped to UNKNOWN gaps — a `denied` concept named
    # in an honest negation is not an unsupported claim, and this predicate cannot
    # see negation. The direction-aware check lives with the Oracle.
    unclaimable_norm = {_norm(f) for f in unsupported_claim_surface_forms(ledger)}
    # #249 run-4 (2026-07-24): ONE shared presence predicate over BOTH
    # surfaces — a keyword that clears `surface_present` against the vault's
    # OWN literal text (the same instrument the Truthfulness Oracle's
    # ground_skill_claim checks, services/oracle/matchers/grounding.py) can
    # never be flagged present_unsupported, whatever the Keyword Ledger's own
    # classification says. Defense-in-depth over narrowing the denial match
    # at ledger-build time (services/keyword_ledger.py): even a stale or
    # otherwise mis-classified ledger row can no longer put the ATS panel and
    # the Oracle at odds about the same skill string. `vault_text_norm` is
    # optional and back-compat (None for every existing caller) — omitting it
    # reproduces today's behaviour exactly.
    literally_grounded = (
        {k for k in present if surface_present(k, vault_text_norm)}
        if vault_text_norm
        else set()
    )
    # ADR-090 amended 2026-09-26 (WP-R, ruling R-1): the unsupported-claim
    # decision reads only the CANDIDATE's text. The audited text also carries the
    # posting's own words — the target job title, the employer's name (and, R-2,
    # the employer-business clause) — which are on the page (so `present` and
    # every coverage figure above keep the full text) but are no claim. A keyword
    # whose every occurrence is the posting's is therefore not unsupported, and a
    # flagged keyword's recorded forms are the ones the candidate's text carries.
    # `non_claim=None` (every caller that passes nothing) reproduces the
    # full-text behaviour exactly.
    if non_claim:
        # adv #2: an employer variant that is one of the posting's own keyword /
        # ledger forms is a skill the candidate may claim — never masked. (A
        # vault-only skill that is no JD keyword cannot be in present_unsupported,
        # so keywords + ledger forms are the complete set this decision needs.)
        skill_forms = list(keywords)
        for e in ledger or []:
            skill_forms.extend(e.get("surface_forms") or [])
            if e.get("concept"):
                skill_forms.append(e["concept"])
        non_claim = non_claim.excluding_skills(skill_forms)
        claim_text_norm = mask_non_claim_spans(text_norm, non_claim)
        claim_matches = {k: keyword_matches(k, claim_text_norm, ledger) for k in present}
    else:
        claim_matches = matches_by_kw
    present_unsupported = [
        k for k in present
        if _norm(k) in unclaimable_norm
        and _norm(k) not in claimable_norm
        and k not in literally_grounded
        and claim_matches[k]
    ]
    # ADR-090 clause 4: the re-audit must see new evidence written in the
    # CANDIDATE's wording. The literal-only rule above never grounds "IT Data & AI
    # Governance" against a vault that now says "AI governance", although that is
    # exactly the wording the document carries. So a flagged keyword is also
    # grounded when every matched form is backed by the Oracle's skill-grounding
    # instrument over the (claimable-only) vault index. `vault_index=None` (every
    # caller that does not pass it) reproduces the literal-only behaviour exactly.
    if vault_index is not None:
        present_unsupported = [
            k for k in present_unsupported
            if not _grounded_through_matched_forms(claim_matches[k], vault_index)
        ]
    # Build-2 delivery F2 (SF-REVIEW.18): the recorded denials that cover a
    # flagged term, read off the vault index every audit seam already builds
    # (`grounding_vault_index`) — the ledger floor's own predicate, so the review
    # never offers "add it to my profile" for what the candidate disclaimed.
    present_unsupported_denials: dict[str, list[str]] = {}
    denied_concepts = list(getattr(vault_index, "denied_concepts", ()) or ())
    if denied_concepts and present_unsupported:
        from applire.services.profile.reconcile.stance import denials_covering

        release = getattr(vault_index, "denial_release_text", "") or ""
        for k in present_unsupported:
            forms = [m.get("form", "") if isinstance(m, dict) else getattr(m, "form", "")
                     for m in claim_matches.get(k) or []]
            covering = denials_covering(k, [f for f in forms if f], denied_concepts, release)
            if covering:
                present_unsupported_denials[k] = covering
    # #790 line L10 (founder ruling D2-1 = A, ADR-068 amended 2026-10-20): every
    # test above is literal, so on a pair whose vault may state the evidence in
    # ANOTHER language ("Stripe payouts" on an EN CV, "Stripe-Auszahlungen …" in
    # a DE-first vault) "nothing in your profile backs this" is an
    # absence-based accusation the instrument cannot make. Such a term leaves
    # the group-1 population for a neutral "not checked across languages" row
    # (``present_other_language``, rendered by ``_other_language_check``). The
    # pair fact is the Oracle's own (``holds_other_language``, ADR-066: one
    # predicate for both halves). No vault index or no document language →
    # unchanged (fail open to today's behaviour).
    # Build-2 adversarial F-D (founder ruling R3-a, ADR-068 amended): the
    # other-language move below is about a term the vault may state in other
    # WORDS. A term one of the candidate's own recorded denials covers is not
    # that case — the candidate said they do not have it — so it never leaves
    # the red population (the same cover the review card reads above).
    present_other_language: list[str] = []
    if vault_index is not None and present_unsupported:
        from applire.services.oracle.matchers.vault import holds_other_language

        if holds_other_language(vault_index, document_language):
            present_other_language = [
                k for k in present_unsupported if k not in present_unsupported_denials
            ]
            present_unsupported = [
                k for k in present_unsupported if k in present_unsupported_denials
            ]
    # F-8: a PRESENT keyword whose owning ledger row the candidate DENIED. The
    # ownership rule is `keyword_present`'s own (`_entry_norms`, ADR-048 §8/#122), so
    # the two can never disagree about which row a keyword belongs to; the fact read
    # off that row is its `status`, which the ledger already carries
    # (`keyword_ledger._VALID_STATUS`) and already reports separately in the
    # DO-NOT-CLAIM block. Read-only on the ledger — no ledger semantics change here.
    # #803 belt (founder ruling T-2 = B, 2026-10-24, ADR-090 amended): on a
    # LETTER, a still-flagged term whose every occurrence sits in a sentence that
    # names the employer is most likely about the employer ("… spricht mich an,
    # weil Sie … fertigen"), not a claim — the clause mask cannot reach every
    # such sentence shape. It stays a group-1 row (take-out and edit stay) but
    # neutral: no add-to-profile exit and not counted in the headline.
    present_unsupported_employer_sentence = (
        _employer_sentence_terms(text_norm, present_unsupported, non_claim, ledger)
        if non_claim and non_claim.employer_clause
        else []
    )
    present_denied = [
        k for k in present
        if any(
            e.get("status") == "denied" and _norm(k) in _entry_norms(e)
            for e in (ledger or [])
        )
    ]
    # E048/US266 (#249 option b): the FULL claimable list, independent of
    # presence — the same list already computed above for the missing_claimable
    # split, exposed on the report itself.
    # #260: the pre-generation liability slice, exposed on the report too (agent
    # + report-surface parity) — orthogonal to claimable_concepts (literal vault
    # presence) and independent of document presence, same as claimable_concepts.
    keyword_liability_concepts = [e.get("concept", "") for e in keyword_liabilities(ledger)]
    return ATSKeywordCoverage(
        present=present,
        missing=missing,
        missing_claimable=missing_claimable,
        missing_honest_gap=missing_honest_gap,
        present_unsupported=present_unsupported,
        present_denied=present_denied,
        claimable_concepts=claimable_concepts,
        keyword_liability_concepts=keyword_liability_concepts,
        present_unsupported_matches={k: claim_matches[k] for k in present_unsupported},
        present_other_language=present_other_language,
        present_denied_matches={k: matches_by_kw[k] for k in present_denied},
        present_unsupported_denials=present_unsupported_denials,
        present_unsupported_employer_sentence=present_unsupported_employer_sentence,
    )


#: Sentence ends on the normalised letter text (``_norm`` keeps punctuation).
_LETTER_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+|\s*" + re.escape(_CORPUS_FRAGMENT_BOUNDARY) + r"\s*")


def _employer_sentence_terms(
    text_norm: str,
    terms: list[str],
    non_claim: NonClaimNames,
    ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """#803 belt — the flagged ``terms`` that occur ONLY in sentences naming the
    employer. A sentence names the employer when :func:`_name_intervals` finds one
    of ``non_claim.employers`` in it. A term is in that population when its
    claim-text matches (the same masked text the flag read) find nothing in the
    other sentences."""
    if not terms or not non_claim.employers:
        return []
    sentences = [x for x in _LETTER_SENTENCE_END_RE.split(text_norm) if x and x.strip()]
    other = [x for x in sentences if not _name_intervals(x, non_claim.employers)]
    if len(other) == len(sentences):
        return []  # no sentence names the employer
    rest = (" " + _CORPUS_FRAGMENT_BOUNDARY + " ").join(mask_non_claim_spans(x, non_claim) for x in other)
    return [k for k in terms if not keyword_matches(k, rest, ledger)]


#: 786:47 (founder 2026-10-22, Hazelnut build 4) — the employer-name repetition
#: tell, as a deterministic letter finding (ADR-082: repetition is DETECTED by the
#: audit, never repaired by a pass). The 2026-08-19 ship-gate letter opened eight
#: body sentences with "Bei der Schwarzwald Präzision GmbH" and both blind panel
#: reviewers called it machine-written; the #565 credit bar is "≤ 1 sentence-initial
#: employer mention per paragraph". A mention is sentence-initial when at most
#: this many words precede it ("Bei der …", "At …", "Dort bei …").
EMPLOYER_REPETITION_CHECK_ID = "employer-repetition"
_EMPLOYER_OPENING_LEAD_WORDS = 3
_EMPLOYER_OPENINGS_PER_PARAGRAPH = 1


def _is_salutation(paragraph: str) -> bool:
    p = paragraph.strip()
    return p.endswith(",") and len(p.split()) <= 8


def employer_openings(
    paragraphs: Sequence[str], employer_names: Sequence[str | None]
) -> list[tuple[int, str, int]]:
    """786:47 — ``(paragraph number, employer as given, openings)`` for every body
    paragraph in which one employer opens more sentences than the #565 bar allows.

    Paragraph numbers count body paragraphs only (a leading salutation line is not
    one). One count per sentence per employer; every form of a name (with and
    without its legal form, :func:`non_claim_names`) counts as that employer."""
    employers: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for e in employer_names or ():
        forms = non_claim_names(None, [e]).employers
        if not forms or forms[0] in seen:
            continue
        seen.add(forms[0])
        employers.append(((e or "").strip(), forms))
    if not employers:
        return []
    out: list[tuple[int, str, int]] = []
    body = [p for p in paragraphs if isinstance(p, str) and _norm(p)]
    if body and _is_salutation(body[0]):
        body = body[1:]
    for number, paragraph in enumerate(body, start=1):
        sentences = [x for x in _LETTER_SENTENCE_END_RE.split(_norm(paragraph)) if x and x.strip()]
        for display, forms in employers:
            openings = 0
            for sentence in sentences:
                spans = _name_intervals(sentence, forms)
                if spans and len(sentence[: min(a for a, _ in spans)].split()) <= _EMPLOYER_OPENING_LEAD_WORDS:
                    openings += 1
            if openings > _EMPLOYER_OPENINGS_PER_PARAGRAPH:
                out.append((number, display, openings))
    return out


def _employer_repetition_check(
    checks: list[ATSCheck], paragraphs: Sequence[str], employer_names: Sequence[str | None]
) -> None:
    """786:47 — an ADVISORY row (``pass`` with details, like ``skills-weak-vault-tie``):
    the letter reads, nothing is wrong with a fact; the person decides. Emitted only
    when it fires. ``details_params`` stay locale-neutral (the name, two numbers)."""
    hits = employer_openings(paragraphs, employer_names)
    if not hits:
        return
    number, employer, count = max(hits, key=lambda h: h[2])
    checks.append(ATSCheck(
        id=EMPLOYER_REPETITION_CHECK_ID,
        status="pass",
        details="; ".join(
            f"'{e}' opens {c} sentences in paragraph {n}" for n, e, c in hits
        ) + " — a name repeated at sentence starts reads as machine-written; a back-reference reads naturally",
        details_key=EMPLOYER_REPETITION_CHECK_ID,
        details_params={"employer": employer, "paragraph": number, "count": count, "more": len(hits) - 1},
    ))


def _other_language_check(checks: list[ATSCheck], coverage: ATSKeywordCoverage) -> None:
    """#790 line L10 (D2-1 = A) — the neutral row for ``present_other_language``.

    The copy claims only what the instrument knows (ruling R3-a): the check
    compares wording and the vault holds text in another language, so
    these terms were not checked — never that the profile states them.

    ``not_applicable``: the keyword check did not run across languages for these
    terms, so it neither passes nor fails them (ADR-079 cl. 4 bucket) and the
    review surface shows them under "Weitere Hinweise", outside group 1's count.
    Emitted only when there is something to say."""
    terms = coverage.present_other_language
    if not terms:
        return
    checks.append(ATSCheck(
        id="keyword-other-language",
        status="not_applicable",
        details=(
            "not checked across languages — your profile contains text in another "
            "language and this check compares wording only; check these terms "
            "yourself: "
            + ", ".join(terms[:8]) + (f" (+{len(terms) - 8} more)" if len(terms) > 8 else "")
        ),
        details_key="keyword-other-language",
        details_params={"count": len(terms), "terms": ", ".join(terms[:8])},
    ))


def _finish(document: Literal["cv", "cover_letter"], checks: list[ATSCheck], coverage: ATSKeywordCoverage) -> ATSReport:
    # E057/ADR-079 clause 4: not_applicable is its own bucket, counted the same
    # way passed/failed are (a sum over status equality) — never derived as
    # "whatever's left over", so a status this function doesn't yet know about
    # would undercount everywhere rather than silently inflating one bucket.
    from applire.services.finding_registry import stamp_ats_model

    # ADR-099 cl. 2 (WP-B): every check, terminal-review item and keyword bucket
    # carries its delivery class + resolver from the one registry.
    return stamp_ats_model(ATSReport(
        document=document,
        checks=checks,
        keywords=coverage,
        passed=sum(1 for c in checks if c.status == "pass"),
        failed=sum(1 for c in checks if c.status == "fail"),
        not_applicable=sum(1 for c in checks if c.status == "not_applicable"),
    ))


def _free_text_snippets(tailored: TailoredCVData) -> list[str]:
    """Every piece of the CV the candidate actually wrote, in document order.

    ADR-039 amendment (2026-08-31, #634). The summary, each work-entry bullet,
    each bullet of a project nested under an entry, and each bullet of a
    standalone project. Deliberately NOT the structured fields — those already
    have their own named checks, and duplicating them here would report one
    defect twice.

    Blank entries are dropped rather than checked: there is nothing to verify,
    and an empty needle matches anywhere. Mirrors the letter side's
    empty-paragraph guard.

    Templates render every bullet exactly once — the #622 page-break policy
    slices them into ``[:2]`` / ``[2:-2]`` / ``[-2:]`` groups but reconstitutes
    the full list (verified at n = 1, 2, 3, 4, 5, 7 on all seven templates), and
    nested project bullets are never sliced — so a bullet listed here is one the
    delivered document is expected to carry.
    """
    out: list[str] = []

    def _add(value: str | None) -> None:
        if value and value.strip():
            out.append(value)

    _add(tailored.summary)
    for w in tailored.work_history:
        for b in w.bullets or []:
            _add(b)
        for proj in w.projects or []:
            for b in proj.bullets or []:
                _add(b)
    for proj in tailored.projects or []:
        for b in proj.bullets or []:
            _add(b)
    return out


def _name_ladder_cuts(checks: list, ladder_cuts: list) -> None:
    """#786 L9/L18 (founder design): the page-length finding names every cut the
    page ladder made (``services.cv_page_ladder``) — machine-readable in
    ``details_params`` and spelled out in the EN ``details``."""
    row = next((c for c in reversed(checks) if c.id == "page-length"), None)
    if row is None:
        return
    spelled = "; ".join(f"{c['kind']}: {', '.join(c['items'])}" for c in ladder_cuts)
    suffix = f"removed to fit the page limit — {spelled}"
    row.details = f"{row.details}; {suffix}" if row.details else suffix
    # details_params values are int | str (schemas.ats.ATSCheck): the cut list
    # travels as one spelled string, the kinds as a comma-joined id list.
    row.details_params = {
        **(row.details_params or {}),
        "ladder_cuts": spelled,
        "ladder_cut_kinds": ",".join(c["kind"] for c in ladder_cuts),
    }


#: 786:237 — the page-length outcomes a "keep" choice decides (over the target).
_PAGE_LENGTH_OVER_TARGET_KEYS = frozenset({
    "page-length-target-missed", "page-length-exhausted", "page-length-exceeds", "page-length-senior",
})


def kept_page_length_check(check: ATSCheck, pages_kept_at: int | None) -> ATSCheck:
    """786:237 — ``check`` (a CV ``page-length`` row) as DECIDED when a "keep"
    covered its length, else unchanged. One predicate for the audit and for the
    keep-without-new-data door, which updates the stored report without a render."""
    params = check.details_params or {}
    pages, target = params.get("pages"), params.get("target", params.get("standard"))
    if (
        pages_kept_at is None
        or check.id != "page-length"
        or check.details_key not in _PAGE_LENGTH_OVER_TARGET_KEYS
        or not isinstance(pages, int)
        or pages > pages_kept_at
    ):
        return check
    return ATSCheck(
        id="page-length", status="pass",
        details=f"{pages} pages — you chose to keep them (target {target})",
        details_key="page-length-kept",
        details_params={"pages": pages, "target": target},
    )


def decide_kept_page_length(report: dict | None, pages_kept_at: int | None) -> dict | None:
    """786:237 — the stored CV report with its page-length row decided (see
    :func:`kept_page_length_check`); ``None``/unchanged when nothing applies."""
    if not report or pages_kept_at is None:
        return report
    from applire.services.finding_registry import annotate_report

    out = dict(report)
    out["checks"] = [
        kept_page_length_check(ATSCheck.model_validate(c), pages_kept_at).model_dump()
        if isinstance(c, dict) and c.get("id") == "page-length" else c
        for c in report.get("checks") or []
    ]
    annotate_report(out)  # ADR-099: the decided row is re-classified (kept → S)
    return out


def _audit_cv_text(
    text: str,
    tailored: TailoredCVData,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    page_count: int | None = None,
    page_band_not_applicable: bool = False,
    target: int | None = None,
    region: str = DEFAULT_REGION,
    condensation_exhausted: bool = False,
    ladder_cuts: list | None = None,
    vault_text_norm: str | None = None,
    vault_skill_forms: list[str] | None = None,
    pins: list | None = None,
    terminal_review=None,
    previous_report: dict | None = None,
    document_language: str | None = None,
    vault_index: Any = None,
    non_claim: NonClaimNames | None = None,
    pages_kept_at: int | None = None,
) -> ATSReport:
    t = _norm(text)
    checks: list[ATSCheck] = []

    c = tailored.contact
    if c.name:
        _check(checks, "contact-name", _find(c.name, t) >= 0, f"name '{c.name}' not found in extracted text")
    if c.email:
        _check(checks, "contact-email", _find(c.email, t) >= 0, f"email '{c.email}' not found")
    if c.phone:
        digits = re.sub(r"\D", "", c.phone)
        _check(checks, "contact-phone", digits in re.sub(r"\D", "", text), f"phone '{c.phone}' not found")

    entry_positions: list[int] = []
    for i, w in enumerate(tailored.work_history):
        company_norm = _norm(w.company)
        role_norm = _norm(w.role)
        # Skip the check entirely when BOTH fields are empty
        if not company_norm and not role_norm:
            continue
        pos_company = _find(w.company, t) if company_norm else None
        pos_role = _find(w.role, t) if role_norm else None
        years_ok = all(y in text for y in _years(w.start_date))
        # Each non-empty field must be present; empty fields are not required
        company_ok = pos_company is None or pos_company >= 0
        role_ok = pos_role is None or pos_role >= 0
        ok = company_ok and role_ok and years_ok
        _check(checks, f"work-{i}", ok,
               f"entry '{w.role} @ {w.company}' incomplete in extracted text "
               f"(company={'ok' if company_ok else 'missing'}, role={'ok' if role_ok else 'missing'}, "
               f"year={'ok' if years_ok else 'missing'})")
        # Use whichever position is available for reading-order tracking
        anchor = pos_company if pos_company is not None else (pos_role if pos_role is not None else -1)
        entry_positions.append(anchor)

    if len(entry_positions) > 1 and all(p >= 0 for p in entry_positions):
        ordered = all(a <= b for a, b in zip(entry_positions, entry_positions[1:]))
        _check(checks, "reading-order", ordered,
               "work-history entries appear in a different order in the extracted text "
               "than in the CV data (each entry anchored at its first occurrence of "
               "company/role text)")

    for i, e in enumerate(tailored.education):
        institution_norm = _norm(e.institution)
        degree_norm = _norm(e.degree)
        # Skip entirely if both fields are empty
        if not institution_norm and not degree_norm:
            continue
        institution_ok = not institution_norm or _find(e.institution, t) >= 0
        degree_ok = not degree_norm or _find(e.degree, t) >= 0
        ok = institution_ok and degree_ok
        _check(checks, f"education-{i}", ok, f"education entry '{e.degree} {e.institution}' not fully found")

    if tailored.skills:
        missing_skills = [s for s in tailored.skills if _find(s, t) < 0]
        _check(checks, "skills", not missing_skills,
               "skills missing from extracted text: " + ", ".join(missing_skills))

        # #172: near-duplicate skill tags in the rendered CV (belt-and-braces over
        # the render-side dedup — the SAME shared predicate).
        near_pairs = [
            (a, b)
            for i, a in enumerate(tailored.skills)
            for b in tailored.skills[i + 1:]
            if skills_near_dupe(a, b)
        ]
        _check(checks, "skills-near-dupe", not near_pairs,
               "near-duplicate skills: " + "; ".join(f"'{a}' ~ '{b}'" for a, b in near_pairs))

        # #391 interim (ADR-076 amendment 4 point 6): measurement-only advisory,
        # never a failure — silent unless at least one rendered skill's ONLY
        # vault tie is the weak single-shared-token predicate. Silent by
        # construction when `vault_skill_forms` is not given (back-compat with
        # every existing text-only caller/test).
        weak_ties = skills_weak_vault_tie(tailored.skills, vault_skill_forms or [])
        if weak_ties:
            # EN diagnostic (details) may carry English scaffold words — it is the
            # EN fallback string, never localised (same contract as skills-near-dupe's
            # `details`). details_params must stay locale-neutral (like the
            # page-length checks' `pages`/`region`/`standard`): a bare (skill,
            # matched-vault-form) pair, no English words, so the de/en templated
            # sentences (ats.checkDetails.skills-weak-vault-tie) supply the ONLY
            # prose — the params never leak English into the German chip.
            pairs_en = "; ".join(f"'{s}' (shares only '{v}')" for s, v in weak_ties)
            pairs_neutral = "; ".join(f"'{s}' ('{v}')" for s, v in weak_ties)
            checks.append(ATSCheck(
                id="skills-weak-vault-tie", status="pass",
                details=f"skill(s) tied to your profile by a single shared word "
                        f"only — worth a second look before sending: {pairs_en}",
                details_key="skills-weak-vault-tie",
                details_params={"skills": pairs_neutral, "count": len(weak_ties)},
            ))

    # ── ADR-039 amendment (2026-08-31, #634): the candidate's own prose ──────
    # Everything above verifies STRUCTURED fields. Free text was verified by
    # nothing on this side, so a bullet that lost a phrase between the data and
    # the delivered PDF produced a clean report — #634 shipped
    # "Koordination mit <Projekt Phoenix> und R&D-Teams" as
    # "Koordination mit und R&D-Teams", still grammatical, zero failures.
    #
    # Same `_norm`/`_find` predicate as the structured checks — one predicate,
    # not a second matcher (ADR-066); `_audit_letter_text` converges onto it
    # below. Presence only, never quality: no score (ADR-035).
    for i, snippet in enumerate(_free_text_snippets(tailored)):
        _check(checks, f"content-{i}", _find(snippet, t) >= 0,
               f"text not found in the extracted document: '{snippet[:80]}'")

    # ── ADR-082 clause 5 (2026-09-03, #659 / #424) ──────────────────────────
    # Was #169: a role bullet repeated inside a project nested under that role,
    # by exact `_norm` equality. That is narrower than this check's own NAME in
    # two independent ways, and a delivered CV shipped six near-duplicate bullets
    # under one project while this reported `pass`:
    #   * SCOPE — it never compared a list against ITSELF, and never reached the
    #     standalone projects section.
    #   * PREDICATE — exact match, so no near-duplicate was visible to it.
    # Now every bullet the document delivers is compared: within each role's own
    # list, within each project's own list (nested and standalone), and across the
    # role/nested-project boundary — on `bullets_prose_dupe`, the prose member of
    # the near-dupe family (NOT `skills_near_dupe`, which is calibrated for names
    # and fires on none of these pairs — ADR-082 clause 6).
    #
    # DETECTION ONLY (ADR-076 clause 3, ADR-082 clauses 1-3): this reports, it
    # never edits `tailored`. Prose redundancy is a judgement under ADR-062
    # clause 1, and the measured predicate is deliberately recall-limited — enough
    # to surface a cluster, not enough to license deleting an achievement.
    # Every bullet the document delivers, in one flat list with its location, then
    # compared pairwise. Enumerating the whole delivered set — rather than the
    # handful of axes someone thought to name — is what makes the check's scope
    # equal to its name. It is also what reaches #424's shape: one project entity
    # rendered BOTH nested under its role and standalone puts its bullets in two
    # different containers, which no per-axis scan compares.
    delivered = delivered_bullets(tailored)
    dupe_pairs = redundant_bullet_pairs(tailored)

    # Emitted whenever a comparison was actually possible — a document with fewer
    # than two bullets has nothing to say here, and a check that reports `pass`
    # on an empty comparison is the kind of green this ADR exists to stop.
    if len(delivered) >= 2:
        _check(checks, "duplicate-bullets", not dupe_pairs,
               "bullets that state the same achievement twice: "
               + "; ".join(
                   f"[{wa}] '{a[:60]}' ~ [{wb}] '{b[:60]}'"
                   for wa, a, wb, b in dupe_pairs[:5]
               )
               + (f" (+{len(dupe_pairs) - 5} more)" if len(dupe_pairs) > 5 else ""))

    # ── #424 (ADR-082 clause 1, 2026-09-13) ─────────────────────────────────
    # The bullet-level check above cannot name the SHAPE when the two copies are
    # paraphrases: it reports two redundant sentences, not "this project is on the
    # page twice". The entity question is about NAMES and is therefore a fact the
    # deterministic layer may compute — `skills_near_dupe` is already the
    # reconciler's identity predicate for project names. Detection only: the double
    # WRITE is prevented vault-side by the reconciler's ONE CONTAINER rule, and
    # dropping one of two same-named entries risks deleting bullets only the dropped
    # copy carries (ADR-082 clause 3, the `triage:document-harm` #424 carries).
    project_pairs = duplicate_project_pairs(tailored)
    if len(delivered_projects(tailored)) >= 2:
        _check(checks, "duplicate-project", not project_pairs,
               "the same project is rendered twice: "
               + "; ".join(
                   f"[{wa}] '{a[:50]}' ~ [{wb}] '{b[:50]}'"
                   for wa, a, wb, b in project_pairs[:5]
               )
               + (f" (+{len(project_pairs) - 5} more)" if len(project_pairs) > 5 else ""))

    # ── #724 (ADR-039; ADR-072/ADR-062/ADR-067 amended 2026-09-18) ──────────
    # The delivered document is written in ONE language. On the founder's edge
    # UAT a German CV shipped 4 English work bullets and eight English "Branche:"
    # lines, and both blind reviewers read the document as unedited. The
    # `cv_language` REVIEWER is not this check: it exhausted its two rounds on
    # that very document (`REVIEW_EXHAUSTED chain=cv_language issues=25`) and the
    # CV shipped anyway — a reviewer's exhaustion is not a delivered signal.
    #
    # `not_applicable` rather than absent when the caller states no language
    # (ADR-079 clause 4, the #634 shape): an omitted check is invisible to
    # `passed` and `failed` alike and would read as a clean audit of something
    # never evaluated.
    if document_language:
        _document_language_check(
            checks, foreign_language_items(tailored, document_language), document_language
        )
    else:
        checks.append(ATSCheck(
            id="document-language", status="not_applicable",
            details="not applicable — this caller did not state the document's language",
            details_key="document-language-not-applicable",
            details_params={},
        ))

    # E042/US238 (ADR-051 §5 + amendment §3): target-aware page-length band, replacing
    # the #171a fixed 2/3 thresholds. ATSCheck has no "warn" status, so anything up to
    # the region max passes (carrying an advisory detail when it deviates from the
    # region standard); only over the max fails. Skipped when no count is given
    # (text-only callers/tests). All norm numbers come from REGION_NORMS — never
    # hard-code a page number (ADR-051 §1). Keep id "page-length" (frontend i18n keys
    # on it); details carry a details_key + details_params pair so the frontend can
    # localise them (ADR-038), with the EN `details` string as the fallback.
    #
    # #238 (founder-acceptance F4) amendment: the "within max" branch used to pass
    # unconditionally once page_count <= maximum, regardless of WHY it was over
    # tgt — so an EXPLICIT target the candidate chose (production always resolves
    # and passes one; only text-only callers omit it) that the condense loop
    # could not hit still shipped as unprompted "senior profile" advice, hiding
    # the miss. `target is not None` distinguishes "a target was actually asked
    # for" from the back-compat "no target given, defaults to the regional
    # standard" callers — only the latter keeps the old blanket senior-advisory
    # wording; the former is now an honest fail, whether or not
    # `condensation_exhausted` was set (the section-editor re-audit path can miss
    # the target without ever running the condense loop at all — same honesty
    # applies defensively).
    if page_band_not_applicable:
        # ADR-079 cl. 4 — takes precedence over any page_count a caller also
        # passed: "this artefact has no pages" is a fact about the document
        # kind, and a count measured on some other rendering of it cannot
        # overrule that.
        _page_band_not_applicable(checks)
    elif page_count is not None:
        norm = REGION_NORMS[region]
        standard = norm.cv_standard_pages
        maximum = norm.cv_max_pages
        tgt = target if target is not None else standard
        if page_count <= standard:
            # Within the regional norm — always a plain pass, whatever the target.
            # Guards both directions: a higher chosen target that wasn't needed
            # (no deviation, no noise) AND a sub-standard target (MCP-reachable)
            # that must not trigger the senior wording on a norm-conforming doc.
            checks.append(ATSCheck(id="page-length", status="pass", details=None))
        elif page_count <= tgt:
            # The chosen target was actually USED to go beyond the norm — advise.
            checks.append(ATSCheck(
                id="page-length", status="pass",
                details=f"{page_count} pages — meets your chosen target of {tgt}; "
                        f"the {region} norm is {standard} pages",
                details_key="page-length-target",
                details_params={"pages": page_count, "target": tgt,
                                "region": region, "standard": standard},
            ))
        elif page_count <= maximum and target is None:
            # No explicit target was asked for (back-compat: the region standard
            # was used as a default, not a candidate choice) — the old advisory
            # wording is still honest here, nothing was promised and missed.
            checks.append(ATSCheck(
                id="page-length", status="pass",
                details=f"{page_count} pages — acceptable for senior profiles; "
                        f"the {region} norm is {standard} pages",
                details_key="page-length-senior",
                details_params={"pages": page_count, "region": region, "standard": standard},
            ))
        elif page_count <= maximum:
            # #238: an explicit target was chosen and missed, even though the
            # document still sits within the regional max — must fail honestly,
            # never dressed up as senior-profile advice (founder-acceptance F4).
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — couldn't condense to your {tgt}-page "
                        f"target without cutting relevant content; the {region} max "
                        f"is {maximum} pages",
                details_key="page-length-target-missed",
                details_params={"pages": page_count, "target": tgt, "region": region,
                                "standard": standard, "max": maximum},
            ))
        elif condensation_exhausted:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — condensed to the maximum; length driven by "
                        f"education/skills volume; exceeds the {region} norm of {standard} "
                        f"pages (max {maximum})",
                details_key="page-length-exhausted",
                details_params={"pages": page_count, "region": region,
                                "standard": standard, "max": maximum},
            ))
        else:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — exceeds the {region} norm of {standard} "
                        f"pages (max {maximum})",
                details_key="page-length-exceeds",
                details_params={"pages": page_count, "region": region,
                                "standard": standard, "max": maximum},
            ))
        # 786:237 (Hazelnut build 4): the person chose "keep" on the x+1 card
        # (``page_overflow.choice``, L-3) — the over-target length is DECIDED.
        # ``pages_kept_at`` is the length that choice covered (``full_pages``);
        # a CV that grew past it after the choice is a new length, not decided.
        if checks and checks[-1].id == "page-length":
            checks[-1] = kept_page_length_check(checks[-1], pages_kept_at)
        if ladder_cuts:
            _name_ladder_cuts(checks, ladder_cuts)

    # ── E056/ADR-077 clauses 3+5: pin presence + the structured driver ──────
    # Measured HERE because `tailored` is the override-applied content every
    # `_update_ats_report` door hands in (SF-PIN.5: the single seam covers the
    # generation door, the section-editor re-audit door and the agent door).
    pin_entries = None
    if pins:
        from applire.schemas.ats import PinnedFactReportEntry
        from applire.services.pin_reach import pin_ledger_conflicts, pin_present_in_cv

        pin_entries = []
        for pin in pins:
            if "cv" not in pin.targets:
                continue
            present = (not pin.stale) and pin_present_in_cv(pin, tailored)
            pin_entries.append(PinnedFactReportEntry(
                pin_id=pin.pin_id,
                entry_type=pin.entry_type,
                quote=pin.quote,
                present=present,
                stale=pin.stale,
                # #580: the ledger-conflict FACT rides the same seam on every door.
                ledger_conflict=pin_ledger_conflicts(pin.quote, ledger),
            ))
        present_count = sum(1 for e in pin_entries if e.present)
        page_check = next((c for c in checks if c.id == "page-length"), None)
        if page_check is not None and page_check.status == "fail" and present_count:
            # The bands themselves are UNCHANGED (incl. #238); the driver is
            # additive machine-readable context: N pinned facts occupy space
            # the condense loop was forbidden to reclaim.
            page_check.driver = {"pinned_facts": present_count}

    # ── #563 (D) + #542 — the send seat's two new answers (ADR-039 amended
    # 2026-09-04). Both are appended LAST so their position in the list is stable
    # for a reader scanning the report, and both are ALWAYS present: an absent
    # check is invisible to every counter and reads as a clean, complete audit of
    # something that was never examined (the #634 class, ADR-079 clause 4).
    checks.append(_terminal_review_check(terminal_review, previous_report))
    checks.append(_narrative_evidence_check(tailored, ledger))

    coverage = _keyword_coverage(
        t, keywords, ledger, vault_text_norm, vault_index, non_claim,
        document_language=document_language,
    )
    _other_language_check(checks, coverage)
    report = _finish("cv", checks, coverage)
    if pin_entries is not None:
        report.pinned_facts = pin_entries
    return report


def _terminal_review_check(terminal_review, previous_report: dict | None) -> ATSCheck:
    """#563 (D): the ADR-076 clause-3 terminal review's own outcome, reported.

    ADR-076 clause 2 makes every SIGNAL name an exhaustion disposition and the
    terminal review declares *ship-and-report*; the ship half was built and the
    report half was not. Local import: ``terminal_review_outcome`` reads
    ``review_issues``, and this module is imported by ``keyword_ledger`` which that
    chain reaches — a module-level import here would close the cycle.
    """
    from applire.services.terminal_review_outcome import (
        TERMINAL_REVIEW_CHECK_ID,
        build_terminal_review_check,
        previous_check,
    )

    return build_terminal_review_check(
        terminal_review,
        previous=previous_check(previous_report, TERMINAL_REVIEW_CHECK_ID),
        document="cv",
    )


#: Stable machine id for ADR-076 clause 5's send-seat report (#542).
NARRATIVE_EVIDENCE_CHECK_ID = "narrative-evidence"

#: How many concepts the diagnostic NAMES before it starts counting. Six is the point
#: at which a reader stops reading a list and starts reading a wall; the full count
#: always survives in `driver.concepts`.
_NARRATIVE_EVIDENCE_DETAIL_MAX = 6


def _narrative_evidence_check(tailored: TailoredCVData, ledger: list[dict[str, Any]] | None) -> ATSCheck:
    """#542 / ADR-076 clause 5: is the evidence behind the CV's own claims on the page?

    ``fail`` when a claimable ledger concept at the JD's own requirement rank reaches
    the delivered document only as a bare skills tag or a summary word — or not at all.
    ``not_applicable`` without a ledger: a legacy row cannot be judged, and saying
    "pass" there would be the #634 class in the other direction.

    Deliberately NOT folded into ``ATSKeywordCoverage.missing_claimable``: that field's
    population is WHOLE-DOCUMENT presence, it is already rendered (E058 group 2), and
    widening it in place would silently move a number its readers have been reading.
    """
    from applire.services.cv_gap_hints import verified_narrative_underclaim

    if not ledger:
        return ATSCheck(
            id=NARRATIVE_EVIDENCE_CHECK_ID,
            status="not_applicable",
            details=(
                "No Keyword Ledger is available for this document, so whether its claims "
                "are evidenced in the work history could not be judged."
            ),
        )
    # #672 line 40/45 (2026-09-13): the SAME document, also as the structured-section
    # corpus. `verified_narrative_underclaim` has exempted a concept the COMPOSED
    # document already delivers in a vault-joined section since #666 — LANGUAGES,
    # CERTIFICATIONS, EDUCATION — and this check, its only other caller, never passed
    # the argument, so the corrector-side SIGNAL and the candidate-facing CHECK
    # disagreed about the same question (ADR-066). Delivery-tier evidence: the
    # 2026-09-11 delivery run's `narrative-evidence` read `fail` naming "Sehr gutes
    # Deutsch" and "Gutes Englisch" on a CV whose LANGUAGES section states both — the
    # candidate was told to put a language into a work bullet that the document already
    # carries in its own headed block. `tailored` IS the composed document here (the
    # audit runs after assembly on both doors), so this is the same dict, read twice
    # through the two corpora the signal defines.
    composed = tailored.model_dump(mode="json")
    missing = verified_narrative_underclaim(composed, ledger, structured_document=composed)
    if not missing:
        return ATSCheck(id=NARRATIVE_EVIDENCE_CHECK_ID, status="pass", details=None)
    # Bounded by RANK, not by a character cap: `verified_narrative_underclaim` returns
    # `fit_weight`-descending, so the first entries are the ones most central to this
    # role. A raw character truncation would cut the list at an arbitrary concept and
    # give no indication that it had. The remainder is counted, never silently dropped
    # — and `driver.concepts` always carries the full count.
    shown, rest = missing[:_NARRATIVE_EVIDENCE_DETAIL_MAX], missing[_NARRATIVE_EVIDENCE_DETAIL_MAX:]
    named = ", ".join(
        f"{c.concept} (claimed but not evidenced)" if c.tag_only else f"{c.concept} (absent)"
        for c in shown
    )
    more = f" …and {len(rest)} more." if rest else ""
    details = (
        "Required capabilities the candidate genuinely supports are not shown in any "
        f"work-entry or project bullet: {named}.{more} A skills-list entry alone reads to a "
        "hiring reviewer as an unmet requirement."
    )
    # R2-5 (build 4): the same statement as data — the review card says it in the
    # UI language (``ats.checkDetails.narrative-evidence-*``). Concept names are
    # the ledger's own labels, locale-neutral data.
    tag_only = [c.concept for c in shown if c.tag_only]
    absent = [c.concept for c in shown if not c.tag_only]
    return ATSCheck(
        id=NARRATIVE_EVIDENCE_CHECK_ID,
        status="fail",
        details=details if len(details) <= 1200 else details[:1199].rstrip() + "…",
        details_key="narrative-evidence-underclaim",
        details_params={
            "tagOnly": ", ".join(tag_only),
            "tagOnlyCount": len(tag_only),
            "absent": ", ".join(absent),
            "absentCount": len(absent),
            "more": len(rest),
        },
        driver={"concepts": len(missing)},
    )


def audit_cv(
    pdf_bytes: bytes,
    tailored: TailoredCVData,
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    target: int | None = None,
    region: str = DEFAULT_REGION,
    condensation_exhausted: bool = False,
    vault_text_norm: str | None = None,
    vault_skill_forms: list[str] | None = None,
    pins: list | None = None,
    document_language: str | None = None,
    vault_index: Any = None,
    non_claim: NonClaimNames | None = None,
) -> ATSReport:
    """Audit a rendered CV PDF against the structured CV data and a list of keywords.

    NOTE (E042/US238): production no longer calls this — the CV pipeline's condense
    loop needs the page count itself, so ``services/cv._update_ats_report`` extracts
    once via :func:`extract_text_and_pages` and audits via :func:`_audit_cv_text`
    directly. This PDF-level convenience wrapper is kept as the entry point for the
    ADR-039 render-roundtrip harness (``tests/ats/test_roundtrip.py``) and unit tests;
    keep its behaviour in lockstep with the production pair above.

    ``ledger`` (the Keyword Ledger, ADR-048/US203) annotates each MISSING keyword as
    *missing-claimable* (supported by the profile per the ledger) vs *missing-honest-gap*.

    ``target``/``region``/``condensation_exhausted`` (E042/US238, ADR-051 §5) drive the
    target-aware page-length band; ``target`` defaults to the region standard.

    ``vault_text_norm`` (#249 run-4, 2026-07-24): optional normalised literal vault
    text — when given, a keyword that clears THE shared presence predicate against
    it can never land in ``present_unsupported``, regardless of the ledger's own
    classification (defense-in-depth over the denial-narrowing fix in
    ``services/keyword_ledger.py``). ``None`` (default) reproduces prior behaviour.

    ``vault_skill_forms`` (#391 interim, ADR-076 amendment 4 point 6): optional
    pool of vault-attested skill strings — when given, drives the
    ``skills-weak-vault-tie`` advisory (see :func:`skills_weak_vault_tie`).
    ``None`` (default) reproduces prior behaviour (advisory never fires).
    """
    text, page_count = extract_text_and_pages(pdf_bytes)
    return _audit_cv_text(
        text, tailored, keywords, ledger, page_count=page_count,
        target=target, region=region, condensation_exhausted=condensation_exhausted,
        vault_text_norm=vault_text_norm, vault_skill_forms=vault_skill_forms,
        document_language=document_language, vault_index=vault_index,
        non_claim=non_claim,
    )


def _audit_letter_text(
    text: str,
    letter_data: dict[str, Any],
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    page_count: int | None = None,
    page_band_not_applicable: bool = False,
    vault_text_norm: str | None = None,
    pins: list | None = None,
    truth_floor_hits: set[str] | frozenset[str] = frozenset(),
    terminal_review=None,
    previous_report: dict | None = None,
    vault_index: Any = None,
    non_claim: NonClaimNames | None = None,
    document_language: str | None = None,
    employer_names: Sequence[str | None] | None = None,
) -> ATSReport:
    t = _norm(text)
    checks: list[ATSCheck] = []

    header = letter_data.get("header") or {}
    if header.get("name"):
        _check(checks, "contact-name", _find(header["name"], t) >= 0, f"name '{header['name']}' not found")
    if header.get("email"):
        _check(checks, "contact-email", _find(header["email"], t) >= 0, f"email '{header['email']}' not found")

    recipient = letter_data.get("recipient") or {}
    if recipient.get("company"):
        _check(checks, "recipient-company", _find(recipient["company"], t) >= 0,
               f"recipient company '{recipient['company']}' not found")

    paragraphs = (letter_data.get("body") or {}).get("paragraphs") or []
    for i, p in enumerate(paragraphs):
        # ADR-039 amendment (2026-08-31, #634): the FULL paragraph, not `p[:60]`.
        # The old 60-character probe made detection depend on where in the
        # paragraph the loss happened — measured boundary, exact and
        # template-independent: offset ≤ 59 failed, offset ≥ 60 passed silently.
        # This is the same predicate `_audit_cv_text` now uses on its bullets;
        # the two halves of one responsibility had diverged (ADR-066).
        if not _norm(p or ""):
            continue  # empty/whitespace paragraph — nothing to verify (mirrors the CV-side empty-field guard)
        _check(checks, f"body-{i}", _find(p, t) >= 0, f"body paragraph {i + 1} not found in extracted text")

    # E042/US240 (ADR-051 §6): DETECTION-ONLY page-length check against the region's
    # 1-page letter norm — deliberately no target resolution, no user setting, no
    # condense loop for letters this flavour (unlike the CV band in _audit_cv_text).
    # Same check id ("page-length") as the CV check — the frontend ATSChecksPanel and
    # the checks.page-length i18n key are shared by both document types. Skipped when
    # no count is given (text-only callers/tests), mirroring the CV behaviour. The
    # norm number always comes from REGION_NORMS — never hard-coded (ADR-051 §1).
    if page_band_not_applicable:
        _page_band_not_applicable(checks)          # ADR-079 cl. 4, as above
    elif page_count is not None:
        region = DEFAULT_REGION
        letter_pages = REGION_NORMS[region].letter_pages
        if page_count <= letter_pages:
            checks.append(ATSCheck(id="page-length", status="pass", details=None))
        else:
            checks.append(ATSCheck(
                id="page-length", status="fail",
                details=f"{page_count} pages — a {region} cover letter is {letter_pages} page",
                details_key="page-length-letter",
                details_params={"pages": page_count, "region": region,
                                "letterPages": letter_pages},
            ))

    # ── E056/ADR-077 clauses 2+3+5: pin presence + floor escalation ─────────
    # Measured over the override-applied letter body (the letter twin of the
    # CV seam); a truth-floor deletion is named on its entry (SF-PIN.6), and
    # a failed length band with present pins carries the structured driver.
    pin_entries = None
    if pins:
        from applire.schemas.ats import PinnedFactReportEntry
        from applire.services.pin_reach import letter_pin_present_in_dict, pin_ledger_conflicts

        pin_entries = []
        for pin in pins:
            if "letter" not in pin.targets:
                continue
            present = (not pin.stale) and letter_pin_present_in_dict(
                pin, letter_data
            )
            pin_entries.append(PinnedFactReportEntry(
                pin_id=pin.pin_id,
                entry_type=pin.entry_type,
                quote=pin.quote,
                present=present,
                stale=pin.stale,
                removed_by_truth_floor=pin.pin_id in truth_floor_hits,
                ledger_conflict=pin_ledger_conflicts(pin.quote, ledger),
            ))
        present_count = sum(1 for e in pin_entries if e.present)
        page_check = next((c for c in checks if c.id == "page-length"), None)
        if page_check is not None and page_check.status == "fail" and present_count:
            page_check.driver = {"pinned_facts": present_count}

    # ADR-094 clause L (US369, ruling W2-1b): the v2 letter carries the same
    # `document-language` row the CV does — every body sentence the ADR-066 per-item
    # predicate classifies as another language. Opt-in by the caller (the v2
    # pipeline passes the pinned language); v1 callers pass nothing and the report
    # is unchanged. A delivered letter still in the wrong language SHIPS with this
    # row red in the pre-download review — never a block (W2-1b).
    if document_language:
        from applire.services.letter_v2 import letter_language_fact

        _fact = letter_language_fact(letter_data, document_language)
        _document_language_check(
            checks,
            [(f"paragraph {f.paragraph_index + 1}", f.sentence) for f in _fact.foreign],
            document_language,
        )

    # #563 (D): the letter's terminal review answers the same question. No
    # `narrative-evidence` twin — the letter has no bullet corpus and no
    # `_restore_ledger_bullets` sibling (arc42 §5.3.23, unguarded surface 3), so the
    # narrative/tag distinction has no referent there.
    from applire.services.terminal_review_outcome import (
        TERMINAL_REVIEW_CHECK_ID,
        build_terminal_review_check,
        previous_check,
    )

    checks.append(
        build_terminal_review_check(
            terminal_review,
            previous=previous_check(previous_report, TERMINAL_REVIEW_CHECK_ID),
            document="cover_letter",
        )
    )

    # WP-R: the letter names its employer itself — the recipient block's company
    # joins the posting's names whenever the caller passed any (a caller passing
    # nothing keeps the pre-amendment full-text decision exactly).
    if non_claim is not None:
        non_claim = non_claim.plus_employers(recipient.get("company")).with_employer_clause()
    coverage = _keyword_coverage(
        t, keywords, ledger, vault_text_norm, vault_index, non_claim,
        document_language=document_language,
    )
    _other_language_check(checks, coverage)
    # 786:47: the candidate's own employers (the caller's) and the recipient.
    if employer_names is not None:
        _employer_repetition_check(
            checks, paragraphs, [*employer_names, recipient.get("company")]
        )
    report = _finish("cover_letter", checks, coverage)
    if pin_entries is not None:
        report.pinned_facts = pin_entries
    return report


def audit_cover_letter(
    pdf_bytes: bytes,
    letter_data: dict[str, Any],
    keywords: list[str],
    ledger: list[dict[str, Any]] | None = None,
    vault_text_norm: str | None = None,
    pins: list | None = None,
    truth_floor_hits: set[str] | frozenset[str] = frozenset(),
    terminal_review=None,
    previous_report: dict | None = None,
    vault_index: Any = None,
    non_claim: NonClaimNames | None = None,
    document_language: str | None = None,
    employer_names: Sequence[str | None] | None = None,
) -> ATSReport:
    """Audit a rendered cover letter PDF against the structured letter data and keywords.

    ``employer_names`` (786:47): the candidate's employers from the profile; with
    them (and the recipient's company) the ``employer-repetition`` advisory runs.
    ``None`` keeps the pre-change report.

    ``ledger`` (ADR-048/US203) splits each MISSING keyword into *missing-claimable* vs
    *missing-honest-gap*.

    E042/US240: reads the real page count via :func:`extract_text_and_pages` (one
    PdfReader pass, #171a-style) and threads it into :func:`_audit_letter_text` for
    the detection-only page-length check.

    ``vault_text_norm`` (#249 run-4): see :func:`audit_cv` — same shared-predicate
    guard on ``present_unsupported``, optional and back-compat.
    """
    text, page_count = extract_text_and_pages(pdf_bytes)
    return _audit_letter_text(
        text, letter_data, keywords, ledger, page_count=page_count,
        vault_text_norm=vault_text_norm,
        pins=pins, truth_floor_hits=truth_floor_hits,
        terminal_review=terminal_review, previous_report=previous_report,
        vault_index=vault_index, non_claim=non_claim,
        document_language=document_language, employer_names=employer_names,
    )


def profile_employer_names(profile_json: dict[str, Any] | None) -> list[str]:
    """786:47 — the employers the profile names (``work_experience[].company``),
    the names a letter anchors its claims on. Empty without a profile."""
    out: list[str] = []
    for w in (profile_json or {}).get("work_experience") or []:
        c = (w or {}).get("company") if isinstance(w, dict) else None
        if isinstance(c, str) and c.strip() and c.strip() not in out:
            out.append(c.strip())
    return out


def grounding_vault_index(profile_json: dict[str, Any] | None) -> Any:
    """ADR-090 clause 4 — the vault index the matched-form grounding reads: the
    Oracle's own ``build_vault_index`` over the CLAIMABLE part of the vault
    (``exclude_unconfirmed`` — an unconfirmed or denied entry cannot back a claim,
    ADR-061 clause 3). ``None`` when there is no profile, which keeps the
    literal-only behaviour. Never raises: an index failure grounds nothing."""
    if not profile_json:
        return None
    try:
        from applire.services.oracle.matchers.vault import build_vault_index
        from applire.services.profile.reconcile.stance import exclude_unconfirmed

        return build_vault_index(exclude_unconfirmed(profile_json))
    except Exception:  # pragma: no cover — defensive: grounding widens nothing on error
        return None
