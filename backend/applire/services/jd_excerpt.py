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

"""#271 Task 1 — a de-chromed, content-bearing JD excerpt.

Ground truth (charter run #5, ``.run5fixture/jd.txt``, a 10119-char LinkedIn
scrape): the writer prompt (``prompts.cover_letter.build_cover_letter_prompt``)
and the reviewer's ``grounding_source["job_description"]``
(``services.cover_letter``) each independently sliced ``raw_text[:2000]``. The
run-5 JD's first ~1450 characters are LinkedIn sign-in boilerplate repeated
verbatim three times ("Email or phone / Password / Show / Forgot password? /
Sign in / New to LinkedIn? / Join now / By clicking Continue to join or sign
in, ..."), so the 2000-char window landed on chrome and NEVER reached the
leadership-weighting sentence ("approximately 60% technical leadership and
40% hands-on engineering", real offset 2281), the people-management bullet
("Managing, mentoring and developing a growing AI engineering team", offset
2483), the RAG/agentic bullet (offset 2665), or any line of the "What We're
Looking For" requirements list (offset 3142+). The JD-analysis chain is
unaffected (it reads the full ``raw_text``) — this is purely a writer/reviewer
excerpting bug.

This module is a single deterministic, no-LLM transform:
  1. Collapse whitespace (newlines/repeated spaces -> single space).
  2. Collapse an immediate repeated phrase (a common scrape artifact — the
     page title duplicated as "<Title> Apply <Title> <n> ago") — a bounded,
     word-boundary regex that folds a phrase of >=15 chars repeating with a
     gap of at most 2 filler words down to a single occurrence.
  3. Split into sentence/bullet-granularity segments (on `.`/`!`/`?` and the
     `•` bullet marker the LinkedIn scrape uses for list items) and drop any
     segment that is an EXACT duplicate (case-insensitive) of an earlier one,
     keeping the first occurrence — this is what removes the 3x-repeated
     sign-in block, since each of its constituent sentences repeats verbatim.
  4. Truncate the deduplicated text to ``budget`` characters.

Budget = 4000 (``JD_EXCERPT_BUDGET``). Chosen empirically against the pinned
run-5 fixture: after steps 1-3 the sign-in boilerplate collapses from ~1450
chars to ~500, and the real JD content the writer needs — from the "LegalTech
company" opener through the end of the "What We're Looking For" list — spans
roughly the next ~2300 characters (ending ~3550 in the deduplicated text, see
the test below). 4000 leaves a safety margin beyond that without indulging
E037 PQ #1's warning against a JD-dominant prompt: a JD-dominated prompt made
the model source "achievements" from the employer's requirement language
(fabrication), which is why the OLD budget was deliberately cut to 2000 in
the first place, against a CANDIDATE PROFILE section that was thin at the
time. For the SAME run-5 profile, the CANDIDATE PROFILE block that
``build_cover_letter_prompt`` renders today (work_history[:6] x bullets[:6] +
20 skills + summary) is ~3570 characters — so a 4000-char JD excerpt lands
close to a 1:1 JD:profile ratio (never far JD-dominant), a large rebalance
from the original whole-text ratio of ~10119:3570 (~2.8:1) this fix replaces.
2000 was tried first and rejected: it is simply too small to hold the run-5
JD's real content once the boilerplate blocking it is removed, because the
"What We're Looking For" list is itself substantial, and it must be present
in full — the E048/US264 company-domain-engagement instruction and the
letter's own honest-gap positioning both depend on the writer seeing the
COMPLETE requirement set, not a truncated one.
"""
from __future__ import annotations

import re

# Empirically sized against the pinned run-5 fixture — see the module
# docstring for the arithmetic. Not a magic number: it is the smallest round
# figure that clears the real JD content's end offset (~3550 chars,
# post-dedup) with headroom, while staying close to (not far above) a 1:1
# ratio against the CANDIDATE PROFILE block's typical size.
JD_EXCERPT_BUDGET = 4000

_WHITESPACE_RE = re.compile(r"\s+")

# A common scrape artifact: the page's own title/header duplicated with a
# short filler in between ("Lead AI Engineer Connect-AI Germany Apply Lead AI
# Engineer Connect-AI Germany 1 week ago ..."). Bounded: the repeated phrase
# must be at least 15 characters (long enough to be a real title fragment,
# short enough that a coincidental short repeat in prose is never folded),
# and the gap between the two occurrences is at most 2 filler words — a
# repeat further apart than that is far more likely to be two independent
# mentions of the same term (e.g. "RAG" appearing twice in unrelated
# sentences) than a single duplicated header, so it is deliberately left
# alone (never collapsed by this pass; segment-level dedup below still
# catches it if the two mentions sit in otherwise-identical sentences).
_REPEATED_PHRASE_RE = re.compile(
    r"(\b[\w][\w\s,\-]{14,80}\b)(?:\s+\S+){0,2}\s+\1\b", re.IGNORECASE,
)

# Sentence/bullet segmentation: split on the usual sentence terminators AND
# the "•" bullet marker LinkedIn (and most job boards) use for list items —
# a bare period-only split would keep an entire bulleted list as one
# "sentence" and could never recognise two individually-repeated bullets.
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\s*•\s*")


def _collapse_repeated_phrases(text: str, max_passes: int = 5) -> str:
    """Fold an immediate repeated phrase down to one occurrence.

    Iterates (bounded by ``max_passes``) because folding one repeat can
    reveal another immediately adjacent to it; converges quickly in
    practice (real scrape artifacts are 1-2 repeats deep) and the bound
    guarantees this can never loop.
    """
    out = text
    for _ in range(max_passes):
        new = _REPEATED_PHRASE_RE.sub(lambda m: m.group(1), out)
        if new == out:
            break
        out = new
    return out


def _dedupe_segments(text: str) -> str:
    """Drop exact-duplicate (case-insensitive) segments, keeping the first.

    Segments are sentence/bullet granularity (see ``_SEGMENT_SPLIT_RE``) —
    fine enough that the LinkedIn sign-in block's constituent sentences
    ("Sign in Sign in with Email or New to LinkedIn?", "By clicking
    Continue to join or sign in, you agree to LinkedIn's User Agreement,
    Privacy Policy, and Cookie Policy.") are recognised as verbatim repeats
    even though the sentences immediately before them differ slightly
    ("Tailor my resume Sign in to access..." vs "Sign in to evaluate your
    skills..." vs "Sign in to tailor your resume...").
    """
    segments = [s.strip() for s in _SEGMENT_SPLIT_RE.split(text) if s.strip()]
    seen: set[str] = set()
    kept: list[str] = []
    for seg in segments:
        key = seg.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(seg)
    return " ".join(kept)


def build_jd_excerpt(raw_text: str | None, budget: int = JD_EXCERPT_BUDGET) -> str:
    """The SAME de-chromed, bounded JD excerpt for BOTH the writer prompt
    (``prompts.cover_letter.build_cover_letter_prompt``) and the reviewer's
    ``grounding_source["job_description"]``
    (``services.cover_letter._render_cover_letter_background``) — #271.

    Deterministic, no LLM: collapse whitespace, fold an immediate repeated
    phrase (the scraped-title artifact), drop exact-duplicate sentence/bullet
    segments keeping the first occurrence (the repeated sign-in block), then
    truncate to ``budget`` characters (default :data:`JD_EXCERPT_BUDGET`).

    Both call sites MUST call this function (never re-slice ``raw_text``
    independently) so the writer and the reviewer can never disagree about
    what the JD says — a writer/reviewer JD mismatch would make the run-5
    reviewer's "flagged a grounded employer fact as invented" failure mode
    worse, not better.

    ``None``/empty-tolerant: returns ``""`` rather than raising.
    """
    if not raw_text:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", raw_text).strip()
    collapsed = _collapse_repeated_phrases(collapsed)
    deduped = _dedupe_segments(collapsed)
    return deduped[:budget]
