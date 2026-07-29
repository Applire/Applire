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
``surface_present``, the shared US212 presence predicate). Denial-level choices
— chips that deny direct experience — may name the term; denying is the point.

#236 (founder-acceptance F5): the whole-profile evidence pool above is too
permissive once a chip names a specific employer. The live trace showed a chip
naming NordPharm that was truthful about the TECH (LangGraph/RAG — NordPharm
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

ADR-062 classification (declared per clause 6): this module computes FACTS
only — token/substring presence of a named term against a text corpus, and
company-name/legal-suffix matching. It never classifies what a chip *means*.

Until 2026-07-29 that line was crossed: ``_is_honesty_frame`` matched a
casefolded chip against ``_HONESTY_MARKERS``, a growing phrase list, to GUESS
whether a chip was a denial or an assertion — exactly the "judgement disguised
as a deterministic rule" ADR-062 names ``choice_grounding`` for. It was wrong
in both directions the list was tuned against (typographic apostrophes,
"no experience"/"keine Erfahrung") and reproducibly still wrong afterwards
("I've never touched TOGAF.", "TOGAF kenne ich nicht.", an NBSP inside a
marker phrase — none matched). Per ADR-062 clause 3 (deletion over repair)
and clause 2 (judgements go to the model), the classification itself has been
moved to the generator: ``prompts/interview.py`` now asks the model to tag
every choice with its own ``level`` ("direct" | "partial" | "denial") as part
of the coverage rule it already has to reason about. This module no longer
GUESSES the level — it reads it off the choice and only ever *compares*
(a fact), never *interprets* (a judgement). A choice with no level, or an
unrecognised one, falls back to the pre-existing full grounding check — the
safe direction, identical to the behaviour before levels existed.

A "denial"-tagged choice is not trusted blindly, because the model can
mislabel its own output: the chip's TEXT remains the authority for the
company-attribution and content-coverage facts already checked here (#236),
whether or not it is tagged "denial". Only the *term-evidence* requirement —
"every cluster/JD term the chip names must appear in the profile" — is what a
denial is exempt from, because a denial names the term to deny it; there is
nothing to ground. See ``_level_of`` and the "denial" branch of
``filter_ungrounded_choices`` for the exact scope of that exemption.

Denial-clause scoping (adversarial pass 2026-07-23, preserved under the level
tag): a chip can combine a denial with a bridging affirmative claim — "I
haven't used Tailwind CSS directly, but I've worked with React and Next.js
... at StartupXYZ." Live trace: Next.js/React are real, but only at TechCorp
GmbH, never StartupXYZ; the fabricated, misattributed AFFIRMATIVE clause rode
along with the legitimate Tailwind denial. For a "denial"-tagged choice,
``filter_ungrounded_choices`` splits it at its FIRST denial→affirmation pivot
(``_split_denial_choice``) and runs the SAME checks — cluster/JD-term
evidence, and the #236 employer-scoped guard when it names a known employer —
on the remainder AFTER that pivot only. Everything BEFORE the pivot stays
fully exempt (naming the denied term is the point).

**F2 (2026-07-29, fixed): term-evidence stays clause-ORDER-dependent by
necessity; the #236 employer-scoped guard no longer is.**
``_split_denial_choice`` has no way to tell WHICH side of the pivot is the
denial and which is the affirmation — it only locates the pivot phrase
(", but " / ", aber " / ...) and always treats the FIRST clause as the
exempt denial. For the TERM-EVIDENCE check this is unavoidable: "I worked
with Y at Employer" and "I haven't worked with Y at Employer" surface-match
the term "Y" identically, so a deterministic rule genuinely cannot tell
which clause is the legitimate denial by that check alone — checking both
clauses' terms would drop the single most common LEGITIMATE denial shape
(a chip denying the cluster's own JD term while bridging to a truthful
adjacent claim) exactly as often as it would catch a fabrication. That half
of the gap is real and stays open by design; term-evidence is applied to
the designated post-pivot ("affirmative") clause only, exactly as before.

The #236 employer-scoped guard, however, is TWO separate checks (see above):
A.1 (tech×employer term evidence) is exactly as order-blind-*incapable* as
the term-evidence check for the same reason. But A.2 (free-text content
coverage against the named employer's own bullets — fabricated-*context*
detection) never asks whether a term is claimed or denied; once the named
employer and the cluster/JD terms themselves are stripped out of a clause,
what's left is either nothing (a bare "I haven't used X at Employer" denial
has no narrative content beyond term/employer/scaffolding, and clears A.2
trivially) or genuine fabricated narrative that does or doesn't belong to
the named employer's own bullets — a fact, not a judgement about which
clause is honest. ``_passes_content_coverage_only`` (A.2 without A.1) now
runs on WHICHEVER clause the pivot split put before it, whenever that clause
names a known employer — so a fabricated, employer-misattributed clause is
caught whether the model wrote the denial first (already covered by the
affirmative-clause pipeline, which still runs the FULL A.1+A.2 guard there)
or the bridging/affirmative clause first (previously invisible — see
``filter_ungrounded_choices``). A pure denial with no pivot still runs the
FULL employer-scoped guard (A.1+A.2) over the WHOLE chip when it names a
known employer, unchanged from before F2 — see docstring on
``filter_ungrounded_choices`` for the residual, by-design gap this narrower,
no-pivot case leaves (a mislabelled denial naming NO employer and asserting
only the same, entirely unevidenced concept it claims to deny — not
fact-distinguishable from a genuine denial by any deterministic signal;
that is the judgement ADR-062 assigns to the model via the tag, not to this
module).
"""

import logging
import re
from typing import Any

from applire.services.ats_audit import _norm, skill_tokens, surface_present

logger = logging.getLogger(__name__)

# The three levels the generation prompt (ADR-064 + this fix) asks every
# drafted choice to declare. A FACT read off the model's own output, not a
# judgement this module makes — see the module docstring.
_VALID_LEVELS = frozenset({"direct", "partial", "denial"})


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


def _level_of(choice: Any) -> tuple[str, str | None]:
    """Read a choice's text and declared level (FACT, per ADR-062 clause 6).

    A choice is either the new shape — ``{"text": str, "level": str}`` — or a
    bare string for backward compatibility (a caller that hasn't adopted
    levels yet, or a model that ignored the schema). Returns
    ``(text, level)`` where ``level`` is one of ``_VALID_LEVELS`` or ``None``.
    ``None`` covers every defensive case at once: no "level" key, an empty/
    null value, or a value that doesn't match one of the three known levels
    (typo, translated word, hallucinated category) — all fall back to the
    SAME safe default, the pre-existing full grounding check, identical to
    behaviour before levels existed. This function never inspects the
    choice's own wording to guess anything; it only reads a field.
    """
    if isinstance(choice, dict):
        text = str(choice.get("text") or "").strip()
        level_raw = choice.get("level")
        level = str(level_raw).strip().lower() if level_raw else None
        return text, (level if level in _VALID_LEVELS else None)
    return str(choice).strip(), None


# ── denial-clause scoping — denial→affirmation pivot ─────────────────────────
# Deterministic pivot phrases (EN + DE) that separate a denial clause from an
# affirmative remainder in the "I haven't worked with X directly, but ..."
# shape a denial-level choice may take. Matched case-insensitively against a
# length-preserving fold (quotes only — NOT casefold(), which can change
# string length for e.g. German "ß" and would desync the position map back
# onto the original text).
_PIVOT_MARKERS: tuple[str, ...] = (
    ", but ", "; but ", ". but ",
    ", though ", ", however ",
    " — but ",
    # German
    ", aber ", ". aber ",
)


def _fold_for_split(text: str) -> str:
    """Length-preserving lower+quote-fold used only to locate pivot byte
    offsets that must map 1:1 back onto the original string."""
    return _fold_quotes(text).lower()


def _split_denial_choice(text: str) -> tuple[str, str | None]:
    """Split a "denial"-level choice at its denial→affirmation pivot.

    Returns ``(text, None)`` for a pure denial with no pivot — nothing to
    split, the whole chip is the denial. Otherwise returns
    ``(denial, affirmative)`` split at the FIRST pivot phrase in the chip.
    The denial half is never checked for term evidence (naming the denied
    term is the point); the affirmative half goes through the normal
    grounding pipeline as its own chip. Only called for choices the model
    has already tagged "denial" — this function locates a clause boundary,
    it does not decide whether the chip is a denial (see ``_level_of``).
    """
    folded = _fold_for_split(text)

    pivot_start, pivot_end = -1, -1
    for pivot in _PIVOT_MARKERS:
        idx = folded.find(pivot)
        if idx != -1 and (pivot_start == -1 or idx < pivot_start):
            pivot_start, pivot_end = idx, idx + len(pivot)

    if pivot_start == -1:
        return text, None  # pure denial — no affirmative clause to check

    return text[:pivot_start], text[pivot_end:]


# ── #236 — employer-scoped attribution guard ─────────────────────────────────
# _norm's NFKC pass already folds NBSP to a plain space, but typographic quotes
# are a distinct codepoint (not a compatibility decomposition) — fold them
# explicitly before matching, mirroring _fold_for_split's apostrophe fold.
_QUOTE_FOLD = str.maketrans({
    "’": "'", "‘": "'", "ʼ": "'",
    "“": '"', "”": '"',
    " ": " ",
})


def _fold_quotes(text: str) -> str:
    return text.translate(_QUOTE_FOLD)


# Corporate suffixes stripped from a profile company name before it is
# searched for in a chip — "NordPharm SE" must be found by a chip that only
# says "NordPharm" (founder-acceptance F5, issue #236).
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
    "have", "has", "had", "having", "i've", "we've", "they've", "you've",
    "part", "role", "solution", "solutions", "using", "use", "used", "over",
    "an", "of", "for", "with", "the", "a", "to", "in", "and", "or", "&",
    # German
    "ich", "habe", "mein", "meine", "meinem", "meiner", "unser", "bei", "und",
    "im", "in", "der", "die", "das", "den", "dem", "eine", "einen", "einer",
    "fur", "für", "unter", "ein", "mit", "wie", "zur", "zum", "von", "auf",
    # Negation particles (F2, 2026-07-29): scaffolding the same way — a bare
    # "I haven't used X at Employer" denial clause has NOTHING left after
    # employer + cluster-term + scaffolding stripping, and must clear
    # ``_passes_content_coverage_only`` trivially rather than fail on the
    # negation word itself. Function words, not skill/framework names.
    "not", "no", "never", "n't",
    "haven't", "hasn't", "hadn't", "isn't", "wasn't", "weren't", "aren't",
    "don't", "doesn't", "didn't", "won't", "wouldn't", "can't", "cannot",
    "couldn't", "shouldn't", "shan't",
    "nicht", "kein", "keine", "keinen", "keiner", "keinem", "keines",
    "nie", "niemals", "nichts",
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
# of its tokens are real NordPharm vocabulary lifted from an unrelated bullet,
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


def _passes_content_coverage_only(
    clause_norm: str, terms: list[str], matched_entries: list[dict], profile: dict,
) -> bool:
    """F2 (2026-07-29): the order-blind half of the #236 guard — A.2 (free-
    text content coverage) WITHOUT A.1 (tech×employer term evidence).

    A.1 is polarity-sensitive: it only checks whether a cluster/JD term the
    clause *names* is evidenced at the named employer, and "I worked with X
    at Employer" surface-matches "X" identically to "I haven't worked with X
    at Employer" — there is no deterministic way to tell a legitimate denial
    clause from a fabricated claim by that check alone (this is exactly the
    ordering ambiguity ``filter_ungrounded_choices``' docstring already
    disclaims for the whole-choice term-evidence exemption). So this
    function never runs A.1.

    A.2 has no such problem: once the named employer AND the cluster/JD
    terms themselves are stripped out of the clause, what's left is either
    nothing (a bare denial — "I haven't used X at Employer" — or a bare
    claim naming only the term and employer) or genuine extra narrative
    content, and that content either belongs to the named employer's own
    bullets or it doesn't, regardless of whether the clause was asserting or
    denying the term. Used to close the F2 ordering hole: applied to
    WHICHEVER clause a denial-tagged choice's pivot split put the pre-pivot
    half in, so a fabricated, employer-misattributed clause is caught
    whether the model wrote the denial first (already covered by the
    affirmative-clause pipeline) or the bridging/affirmative clause first
    (previously invisible — see ``filter_ungrounded_choices``).
    """
    scoped_evidence = _employer_scoped_evidence(matched_entries, profile)
    strip_bases = {_employer_base(str(e.get("company") or "")) for e in matched_entries}
    stripped = clause_norm
    for term in terms:
        term_norm = _norm(term)
        if term_norm:
            stripped = re.sub(r"\b" + re.escape(term_norm) + r"\b", " ", stripped)
    content = _content_tokens(stripped, strip_bases)
    if not content:
        return True  # nothing left to falsify beyond the term/employer/scaffolding
    corpus_tokens = frozenset(scoped_evidence.split())
    hits = sum(1 for t in content if _fuzzy_present(t, scoped_evidence, corpus_tokens))
    return (hits / len(content)) >= _CONTENT_COVERAGE_MIN


def filter_ungrounded_choices(
    choices: "list[str | dict[str, Any]] | None",
    cluster: dict[str, Any],
    profile: dict[str, Any],
    gap_category: str | None,  # noqa: ARG001 — one rule for all categories; kept for call-site clarity
) -> list[str] | None:
    """Drop chips that assert experience the profile doesn't evidence.

    Keep a chip when every cluster/JD term it mentions is evidenced in the
    profile. Returns ``None`` when nothing survives (the UI then shows the
    plain answer box — no scaffold beats a fabricated one). Each item in
    ``choices`` may be the new ``{"text": str, "level": str}`` shape or a
    bare string; the return value is always ``list[str]`` regardless of
    input shape — the API contract this feeds (``choices: list[str] | None``
    in ``schemas/session.py``) does not change.

    #236: when a chip names a known employer, the check is scoped to THAT
    employer's own evidence (tech×employer attribution) and its remaining
    free text must clear a content-coverage bar against that employer's own
    bullets (fabricated-context detection) — see ``_passes_employer_scoped_guard``.
    Chips naming no employer keep the original whole-profile cluster-term check.

    Level handling (this fix, 2026-07-29 — see module docstring for the
    ADR-062 rationale): the model tags every choice with its own level.
    - "direct" / "partial" / unrecognised or missing level: full existing
      pipeline, unchanged — every cluster/JD term the chip asserts must be
      evidenced.
    - "denial": exempt from the term-evidence requirement — a denial names
      the term to deny it, there is nothing to ground. Split at its FIRST
      denial→affirmation pivot (``_split_denial_choice``, preserved from the
      2026-07-23 clause-scoping fix): the remainder AFTER that pivot (if any)
      still runs the FULL pipeline (term-evidence + the #236 employer-scoped
      guard), so a bridging claim like "... but I've worked with React and
      Next.js at StartupXYZ" is checked — PROVIDED the model wrote the
      denial clause first. ``_split_denial_choice`` cannot tell which side
      of the pivot is the denial and which is the affirmation; it assumes
      the first clause always is, and for TERM-EVIDENCE that assumption is
      unavoidable (see the module docstring's "F2" note — a deterministic
      rule cannot tell "the clause naming the term IS the legitimate denial"
      from "... is a fabricated claim smuggled past the guard", and checking
      both clauses' terms would drop the single most common LEGITIMATE
      denial shape at least as often as it would catch a fabrication). **F2
      (2026-07-29, fixed) for the #236 employer-scoped guard specifically:**
      its content-coverage half (A.2 — free-text narrative fit against the
      named employer's own bullets, order-blind by construction) now ALSO
      runs on the pre-pivot clause, whichever clause that is, whenever it
      names a known employer (``_passes_content_coverage_only``) — so the
      identical StartupXYZ fabrication, reordered to put the bridging claim
      BEFORE the pivot instead of after, is caught too. A pure denial with
      no pivot still runs the FULL employer-scoped guard (A.1+A.2), over the
      WHOLE chip, when it names a known employer — a mislabelled "denial"
      that borrows a real employer's name for a fabricated narrative is
      caught the same way a "direct"-tagged chip would be, regardless of
      ordering (there is no clause split to be order-dependent about). A
      pure denial naming NO employer and asserting only the same, entirely
      unevidenced concept it claims to deny is trusted: no deterministic,
      fact-only signal can tell "I have no experience with X" apart from a
      claim about X when the profile has zero evidence for X either way —
      that is the judgement ADR-062 assigns to the model via the tag, not to
      this module. A mislabelling of that shape is logged at WARNING when
      caught by the employer guard; the untraceable case is a documented
      residual gap.
    """
    if not choices:
        return None

    evidence = _evidence_norm(profile)
    terms = _cluster_terms(cluster)
    work_experience = profile.get("work_experience") or []

    def _run_pipeline(candidate_text: str, *, require_asserted_for_content_check: bool = False) -> bool:
        """The pre-existing full grounding pipeline (unchanged): employer-scoped
        guard when a known employer is named, else the whole-profile cluster-
        term evidence check. Returns True when the text may be kept."""
        candidate_norm = _norm(_fold_quotes(candidate_text))
        matched_entries = _match_employers(candidate_norm, work_experience)
        if matched_entries:
            return _passes_employer_scoped_guard(
                candidate_norm, terms, matched_entries, profile,
                require_asserted_for_content_check=require_asserted_for_content_check,
            )
        asserted = [t for t in terms if surface_present(t, candidate_norm)]
        return all(surface_present(t, evidence) for t in asserted)

    kept: list[str] = []
    for choice in choices:
        text, level = _level_of(choice)
        if not text:
            continue

        if level != "denial":
            # "direct" / "partial" / unknown — full pipeline, unchanged.
            if _run_pipeline(text):
                kept.append(text)
            continue

        denial_clause, affirmative = _split_denial_choice(text)
        if affirmative is not None:
            # Bridging denial: the affirmative remainder is a real assertion
            # and runs the full pipeline exactly like a "partial" choice.
            # Term-evidence exemption stays scoped to the designated
            # affirmative (post-pivot) half only — a deterministic rule
            # cannot tell which clause is the legitimate denial for THAT
            # check (F2 module docstring: TERM-evidence is polarity-
            # sensitive, "X at Employer" and "not X at Employer" surface-
            # match the term identically).
            #
            # F2 (2026-07-29): the #236 fabricated-CONTEXT check (A.2 of
            # ``_passes_employer_scoped_guard`` — free-text content coverage
            # against a NAMED employer's own bullets, once the employer name
            # and the cluster/JD terms themselves are stripped out) has no
            # such polarity problem: it never asks whether a term is
            # claimed or denied, only whether whatever narrative content
            # remains actually belongs to the employer named. Run THAT half
            # of the guard on the pre-pivot clause too, whichever clause it
            # is — a genuine denial clause ("I haven't used X at Employer")
            # has nothing left after that stripping and passes trivially; a
            # fabricated clause smuggling unrelated narrative in ahead of
            # the pivot (the reordered #236 shape) does not.
            ok = _run_pipeline(affirmative, require_asserted_for_content_check=True)
            if ok:
                denial_norm = _norm(_fold_quotes(denial_clause))
                denial_matched = _match_employers(denial_norm, work_experience)
                if denial_matched and not _passes_content_coverage_only(
                    denial_norm, terms, denial_matched, profile,
                ):
                    ok = False
            if ok:
                kept.append(text)
            else:
                logger.warning(
                    "choice_grounding: dropped a 'denial'-tagged choice whose "
                    "pre-pivot or affirmative clause failed the grounding "
                    "pipeline (mislabelled by the model): %r",
                    text,
                )
            continue

        # Pure denial, no pivot — exempt from term-evidence, but a
        # mislabelled overclaim naming a real employer is still caught.
        choice_norm = _norm(_fold_quotes(text))
        matched_entries = _match_employers(choice_norm, work_experience)
        if matched_entries:
            if _passes_employer_scoped_guard(choice_norm, terms, matched_entries, profile):
                kept.append(text)
            else:
                logger.warning(
                    "choice_grounding: dropped a 'denial'-tagged choice that "
                    "failed the employer-scoped guard (mislabelled by the "
                    "model): %r",
                    text,
                )
            continue
        kept.append(text)  # trust the tag — nothing left to ground
    return kept or None
