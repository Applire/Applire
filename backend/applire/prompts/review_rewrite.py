# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The removal rewrite behind *Take it out for me* (ADR-090 cl. 3, WP-B).

One single-turn call per passage — a CV section, or one cover-letter paragraph —
that removes the flagged wording and changes nothing else. Called only from
``services/review_rewrite.py``.

**Why this is its own prompt and not ``rewrite_section``'s** (``prompts/cv_assist.py``,
US089), decided under ``applire-prompt-first`` (2026-09-23):

* *Step 1 — does any rule ask for it?* ``ASSIST_REWRITE_SYSTEM_PROMPT`` asks for "the
  improved section text" and its user prompt ends "Schreibe den Abschnitt neu …
  Gib nur den verbesserten Text aus". No rule asks to keep every other fact, to add
  nothing, to keep the line layout, or to write in the document's pinned language.
  A removal is category B there: never asked.
* *Step 3 — contradictions.* Passing "remove X" as the user's ``directions`` sets
  it against "improve the section" and against the ``Zielrolle`` line the builder
  appends — an invitation to tailor toward the posting, i.e. to ADD.
* *ADR-084.* ``directions`` is embedded as the user's own words, unfenced; the
  forms here come from the posting's Keyword Ledger and are third-party text.
* The replay (``Documents/Runs/Nougat/review-recut/b/report.md``) measured both
  prompts on the same fixtures before this one was kept.

The prompt names its task as a deletion, not an edit: every rule below exists to
keep a section-scoped rewrite from rephrasing sentences the finding did not name
(`SF-REVIEW.8`).
"""

from __future__ import annotations

REVIEW_REWRITE_SYSTEM_PROMPT = """\
You remove wording from one passage of a job-application document — a CV section or \
one paragraph of a cover letter. The candidate's profile does not back that wording \
and the candidate asked for it to be taken out. You are an editor making a deletion, \
not a writer improving the text.

Rules:
1. Remove every occurrence of each wording listed under WORDING TO REMOVE — in any \
capitalisation, with or without hyphens, singular or plural, as another inflected form \
of the same word, and where it stands inside a longer name or compound word (then drop \
or shorten that name so the wording is gone).
2. Decide per clause what the wording does in it:
   - The clause states the wording itself as experience, knowledge or an activity \
("experience with Terraform", "familiar with Six Sigma", "introduced Scrum to the \
team"): delete the clause. A skill entry, bullet or sentence with nothing else in it is \
deleted whole.
   - The wording names the tool, method or setting through which something else was \
done ("built pipelines in Terraform", "cut defects with Six Sigma tools"): keep what was \
done and drop only the wording and the words that attach it ("built pipelines", "cut \
defects").
   - When in doubt, keep the statement without the wording rather than delete it.
3. What remains must read as correct, natural sentences in the passage's language. \
Never leave a gap where the wording was: no dangling preposition, no missing object, \
no orphaned fragment ("I introduced into our process", "Kenntnisse in und SAP", a \
stray "-Zertifizierung"). Repair the sentence with the words it needs to be grammatical \
(an article, a verb, a conjunction), never with new content. If the wording is the \
verb of a clause, rebuild the clause around what stays true or delete it — never leave \
its object standing alone. A purpose, result or \
detail that belonged to a deleted clause goes with it — never attach it to a different \
statement.
4. Change nothing else. Every other statement stays with its facts intact: employers, \
job titles, dates, numbers, percentages, tools, methods, results, team sizes and scope. \
Keep the original words and word order wherever the removal does not force a change.
5. Add nothing: no new claim, skill, tool, figure, employer, date or qualification, and \
no synonym, paraphrase or umbrella term that says what the removed wording said. \
Replacing the wording with a near-equivalent is not taking it out.
6. Keep the layout of the passage as described under PASSAGE KIND. Lines are only ever \
deleted under rule 2 — never merged, split, reordered, numbered or given bullet \
markers. Add no heading and no blank line. Write in the OUTPUT LANGUAGE, the language \
the passage is already in; never translate.
7. Output only the edited passage exactly as it should now read: no commentary, no \
quotation marks around it, no markdown."""


#: Layout description per passage kind — rule 6 points at it. Keyed by the
#: shape the section text has in the section-override write, not by label.
PASSAGE_KINDS: dict[str, str] = {
    "summary": "the profile summary of a CV — prose.",
    "skills": "the skills list of a CV — one skill entry per line.",
    "bullets": (
        "the work-experience bullets of one position in a CV — one bullet per line, "
        "written without bullet markers."
    ),
    "letter_paragraph": "one paragraph of a cover letter — prose, a single paragraph.",
}

_LANGUAGE_NAMES = {"de": "GERMAN", "en": "ENGLISH"}


def language_name(language: str) -> str:
    """The pinned document language as the prompt names it (``cv_segmented``'s form)."""
    return _LANGUAGE_NAMES.get((language or "").lower(), (language or "").upper() or "ENGLISH")


def build_review_rewrite_prompt(
    passage: str,
    forms: list[str],
    *,
    passage_kind: str,
    language: str,
    occurrences: list[str] | None = None,
) -> str:
    """User prompt for one removal rewrite.

    ADR-084 embedding point 29 (Form A): ``forms`` are Keyword-Ledger surface forms
    — the job posting's text — so they are fenced as a block. They are removed, never
    reproduced, so the fence cannot leak into the document (the Form-B concern does
    not apply). ``passage`` is the candidate's own document text and is delimited, not
    fenced. ``occurrences`` are the spellings the passage actually uses (a computed
    fact, see ``services/review_rewrite.find_occurrences``) — they matter when a form
    matched only through the audit's stem fold ("Coaching" vs "coached").
    """
    from applire.services.untrusted_text import fence

    wording = "\n".join(f"- {f}" for f in forms)
    parts = [
        f"PASSAGE KIND: {PASSAGE_KINDS[passage_kind]}",
        f"OUTPUT LANGUAGE: {language_name(language)}",
        "",
        fence(wording, header="WORDING TO REMOVE"),
    ]
    if occurrences:
        parts.append(
            "In this passage the wording appears as: "
            + "; ".join(f'"{o}"' for o in occurrences)
        )
    parts += [
        "",
        "PASSAGE:",
        "----- PASSAGE START -----",
        passage,
        "----- PASSAGE END -----",
    ]
    return "\n".join(parts)
