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

"""The JD-analysis reviewer's prompt-facing view + GROUNDING FACTS block.

ADR-069 clause 4b (amended 2026-08-29, #617); ADR-078's third instance of
"the model sees content, not bookkeeping".

``build_job_analysis_review_prompt`` (``prompts/review_job_analysis.py``) used
to hand the reviewer the corrector's raw draft dict verbatim, including
``level_changes`` — a bookkeeping transport field the corrector's own output
carries between rounds. The captured evidence for #617 shows the cost of
that directly: round 2 of the reviewed chain flagged ``level_changes`` itself
as "fabricated content" (the #615 shape — a rendered INPUT carrying a field
the prompt's own rules give meaning to — recurring on a second seam). Two
things live in this module:

1. :func:`reviewer_view` — a DEEP COPY of the draft keeping only the
   extraction schema's own keys. Deep copy is non-negotiable, not a style
   choice: ``services/reviewer.py::review_and_refine`` passes the SAME dict
   object this function receives into ``draft_history``, and
   ``services/jd_level_guard.py::apply_jd_level_guard`` later reads
   ``draft_history`` (raw, unfiltered dicts) to reconstruct declared level
   moves across rounds. A view that mutates its input, or that shares a
   nested dict/list with it, would silently corrupt that history out from
   under the level guard.

2. :func:`grounding_facts` — a deterministic, code-computed block stating,
   for every concept term / title / company / seniority value / scope quote
   / leadership quote in the view, whether it is present VERBATIM in the
   posting under one fixed normalisation. ADR-062 classification (per
   clause 6, as ``applire-prompt-first`` requires this be named): this is a
   **fact** — whole-word/phrase presence under a single fixed normalisation
   has exactly one answer, no prose read for meaning. What this deliberately
   does NOT compute is POLARITY: a verbatim "Kubernetes" may still be named
   only to EXCLUDE it ("no Kubernetes experience needed") — that stays the
   reviewer's judgement, under the new check 1b MISREAD POLARITY in
   ``prompts/review_job_analysis.py``, never decided here.

Evidence (#617, replay 2026-08-29, captured run + real-provider replay on
``mistralai/mistral-medium-3-5``): the captured erosion (round-1 21
required-skills -> round-5 5, never approved) was 14 of 16 removed terms
flagged for reasons this rule forecloses ("not stated", "not standalone",
"only part of a larger phrase", "capitalisation differs", "role title, not a
skill") despite being verbatim-present in the posting. Rewording the checks
alone (no facts block) still left ~45 verbatim terms flagged across five
calls. Supplying this block converged the same replay to 0 verbatim-grounded
terms flagged in 5/5 calls, with one stable 6-issue verdict (the residual —
paraphrase/nominalisation judgement calls the block correctly leaves open).
Full replay table: ``Documents/Architecture/ADR.md`` ADR-069, 2026-08-29
amendment.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

#: Top-level keys of the JobAnalysis extraction schema — the "Schema:" JSON
#: block in ``prompts/job_analysis.py``'s ``SYSTEM_PROMPT`` — and the ONLY
#: keys :func:`reviewer_view` keeps. An allowlist, not a denylist: the
#: failure this exists to prevent is a *new* key silently riding into the
#: reviewer's view (most concretely, the corrector's own ``level_changes``
#: transport field), and only an allowlist fails in the safe direction
#: (ADR-078 clause 2's reasoning, applied to a second view). Mirrored here
#: rather than regex-parsed at runtime, so a schema edit cannot silently
#: widen what the reviewer sees without a code change landing too;
#: ``tests/unit/test_jd_grounding_617.py`` pins this constant against a live
#: regex parse of the extraction prompt's own schema block, so the mirror
#: cannot drift unnoticed.
JD_SCHEMA_KEYS: frozenset[str] = frozenset(
    {
        "company_name",
        "role_title",
        "required_skills",
        "nice_to_have_skills",
        "keywords",
        "seniority_level",
        "company_culture_signals",
        "language_requirement",
        "berufsbild_code",
        "berufsbild_label",
        "scope_requirements",
        "leadership_emphasis",
    }
)

# Hyphen-family characters that become a SPACE — splitting a hyphenated
# compound into two separately matchable words ("information-retrieval" ->
# "information retrieval"), never simply dropped (which would instead glue
# the two words together).
_HYPHEN_FAMILY = str.maketrans(
    {
        "-": " ",  # U+002D hyphen-minus
        "–": " ",  # en dash
        "—": " ",  # em dash
        "/": " ",
        "_": " ",
    }
)
# Punctuation is stripped — EXCEPT the three characters that ARE the name:
# "C++", "C#", ".NET", "Node.js". Deleting them collapses a term to a bare
# one- or two-letter word ("c", "net") which then whole-word-matches an
# unrelated sentence ("a Grade C in Mathematics", "own net margin targets")
# and would be reported as a settled verbatim fact — the false-positive
# direction this module must never take (adversarial pass, 2026-08-30).
# Keeping them costs only false NEGATIVES ("Node.js" vs a posting's
# "Node JS"), which fall back to the reviewer's judgement.
_NON_WORD_RE = re.compile(r"[^\w\s+#.]", re.UNICODE)
# URLs, e-mail addresses and bare domains are stripped from the POSTING
# before normalisation: a concept named only inside "ai-solutions.de" or
# "careers@acme.io" is not a stated requirement, and the hyphen-to-space
# rule would otherwise isolate "ai" as a matchable word. Terms are never
# passed through this — only the posting text is.
_LOCATOR_RE = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|[\w.+-]+@[\w-]+\.[\w.-]+"
    r"|(?<![\w.])[\w-]+(?:\.[\w-]+)*\.(?:com|de|net|org|io|ai|eu|ch|at|co|uk|dev|app|me)(?![\w])",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")

# A SIMPLE, deliberately crude suffix stripper for the "words found: k/n"
# INFORMATIONAL count on non-verbatim terms only. Never used by
# is_verbatim(), which is exact whole-word/phrase matching and nothing
# else. Longest suffix first so "-ations" is never mistaken for the
# shorter "-s".
_STEM_SUFFIXES = ("ations", "ation", "ions", "ing", "ers", "ion", "es", "ed", "er", "ly", "s")


def _stem(token: str) -> str:
    for suffix in _STEM_SUFFIXES:
        if len(token) - len(suffix) >= 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def strip_locators(text: str) -> str:
    """Remove URLs, e-mail addresses and bare domains from POSTING text.

    A concept that appears only inside a link or an address is not a stated
    requirement — and the hyphen-to-space rule would otherwise isolate its
    parts as matchable words ("ai-solutions.de" -> "ai solutions de", making
    a fabricated "AI" requirement read as verbatim-grounded). Applied to the
    posting only, never to an extracted term.
    """
    return _LOCATOR_RE.sub(" ", text or "")


def normalise(text: str) -> str:
    """The fixed normalisation shared by every verbatim check in this module.

    NFKC -> lower-case -> eszett -> "ss" -> hyphen/en-dash/em-dash/slash/
    underscore -> space -> strip remaining punctuation EXCEPT ``+``, ``#``
    and ``.`` (they are part of "C++", "C#", ".NET", "Node.js" — see
    :data:`_NON_WORD_RE`) -> collapse whitespace. Applying the SAME
    transform to both the extracted term and the posting text is what makes
    hyphenation ("information-retrieval"), case ("Agentic Workflows"), and a
    job board's own punctuation ("Seniority level: Mid-Senior level") never
    read as "not grounded". Curly vs. straight apostrophes fall out as a
    side effect of the punctuation strip (both are removed, not replaced —
    unlike the hyphen family above), so "Wendy's" normalises identically
    whichever apostrophe character the source used.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.lower()
    # NFKC does NOT fold eszett; Swiss German never writes it, so a DACH
    # posting's "Grosskunden" and a vault term's "Großkunden" are the same
    # word and must compare equal (adversarial pass, 2026-08-30).
    folded = folded.replace("ß", "ss")
    folded = folded.translate(_HYPHEN_FAMILY)
    folded = _NON_WORD_RE.sub("", folded)
    folded = _WHITESPACE_RE.sub(" ", folded).strip()
    return folded


def is_verbatim(term: Any, posting_norm: str) -> bool:
    """Whole-word/phrase match of ``normalise(term)`` inside an ALREADY
    normalised posting.

    ``posting_norm`` is the posting text after :func:`normalise` — callers
    normalise the posting ONCE and reuse it across every term (see
    :func:`grounding_facts`) rather than re-normalising per call.

    Bounded on both sides so a short term is never "found" merely as a
    substring of a longer word — "ai" is not found inside "domain" (the
    #207 precedent). Deliberately POLARITY-BLIND: "Kubernetes" is verbatim
    in "no Kubernetes experience needed" — whether the posting names it to
    REQUIRE or to EXCLUDE it is a judgement (ADR-062 clause 1), left
    entirely to the reviewer's check 1b, never decided here. A non-string or
    empty term returns False (an empty pattern would otherwise match
    everywhere, which is never the intended answer).
    """
    if not isinstance(term, str):
        return False
    norm_term = normalise(term)
    if not norm_term:
        return False
    pattern = r"(?<!\w)" + re.escape(norm_term) + r"(?!\w)"
    return re.search(pattern, posting_norm, flags=re.UNICODE) is not None


def reviewer_view(draft: dict[str, Any]) -> dict[str, Any]:
    """A DEEP COPY of ``draft`` keeping only :data:`JD_SCHEMA_KEYS`.

    Never mutates ``draft`` and never shares a nested dict/list with it —
    see the module docstring for why (``draft_history`` / the level guard
    read the ORIGINAL objects, not this view). A nested mangled shape (e.g.
    the captured ``{"leadership_led": ..., "quote": ...}`` in place of
    ``{"emphasis": ..., "quote": ...}``) is kept AS-IS inside its retained
    top-level key: this function only decides which TOP-LEVEL keys are
    content vs. bookkeeping, never restructures a value, so the reviewer
    still sees — and can flag — the mangled key itself.

    Tolerant of a non-dict input (returned unchanged): a prompt-input filter
    must never become a new way for the caller to fail.
    """
    if not isinstance(draft, dict):
        return draft
    return {key: copy.deepcopy(value) for key, value in draft.items() if key in JD_SCHEMA_KEYS}


def _words_found(
    term: str, posting_tokens: frozenset[str], posting_stems: frozenset[str]
) -> tuple[int, int]:
    """(found, total) content-word overlap between ``term`` and the posting,
    tolerant of the crude suffix stripping :func:`_stem` performs.
    Informational only — never consulted by :func:`is_verbatim`."""
    tokens = normalise(term).split()
    if not tokens:
        return 0, 0
    found = sum(1 for tok in tokens if tok in posting_tokens or _stem(tok) in posting_stems)
    return found, len(tokens)


def _fact(
    term: Any,
    posting_norm: str,
    posting_tokens: frozenset[str],
    posting_stems: frozenset[str],
) -> str | None:
    """'"term": verbatim yes' or '"term": verbatim no (words found: k/n)'.

    ``None`` when ``term`` is not a usable non-empty string — nothing to
    check, so nothing is claimed (a null ``company_name``, an empty
    ``scope_requirements`` quote, a mangled ``leadership_emphasis`` with no
    ``quote`` key all fall here and are silently omitted from the block).
    """
    if not isinstance(term, str):
        return None
    term = term.strip()
    if not term:
        return None
    if is_verbatim(term, posting_norm):
        return f'"{term}": verbatim yes'
    found, total = _words_found(term, posting_tokens, posting_stems)
    return f'"{term}": verbatim no (words found: {found}/{total})'


_GROUNDING_FACTS_HEADER = (
    "GROUNDING FACTS (code-computed, not the model's judgement — ADR-069 clause "
    '4b): a term below marked "verbatim yes" is present in the SOURCE JOB POSTING '
    "above under a fixed normalisation (NFKC, lower-case, hyphen/en-dash/em-dash/"
    "slash/underscore -> space, eszett -> ss, other punctuation stripped except "
    "+ # . as in C++/C#/.NET, links and e-mail addresses ignored, whole-word/phrase "
    'boundaries). A "verbatim yes" term is NEVER grounds for a "not stated", "not '
    'standalone", "only part of a phrase", capitalisation, "role title not a '
    'skill", or "not explicitly listed as a requirement" finding — treat it as '
    "settled. The one still-open question against a verbatim-yes term is POLARITY "
    "(check 1b) — the posting may name it only to exclude or negate it. A "
    '"verbatim no" term stays your own judgement; its words-found count is '
    "informational, not a verdict."
)


def grounding_facts(view: dict[str, Any], jd_text: str) -> str:
    """The deterministic GROUNDING FACTS block appended to the reviewer's
    user prompt (ADR-069 clause 4b). No LLM call — everything here is a
    fixed-normalisation string computation over ``view`` and ``jd_text``.

    Fields covered, in this fixed order: ``required_skills``,
    ``nice_to_have_skills``, ``keywords`` (each entry, in the DRAFT's own
    order — never re-sorted, so the block reads as a companion to the
    EXTRACTED ANALYSIS block above it), then ``role_title``,
    ``company_name``, ``seniority_level``, then each
    ``scope_requirements[].quote``, then ``leadership_emphasis.quote``. A
    field that is null, missing, or the wrong shape simply contributes no
    line — this function reports facts about what IS there, it invents
    nothing about what is not.
    """
    from applire.services.untrusted_text import items_note

    posting_norm = normalise(strip_locators(jd_text or ""))
    posting_tokens = frozenset(posting_norm.split())
    posting_stems = frozenset(_stem(tok) for tok in posting_tokens)

    def fact(term: Any) -> str | None:
        return _fact(term, posting_norm, posting_tokens, posting_stems)

    # ADR-084 embedding point 5 (Form B): every term below is quoted back from
    # the posting, and the block's own header tells the reviewer to treat a
    # "verbatim yes" as SETTLED — which is precisely the authority an injected
    # requirement would inherit. Form B, not a fence: the header above is OUR
    # instruction and must keep its force.
    lines: list[str] = [
        _GROUNDING_FACTS_HEADER,
        "",
        items_note("terms and quotes"),
    ]

    for field in ("required_skills", "nice_to_have_skills", "keywords"):
        terms = view.get(field)
        if not isinstance(terms, list):
            continue
        rendered = [r for r in (fact(t) for t in terms) if r is not None]
        if not rendered:
            continue
        lines.append("")
        lines.append(f"{field}:")
        lines.extend(f"  - {r}" for r in rendered)

    scalar_lines = [
        f"{label} — {r}"
        for label in ("role_title", "company_name", "seniority_level")
        if (r := fact(view.get(label))) is not None
    ]
    if scalar_lines:
        lines.append("")
        lines.extend(scalar_lines)

    scope = view.get("scope_requirements")
    if isinstance(scope, list):
        scope_lines = []
        for i, entry in enumerate(scope):
            if not isinstance(entry, dict):
                continue
            r = fact(entry.get("quote"))
            if r is not None:
                scope_lines.append(f"scope_requirements[{i}].quote — {r}")
        if scope_lines:
            lines.append("")
            lines.extend(scope_lines)

    leadership = view.get("leadership_emphasis")
    if isinstance(leadership, dict):
        r = fact(leadership.get("quote"))
        if r is not None:
            lines.append("")
            lines.append(f"leadership_emphasis.quote — {r}")

    return "\n".join(lines)
