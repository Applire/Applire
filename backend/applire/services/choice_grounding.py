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

"""Deterministic grounding filter for interview starting-point chips (#110, #236).

The Mode-A question generator drafts 2-3 first-person "starting point" chips a
candidate can select into the answer box. The LLM sees rich JD context but only
a thin profile summary, so a drafted chip can attribute JD technology to a
candidate who has never touched it (blind PQ 2026-07-02, F5) — one careless
click away from an invented project.

The prompt now instructs the model to ground chips in profile evidence; THIS
module is the guarantee. A chip that *asserts* experience with a cluster/JD
term survives only when the profile actually evidences that term (judged by
``surface_present``, the shared US212 presence predicate). Honesty frames —
chips that deny direct experience — may name the term; denying is the point.

#236 (founder-acceptance F5): the whole-profile evidence pool above is too
permissive once a chip names a specific employer. The live trace showed a chip
naming BioNTech that was truthful about the TECH (LangGraph/RAG — BioNTech
really does have both) but fabricated the NARRATIVE CONTEXT ("clinical data
workflows under tight timelines" — conflated from an unrelated bullet, plus
invented urgency). Whole-profile evidence checking never catches this because
it only ever checked CLUSTER terms, never the chip's own free-text assertions,
and never scoped by WHICH employer the chip named. ``filter_ungrounded_choices``
now runs an employer-scoped guard first when a chip names a known employer:
cluster/JD terms must be evidenced under THAT employer specifically (tech×
employer attribution), and the chip's remaining free-text content must clear a
token-coverage threshold against that employer's own bullets (fabricated-context
detection). Chips naming no employer are unaffected — today's whole-profile
cluster-term check still applies.

Truthfulness-critical: sits directly beside the honesty pipeline (ADR-040).

Honesty-frame clause scoping (adversarial pass 2026-07-23): ``_is_honesty_frame``
matches markers against the WHOLE chip, so a single "haven't" anywhere used to
exempt the entire chip from every check above — including the #236
employer-scoped guard. Live trace: "I haven't used Tailwind CSS directly, but
I've worked with React and Next.js ... at StartupXYZ" — Next.js/React are real,
but only at TechCorp GmbH, never StartupXYZ; the fabricated, misattributed
AFFIRMATIVE clause rode along with the legitimate Tailwind denial.
``filter_ungrounded_choices`` now splits an honesty frame at its denial→
affirmation pivot (``_split_honesty_frame``) and runs the SAME checks — cluster/
JD-term evidence, and the #236 employer-scoped guard when it names a known
employer — on the affirmative remainder only. The denial clause itself stays
fully exempt (naming the denied term is the point). A pure denial with no
pivot keeps today's full-chip exemption.
"""

import re
from typing import Any

from applire.services.ats_audit import _norm, skill_tokens, surface_present

# A chip containing one of these (casefolded) markers is an honesty frame:
# it names a term to DENY or hedge direct experience, not to claim it.
# UI languages are en/de (chips are generated in the user's language).
_HONESTY_MARKERS: tuple[str, ...] = (
    # English
    "haven't",
    "have not",
    "has not",
    "not directly",
    "no direct",
    "not yet",
    "never worked",
    "don't have",
    "do not have",
    "closest experience",
    # German
    "nicht direkt",
    "bisher nicht",
    "noch nicht",
    "noch keine",
    "noch nie",
    "keine direkte",
    "habe ich nicht",
)


def _evidence_norm(profile: dict[str, Any]) -> str:
    """Normalised free-text haystack of everything the profile evidences."""
    parts: list[str] = []
    for skill in profile.get("skills") or []:
        if isinstance(skill, dict):
            parts.extend(str(v) for v in (skill.get("name"), skill.get("category")) if v)
        elif skill:
            parts.append(str(skill))
    for entry in (
        (profile.get("work_experience") or [])
        + (profile.get("projects") or [])
        + (profile.get("volunteer_activities") or [])
    ):
        if not isinstance(entry, dict):
            continue
        for key in ("company", "role", "name", "title", "industry_context", "description"):
            if entry.get(key):
                parts.append(str(entry[key]))
        for key in ("technologies", "responsibilities", "achievements"):
            parts.extend(str(v) for v in (entry.get(key) or []) if v)
    for entry in profile.get("certifications") or []:
        if isinstance(entry, dict) and entry.get("name"):
            parts.append(str(entry["name"]))
    for entry in profile.get("education") or []:
        if isinstance(entry, dict):
            parts.extend(str(v) for v in (entry.get("degree"), entry.get("field")) if v)
    return _norm("\n".join(parts))


def _cluster_terms(cluster: dict[str, Any]) -> list[str]:
    """The JD-side terms a chip could wrongly attribute to the candidate."""
    terms: list[str] = []
    seen: set[str] = set()
    for term in [
        cluster.get("label") or "",
        *(cluster.get("gaps") or []),
        *(cluster.get("jd_skills") or []),
    ]:
        term = str(term).strip()
        n = _norm(term)
        if term and n and n not in seen:
            seen.add(n)
            terms.append(term)
    return terms


def _is_honesty_frame(choice: str) -> bool:
    # Real models emit typographic apostrophes ("haven’t", U+2019/U+02BC) —
    # normalise to ASCII before marker matching (blind agent probe 2026-07-11:
    # the ASCII-only match over-dropped truthful frames).
    folded = choice.casefold().replace("’", "'").replace("ʼ", "'")
    return any(marker in folded for marker in _HONESTY_MARKERS)


# ── honesty-frame clause scoping — denial→affirmation pivot ─────────────────
# Deterministic pivot phrases (EN + DE) that separate a denial clause from an
# affirmative remainder in the "I haven't worked with X directly, but ..."
# shape every real honesty-frame chip in this test suite uses. Matched
# case-insensitively against a length-preserving fold (quotes only — NOT
# casefold(), which can change string length for e.g. German "ß" and would
# desync the position map back onto the original text).
_PIVOT_MARKERS: tuple[str, ...] = (
    ", but ", "; but ", ". but ",
    ", though ", ", however ",
    " — but ",
    # German
    ", aber ", ". aber ",
)


def _fold_for_split(text: str) -> str:
    """Length-preserving lower+quote-fold used only to locate marker/pivot
    byte offsets that must map 1:1 back onto the original string."""
    return _fold_quotes(text).lower()


def _split_honesty_frame(text: str) -> tuple[str, str | None]:
    """Split an honesty-frame chip at its denial→affirmation pivot.

    Returns ``(text, None)`` for a pure denial with no pivot after its
    honesty marker — today's full-chip exemption is unchanged. Otherwise
    returns ``(denial, affirmative)`` split at the LAST pivot phrase that
    occurs after the FIRST honesty marker in the chip. The denial half is
    never checked (naming the denied term is the point); the affirmative
    half goes through the normal grounding pipeline as its own chip.
    """
    folded = _fold_for_split(text)

    marker_pos = -1
    for marker in _HONESTY_MARKERS:
        pos = folded.find(marker)
        if pos != -1 and (marker_pos == -1 or pos < marker_pos):
            marker_pos = pos
    if marker_pos == -1:
        return text, None  # caller only invokes this when _is_honesty_frame matched

    pivot_start, pivot_end = -1, -1
    for pivot in _PIVOT_MARKERS:
        search_from = marker_pos
        while True:
            idx = folded.find(pivot, search_from)
            if idx == -1:
                break
            if idx > pivot_start:
                pivot_start, pivot_end = idx, idx + len(pivot)
            search_from = idx + 1

    if pivot_start == -1:
        return text, None  # pure denial — no affirmative clause to check

    return text[:pivot_start], text[pivot_end:]


# ── #236 — employer-scoped attribution guard ─────────────────────────────────
# _norm's NFKC pass already folds NBSP to a plain space, but typographic quotes
# are a distinct codepoint (not a compatibility decomposition) — fold them
# explicitly before matching, mirroring _is_honesty_frame's apostrophe fold.
_QUOTE_FOLD = str.maketrans({
    "’": "'", "‘": "'", "ʼ": "'",
    "“": '"', "”": '"',
    " ": " ",
})


def _fold_quotes(text: str) -> str:
    return text.translate(_QUOTE_FOLD)


# Corporate suffixes stripped from a profile company name before it is
# searched for in a chip — "BioNTech SE" must be found by a chip that only
# says "BioNTech" (founder-acceptance F5, issue #236).
_LEGAL_SUFFIX_RE = re.compile(
    r"\s*,?\s*\b("
    r"gmbh\s*&\s*co\.?\s*kg|gmbh|mbh|ag|se|kg|ug|e\.?v\.?|"
    r"inc\.?|incorporated|ltd\.?|limited|llc|corp\.?|corporation|co\.?|"
    r"plc|s\.a\.?|n\.v\.?|oy|ab|bv|sarl|sas|spa"
    r")\.?\s*$",
    re.IGNORECASE,
)

# Function words / generic interview-chip scaffolding stripped before the
# fabricated-context content-coverage check — NOT attributable content on
# their own (either language may appear; chips are drafted in the UI language).
_CONTENT_STOPWORDS = frozenset({
    "i", "my", "our", "we", "at", "on", "under", "as", "is", "are", "was", "were",
    "be", "been", "that", "this", "these", "those", "such", "like", "including",
    "include", "included", "work", "worked", "working", "contributed", "involved",
    "part", "role", "solution", "solutions", "using", "use", "used", "over",
    "an", "of", "for", "with", "the", "a", "to", "in", "and", "or", "&",
    # German
    "ich", "habe", "mein", "meine", "meinem", "meiner", "unser", "bei", "und",
    "im", "in", "der", "die", "das", "den", "dem", "eine", "einen", "einer",
    "fur", "für", "unter", "ein", "mit", "wie", "zur", "zum", "von", "auf",
})

# Shared-root cognate fallback: German/English career vocabulary is heavy on
# Latinate loanwords ("automatisiert" / "automates", "System" / "system") that
# a strict substring match misses across languages or verb inflection. A token
# counts as present when a long-enough prefix matches a corpus token too — a
# faithful DE paraphrase of an English CV bullet must still clear the bar.
_FUZZY_PREFIX_LEN = 6

# Content-token coverage a chip's remaining free text must reach against its
# named employer's OWN evidence, once cluster/JD terms are cleared. Tuned
# against the #236 fixtures: the verbatim fabricated trace chip ("...clinical
# data workflows under tight timelines") scores 0.60 (EN) / 0.30 (DE) — some
# of its tokens are real BioNTech vocabulary lifted from an unrelated bullet,
# which is exactly the conflation this guard exists to catch. A faithful
# paraphrase of the real bullet scores 0.78-0.89 (EN) / 0.86 (DE). 0.65 sits
# in the gap and separates every fixture correctly. Erring low is deliberate —
# a lost suggestion is cheap, a fabricated one is the brand failure.
_CONTENT_COVERAGE_MIN = 0.65


def _employer_base(company: str) -> str:
    """Normalised company name with a trailing legal-form suffix stripped."""
    base = _norm(company)
    prev = None
    while prev != base:
        prev = base
        base = _LEGAL_SUFFIX_RE.sub("", base).strip()
    return base


def _match_employers(chip_norm: str, work_experience: list) -> list[dict]:
    """Work entries whose company is named (word-boundary) in the chip."""
    matched = []
    for entry in work_experience:
        if not isinstance(entry, dict):
            continue
        base = _employer_base(str(entry.get("company") or ""))
        if len(base) < 2:
            continue
        if re.search(r"\b" + re.escape(base) + r"\b", chip_norm):
            matched.append(entry)
    return matched


def _employer_scoped_evidence(matched_entries: list[dict], profile: dict) -> str:
    """Union evidence text for the NAMED employer(s) only (#236).

    Own bullets/tech of each matched work entry, skills whose
    ``experience_refs`` point at one of them, and any nested projects
    (``associated_experience`` == the matched entry's id) — mirrors the
    Oracle vault index's ownership model (services/oracle/matchers/vault.py)
    without importing it (that package has a parallel owner this session).
    Multiple entries can match the same employer (e.g. two stints at the same
    company); their evidence is unioned rather than picked apart, matching the
    "ambiguous multi-employer chips: fail-closed only on clear violations"
    guidance — a candidate blending two roles at ONE employer is not the
    misattribution this guard targets.
    """
    ids = {e.get("id") for e in matched_entries if e.get("id")}
    parts: list[str] = []
    for entry in matched_entries:
        for key in ("company", "role", "industry_context"):
            if entry.get(key):
                parts.append(str(entry[key]))
        for key in ("technologies", "responsibilities", "achievements"):
            parts.extend(str(v) for v in (entry.get(key) or []) if v)
    for skill in profile.get("skills") or []:
        if isinstance(skill, dict) and ids & set(skill.get("experience_refs") or []):
            if skill.get("name"):
                parts.append(str(skill["name"]))
    for project in profile.get("projects") or []:
        if isinstance(project, dict) and project.get("associated_experience") in ids:
            for key in ("name", "role", "description"):
                if project.get(key):
                    parts.append(str(project[key]))
            for key in ("technologies", "responsibilities", "achievements"):
                parts.extend(str(v) for v in (project.get(key) or []) if v)
    return _norm(" ".join(parts))


def _content_tokens(text_norm: str, strip_bases: set[str]) -> frozenset[str]:
    """The chip's checkable content tokens: employer name(s) and generic
    scaffolding stripped, via the shared ``skill_tokens`` stemmer/edge-punct
    fold (#172) so morphology and formatting can't dodge the check."""
    stripped = text_norm
    for base in strip_bases:
        if base:
            stripped = re.sub(r"\b" + re.escape(base) + r"\b", " ", stripped)
    return frozenset(t for t in skill_tokens(stripped) if t not in _CONTENT_STOPWORDS)


def _fuzzy_present(token: str, corpus_norm: str, corpus_tokens: frozenset[str]) -> bool:
    if surface_present(token, corpus_norm):
        return True
    if len(token) >= _FUZZY_PREFIX_LEN:
        prefix = token[:_FUZZY_PREFIX_LEN]
        return any(
            t.startswith(prefix) for t in corpus_tokens if len(t) >= _FUZZY_PREFIX_LEN
        )
    return False


def _passes_employer_scoped_guard(
    choice_norm: str,
    terms: list[str],
    matched_entries: list[dict],
    profile: dict,
    *,
    require_asserted_for_content_check: bool = False,
) -> bool:
    """Rule A.1 (tech×employer) + A.2 (fabricated-context coverage), #236.

    ``require_asserted_for_content_check`` (honesty-frame affirmative clauses
    only, adversarial pass 2026-07-23): skip A.2 when the clause asserts NO
    cluster/JD term at all. A.2 exists to catch invented narrative WRAPPED
    AROUND an already-verified tech claim; without at least one asserted term
    it has no verified peg to scope itself against, and firing it anyway would
    over-drop vague-but-harmless affirmations (the #207 over-drop lesson) —
    e.g. an honesty frame's affirmative half that merely gestures back at the
    denied term with a pronoun ("... but I've run it in production at
    Applire") rather than restating it. The full-chip (non-honesty-frame) path
    keeps A.2 unconditional, matching the original #236 fixtures.
    """
    scoped_evidence = _employer_scoped_evidence(matched_entries, profile)

    # A.1 — cluster/JD terms the chip asserts must be evidenced under THIS
    # employer specifically, not merely somewhere in the whole profile.
    asserted = [t for t in terms if surface_present(t, choice_norm)]
    if not all(surface_present(t, scoped_evidence) for t in asserted):
        return False

    if require_asserted_for_content_check and not asserted:
        return True

    # A.2 — the chip's remaining free text must reach a defensible coverage
    # threshold against the employer's own bullets (catches invented context
    # built from real-but-unrelated tokens, e.g. the #236 trace chip).
    strip_bases = {_employer_base(str(e.get("company") or "")) for e in matched_entries}
    content = _content_tokens(choice_norm, strip_bases)
    if not content:
        return True  # nothing left to falsify — no specific claim beyond the terms above
    corpus_tokens = frozenset(scoped_evidence.split())
    hits = sum(1 for t in content if _fuzzy_present(t, scoped_evidence, corpus_tokens))
    return (hits / len(content)) >= _CONTENT_COVERAGE_MIN


def filter_ungrounded_choices(
    choices: list[str] | None,
    cluster: dict[str, Any],
    profile: dict[str, Any],
    gap_category: str | None,  # noqa: ARG001 — one rule for all categories; kept for call-site clarity
) -> list[str] | None:
    """Drop chips that assert experience the profile doesn't evidence.

    Keep a chip when every cluster/JD term it mentions is evidenced in the
    profile. Returns ``None`` when nothing survives (the UI then shows the
    plain answer box — no scaffold beats a fabricated one).

    #236: when a chip names a known employer, the check is scoped to THAT
    employer's own evidence (tech×employer attribution) and its remaining
    free text must clear a content-coverage bar against that employer's own
    bullets (fabricated-context detection) — see ``_passes_employer_scoped_guard``.
    Chips naming no employer keep the original whole-profile cluster-term check.

    Honesty-frame clause scoping (adversarial pass 2026-07-23): a chip that
    denies direct experience ("I haven't worked with X directly, but ...") is
    split at its denial→affirmation pivot (``_split_honesty_frame``) and the
    checks above run on the AFFIRMATIVE remainder only. The denial clause
    itself is always exempt — naming the denied term is the point. A pure
    denial with no pivot keeps full-chip exemption, unchanged.
    """
    if not choices:
        return None

    evidence = _evidence_norm(profile)
    terms = _cluster_terms(cluster)
    work_experience = profile.get("work_experience") or []

    kept: list[str] = []
    for choice in choices:
        text = str(choice).strip()
        if not text:
            continue
        if _is_honesty_frame(text):
            _denial, affirmative = _split_honesty_frame(text)
            if affirmative is None:
                kept.append(text)  # pure denial, no pivot — full exemption
                continue
            aff_norm = _norm(_fold_quotes(affirmative))
            matched_entries = _match_employers(aff_norm, work_experience)
            if matched_entries:
                if _passes_employer_scoped_guard(
                    aff_norm, terms, matched_entries, profile,
                    require_asserted_for_content_check=True,
                ):
                    kept.append(text)
                continue
            asserted = [t for t in terms if surface_present(t, aff_norm)]
            if all(surface_present(t, evidence) for t in asserted):
                kept.append(text)
            continue
        choice_norm = _norm(_fold_quotes(text))
        matched_entries = _match_employers(choice_norm, work_experience)
        if matched_entries:
            if _passes_employer_scoped_guard(choice_norm, terms, matched_entries, profile):
                kept.append(text)
            continue
        asserted = [t for t in terms if surface_present(t, choice_norm)]
        if all(surface_present(t, evidence) for t in asserted):
            kept.append(text)
    return kept or None
