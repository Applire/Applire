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

"""Deterministic, no-LLM positioning inputs for the cover-letter prompt (E048/US264,
ADR-057 amended 2026-07-24).

A blind hiring panel rejected an otherwise-honest application because the letter
never engaged the employer concretely, never argued the candidate's own transfer
story for the one true gap in the JD (even though the argument sat in the vault as
interview testimony), and never addressed an obvious concurrent-roles/availability
question. ADR-058 exception (a) scopes the fix to PROMPT-INPUT THREADING only —
no new LLM chain, no new pass. This module supplies the deterministic (keyword/
count-based) inputs the cover-letter prompt builder threads in; it invents nothing
and makes no LLM call itself. Mirrors the no-LLM style of
:mod:`applire.services.cv_gap_mapper`.
"""

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\b[a-zA-ZÀ-ÿ0-9.#+\-]{2,}\b")

# #272 Task 1 (RC-C): PHRASE-scoped availability/concurrent-commitment matching —
# never a bare token. RC-C ground truth: the bare token "parallel" matched an
# enrichment record whose value was the TITLE of an unrelated neuroscience paper
# ("Parallel Processing via a Dual Olfactory Pathway in the Honeybee"), and that
# string was threaded into the writer prompt as the candidate's own availability
# testimony. A false negative here (no availability claim made) is SAFE; a false
# positive puts invented/misattributed content in a signed letter, so every entry
# below is a phrase (or an unambiguous, non-generic compound term) that actually
# denotes availability/notice/concurrent-commitment — never a standalone common
# word like "parallel", "concurrent", or "commitment" that also occurs constantly
# in unrelated prose (paper titles, technical descriptions, generic mission
# statements, …).
_AVAILABILITY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bnotice\s*periods?\b", re.IGNORECASE),
    re.compile(r"\bk[üu]ndigungsfrist\w*\b", re.IGNORECASE),
    re.compile(r"\beintrittstermine?\b", re.IGNORECASE),
    re.compile(r"\bavailable\s+from\b", re.IGNORECASE),
    re.compile(r"\bavailability\b", re.IGNORECASE),
    re.compile(r"\bverf[üu]gbar\w*\b", re.IGNORECASE),
    re.compile(r"\bin\s+parallel\s+to\s+(?:my|the)\s+current\b", re.IGNORECASE),
    re.compile(r"\bconcurrent\s+(?:role|roles|position|positions|commitments?)\b", re.IGNORECASE),
    re.compile(r"\balongside\s+my\b", re.IGNORECASE),
    re.compile(r"\b(?:other|existing)\s+commitments?\b", re.IGNORECASE),
    re.compile(r"\bnebenbei\b", re.IGNORECASE),
    re.compile(r"\bnebenjob\b", re.IGNORECASE),
    re.compile(r"\bmoonlight(?:ing)?\b", re.IGNORECASE),
]


def _tokenise(text: str) -> set[str]:
    """Lowercase word tokens, 2+ chars — mirrors cv_gap_mapper._tokenise."""
    return {w.lower() for w in _TOKEN_RE.findall(text or "")}


# #272 Task 1: split on sentence-ending punctuation (incl. the ellipsis "…" the
# real run-5 denial statement uses to separate its RAG-scope preamble from its
# availability tail) so a phrase match can be threaded as ONLY its own sentence
# — never the whole (possibly unrelated) surrounding paragraph/statement.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def _first_availability_sentence(text: str) -> str | None:
    """Return the first sentence in ``text`` matching an availability phrase, or
    None. Phrase-scoped (see ``_AVAILABILITY_PATTERNS``) and sentence-scoped: a
    match inside a longer statement threads only the matching sentence."""
    for sentence in _split_sentences(text):
        if any(pattern.search(sentence) for pattern in _AVAILABILITY_PATTERNS):
            return sentence
    return None


def _story_text(story: dict) -> str:
    """Concatenate a signature story's own prose fields (never the JD/gap label —
    only the candidate's own testimony can ground a claim)."""
    parts = [
        story.get("title") or "",
        story.get("challenge") or "",
        story.get("mechanism") or "",
        story.get("outcome") or "",
        story.get("benchmark") or "",
    ]
    return " ".join(p for p in parts if p)


def detect_concurrent_roles(work_experience: list[dict]) -> bool:
    """True iff >=2 entries have an OPEN end date (a current/ongoing role).

    An entry counts as open-ended when ``is_current`` is explicitly True, or
    when ``is_current`` is unset (None) AND ``end_date`` is empty/absent — the
    tri-state convention #155 already uses elsewhere (``is_current`` False
    always means "known ended", regardless of a blank end_date).
    """
    open_count = 0
    for entry in work_experience or []:
        if not isinstance(entry, dict):
            continue
        is_current = entry.get("is_current")
        end_date = entry.get("end_date")
        if is_current is True or (is_current is None and not end_date):
            open_count += 1
    return open_count >= 2


def find_gap_testimony(
    category_c_gaps: list[str], signature_stories: list[dict]
) -> dict | None:
    """Return the first category-C gap with a matching signature story, or None.

    Deterministic keyword-overlap match (mirrors
    :func:`applire.services.cv_gap_mapper.map_gaps_to_sections`): for each gap
    label IN ORDER (category_c is already severity-ordered by the gap analysis),
    score every story by token overlap between the gap label and the story's OWN
    prose; the first gap with a positive-scoring story wins (deterministic
    first-match, same tie-break philosophy as the CV gap mapper). Returns
    ``{"gap": <gap label>, "story": <story dict>}`` — the caller threads the
    story's own text VERBATIM into the prompt; nothing is invented here.
    """
    stories = [s for s in (signature_stories or []) if isinstance(s, dict)]
    if not stories:
        return None
    for gap in category_c_gaps or []:
        gap_tokens = _tokenise(gap)
        if not gap_tokens:
            continue
        best_story: dict | None = None
        best_score = 0
        for story in stories:
            score = len(gap_tokens & _tokenise(_story_text(story)))
            if score > best_score:
                best_score = score
                best_story = story
        if best_story is not None:
            return {"gap": gap, "story": best_story}
    return None


def find_availability_testimony(
    signature_stories: list[dict],
    enrichment_history: list[dict],
    denied_concepts: list[dict] | None = None,
) -> str | None:
    """Search the vault for the candidate's OWN testimony about availability /
    concurrent commitments. Returns the matched SENTENCE verbatim, or None.

    Three sources, all deterministic PHRASE matches against
    ``_AVAILABILITY_PATTERNS`` (#272 Task 1 — bare-token matching was the RC-C
    defect: a bare "parallel" token matched an unrelated paper title) — never a
    guess:
    1. Signature stories (ADR-055) whose own prose mentions one of the phrases.
    2. Enrichment-history field changes (interview/agent_interview turns) whose
       rationale or recorded value mentions one of the phrases.
    3. ``denied_concepts[].statement`` (#231) — added by #272: the run-5 ground
       truth showed the candidate's real availability testimony living as the
       TAIL of a denial statement, a source this function never searched before.

    First match wins (stories checked first — they are the richer, full-prose
    unit; denied_concepts checked last). When a phrase matches inside a longer
    statement, ONLY the matching SENTENCE is returned — never the whole
    paragraph/statement, which may carry unrelated content (the real run-5
    statement is mostly about RAG scope). Returns None (no claim made) when
    nothing matches.
    """
    for story in signature_stories or []:
        if not isinstance(story, dict):
            continue
        sentence = _first_availability_sentence(_story_text(story))
        if sentence:
            return sentence

    for record in enrichment_history or []:
        if not isinstance(record, dict):
            continue
        for change in record.get("changes") or []:
            if not isinstance(change, dict):
                continue
            rationale = change.get("rationale") or ""
            new_value = change.get("new_value")
            value_text = new_value if isinstance(new_value, str) else ""
            text = f"{rationale} {value_text}".strip()
            sentence = _first_availability_sentence(text)
            if sentence:
                return sentence

    for concept in denied_concepts or []:
        if not isinstance(concept, dict):
            continue
        sentence = _first_availability_sentence(concept.get("statement") or "")
        if sentence:
            return sentence

    return None


# ---------------------------------------------------------------------------
# #272 Task 3 — structural retention predicate (wired into reviewer.py's
# opt-in retain_if; see services/reviewer.py and services/cover_letter.py)
# ---------------------------------------------------------------------------

# Structural-only floor (a word count, never a quality judgment or anything
# resembling a critic — ADR-060 stays out of scope). RC-D ground truth: the
# run-5 corrector's round-5 rewrite deleted the writer's real closing paragraph
# ("I would welcome the opportunity to discuss how my experience aligns with
# your needs. My notice period can be discussed.") and left the bare stub
# "Notice period can be discussed." (5 words) as the entire final paragraph. A
# genuine closing (interest + call to action, optionally folding in
# availability) reliably runs well past this floor.
_MIN_CLOSING_PARAGRAPH_WORDS = 10


def has_closing_paragraph(letter_data: dict | None) -> bool:
    """True iff the letter's LAST body paragraph reads as a genuine closing
    rather than a bare availability stub.

    Pure structural check (word count of the last paragraph) — never a quality
    score. Used as the ``retain_if`` predicate for the cover-letter
    ``review_and_refine`` chain (#272 Task 3): when the settled draft fails
    this check but an earlier round's draft passed it, the earlier draft ships
    instead.
    """
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") or []
    if not paragraphs:
        return False
    last = paragraphs[-1] or ""
    return len(last.split()) >= _MIN_CLOSING_PARAGRAPH_WORDS


# ---------------------------------------------------------------------------
# #272 Task 6 — deterministic word-floor reviewer wrapper (ADR-051 norm
# registry; no hard-coded number here — the caller passes
# REGION_NORMS[region].letter_body_word_floor)
# ---------------------------------------------------------------------------


def body_word_count(letter_data: dict | None) -> int:
    """The letter body's word count — THE single counter every word-budget check
    (floor and ceiling alike) must go through, so they can never disagree about
    what "the body's word count" means (wave-6 follow-up, charter run #6)."""
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") or []
    return sum(len((p or "").split()) for p in paragraphs)


def _render_word_floor_block(word_count: int, word_floor: int) -> str:
    return (
        "WORD FLOOR CHECK (deterministic, ADR-051 norm registry — never a "
        "hard-coded number in this prompt):\n"
        f"The current draft body is {word_count} words, under this letter's "
        f"{word_floor}-word floor. This is a real issue ONLY when genuinely "
        "selectable, grounded evidence was left out of the letter. Flag it and "
        "name the issue 'insufficient selected evidence', instructing the "
        "writer to add MORE content already grounded in the CANDIDATE SOURCE. "
        "NEVER instruct padding with generic filler and NEVER invent content "
        "to reach the floor — a thin, honest letter is preferable to a padded "
        "or fabricated one."
    )


# ---------------------------------------------------------------------------
# Wave-6 follow-up (charter run #6, Task 2) — structural word-budget predicate,
# meant to compose with has_closing_paragraph as review_and_refine's OPTIONAL
# ``prefer_if`` secondary preference. Reuses body_word_count (the SAME counter
# the word-floor block above is built from) so the floor and the ceiling can
# never disagree about what "the body's word count" means.
# ---------------------------------------------------------------------------


def within_word_budget(letter_data: dict | None, word_budget: int) -> bool:
    """True iff the letter's body word count is at or under ``word_budget``
    (REGION_NORMS[region].letter_body_word_budget). Pure structural check —
    never a quality score — used as review_and_refine's ``prefer_if`` for the
    cover-letter chains, alongside ``retain_if=has_closing_paragraph``: prefer a
    draft that has BOTH the closing AND fits the norm; retain_if alone still
    decides which drafts are eligible at all (the closing is never sacrificed
    to satisfy this predicate — see review_and_refine's ``prefer_if`` contract).
    """
    return body_word_count(letter_data) <= word_budget


def word_floor_reviewer_prompt_fn(base_fn, word_floor: int):
    """Wrap a ``reviewer_prompt_fn`` so every ADR-021 review iteration carries a
    deterministic WORD FLOOR CHECK against the CURRENT draft (#272 Task 6).

    ADR-051 sets an upper bound only (``letter_body_word_budget``), so a thin
    letter previously passed silently. This composes with (does not replace)
    the existing wrapper chain (``coverage_reviewer_prompt_fn``,
    ``unaddressed_requirements_reviewer_prompt_fn``) the same way they compose with each
    other: ``review_and_refine`` calls ``reviewer_prompt_fn(source, draft)``
    fresh each iteration, so the check is recomputed against the LATEST draft
    — deterministic, no new LLM call, no new loop (ADR-058 freeze). The
    instruction is never to pad or invent — only to surface real, already-
    grounded content, or to name the honest shortfall.
    """

    def fn(source: str, draft: dict) -> str:
        prompt = base_fn(source, draft)
        count = body_word_count(draft)
        if count < word_floor:
            prompt = f"{prompt}\n\n{_render_word_floor_block(count, word_floor)}"
        return prompt

    return fn


# ---------------------------------------------------------------------------
# #321 — the candidate's OWN recorded job titles, as facts (ADR-062 clause 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleTitle:
    """One position the vault records, and the title(s) it is recorded under.

    ADR-062 classification: **FACT**. "Which title does the vault carry for this
    position" is settled by the profile's own structure — a field read, no prose
    is interpreted. The judgement this deliberately does NOT make is the one
    #321 turns on: *whether a given letter sentence states a title at all*.
    German prose can name a role without asserting it as a title ("ich
    verantworte den ISO-9001-Bereich"), and separating the two requires reading
    what a sentence means. That belongs to the reviewer (clause 2), which is why
    there is no title-marker list anywhere in this module.
    """

    title: str  # the vault's own string, verbatim
    aliases: tuple[str, ...]  # WorkEntry.role_aliases — equally legitimate titles
    org: str  # employer / organisation / project the title is recorded under
    span: str  # "2017-04 – present"; "" when the vault records no dates


def _title_span(entry: dict) -> str:
    """"2017-04 – present" / "2011-08 – 2017-03" / "" — the position's own dates.

    ``is_current`` is tri-state (#155): ``False`` means *known ended*, so an
    entry that carries it must never be rendered as ongoing merely because its
    end date is missing.
    """
    start = (entry.get("start_date") or "").strip()
    end = (entry.get("end_date") or "").strip()
    current = entry.get("is_current")
    if not end and current is not False and (current or (start and entry.get("end_date") is None)):
        end = "present"
    return " – ".join(part for part in (start, end) if part)


def vault_role_titles(profile_json: dict | None) -> list[RoleTitle]:
    """Every title the vault records for the candidate, in vault order.

    Work experience first, then volunteer activities, then projects — each
    labelled with the organisation a reader would recognise (the same
    ``org_label()`` split the profile schema makes: company / organization /
    project name). A position with no recorded title contributes nothing:
    there is no fact to state about it, and inventing one is the defect.

    ``role_aliases`` are carried because the vault's own schema defines them as
    *"all role titles ever used for this position"* — omitting them would let
    the reviewer flag a letter that used one of the candidate's own legitimate
    titles.

    ADR-062 classification: FACT (see :class:`RoleTitle`). Pure read, no LLM,
    no I/O.
    """
    if not isinstance(profile_json, dict):
        return []
    titles: list[RoleTitle] = []
    for section, org_field in (
        ("work_experience", "company"),
        ("volunteer_activities", "organization"),
        ("projects", "name"),
    ):
        for entry in profile_json.get(section) or []:
            if not isinstance(entry, dict):
                continue
            title = (entry.get("role") or "").strip()
            if not title:
                continue
            aliases = tuple(
                a.strip()
                for a in (entry.get("role_aliases") or [])
                if isinstance(a, str) and a.strip() and a.strip() != title
            )
            titles.append(
                RoleTitle(
                    title=title,
                    aliases=aliases,
                    org=(entry.get(org_field) or "").strip(),
                    span=_title_span(entry),
                )
            )
    return titles


def render_role_titles_block(titles: list[RoleTitle]) -> str:
    """The RECORDED JOB TITLES block — facts, then one narrow rule (#321).

    ADR-062 clause 2 applied the way ``render_figure_ownership_block`` applies
    it: hand over the underlying facts verbatim, plus the narrowest instruction
    that keeps them from being over-read. The facts are the vault's own title
    strings; the instruction distinguishes the two cases a deterministic rule
    cannot tell apart — a sentence that *describes* what the candidate is
    responsible for (fine) and one that *names* a title they never held (the
    #321 defect, where the invented title was assembled out of the same
    position's own achievement text and therefore traced to the vault by every
    coverage check the system has).

    Stated in both directions on purpose: run #8's invented title *understated*
    the real one, so neither blind panel reviewer flagged it, and the Oracle
    graded the sentence ``grounded`` on the real headcount around it. A title
    the employer's record contradicts is a false statement about the candidate
    whichever way it drifts (ADR-057: positioning must ground).

    Returns "" when the vault records no title at all.
    """
    if not titles:
        return ""
    lines = [
        "RECORDED JOB TITLES (deterministic vault lookup — this is ground truth, "
        "do not re-derive it). These are the only titles the candidate has held, "
        "each with the position it is recorded under:",
    ]
    for t in titles:
        where = " ".join(part for part in (t.org, f"({t.span})" if t.span else "") if part)
        line = f'  - "{t.title}"'
        if where:
            line += f" — {where}"
        if t.aliases:
            also = ", ".join(f'"{a}"' for a in t.aliases)
            line += f"; also recorded for this same position as {also}"
        lines.append(line)
    lines += [
        "",
        "Whether a sentence states a TITLE for the candidate is YOUR judgement, "
        "from the letter's own prose — describing what the candidate is "
        "responsible for is not a title claim, and must never be flagged as "
        "one. But where the letter does name the role the candidate holds or "
        "held at one of their own positions, that name must be one of the "
        "titles above, for that same position, character for character. A "
        "title assembled out of a responsibility, an achievement, a "
        "certification or a ledger term is ungrounded even though every word "
        "of it appears somewhere in the vault: set approved=false and name the "
        "recorded title the writer must use instead.",
        "This holds in BOTH directions. A title that understates the recorded "
        "one is as false as one that inflates it — the employer's own record is "
        "what a reference call or an Arbeitszeugnis returns, and the CV, built "
        "from this same vault, states the recorded title. Titles are names: "
        "never ask for one to be translated into the letter's language, and "
        "never ask for a title this list does not carry.",
    ]
    return "\n".join(lines)
