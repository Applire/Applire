# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The versioned primer turns for prompt arm B (E068 US370, E066 1.6).

A primer is a pre-built user/assistant exchange placed between the system prompt and
the task: the question a careful writer would ask before starting, answered by a
frontier model. Keyed by (document type, role). One English version per key in this
first version: the answer tells the model to write in the document's language, and a
per-language variant is only worth authoring if arm B wins the screen.

Changing a text means a new ``PRIMER_VERSION``; the bench records the version through
``PROMPT_CONTEXT`` and this module's metadata.
"""

from __future__ import annotations

PRIMER_VERSION = "2026-10-17.1"
PRIMER_AUTHOR_MODEL = "claude-opus-5-5"
PRIMER_AUTHORED = "2026-10-17"

_Q = {
    "writer": "Before I start: what separates a strong {doc} from one that fails here?",
    "reviewer": "Before I review: which problems in a {doc} actually cost the candidate, and which do not?",
    "corrector": "Before I revise: how do I fix what the feedback names without making the {doc} worse?",
    "editor": "Before I edit: what must survive when I change this {doc}?",
}
_DOC = {"cv": "CV", "letter": "cover letter"}

_A: dict[tuple[str, str], str] = {
    ("cv", "writer"): (
        "Four things decide it. One language: every line in the document language — summary, "
        "bullets, skills, education descriptions — with only names, tools and CEFR codes left "
        "as they are; a single sentence copied from a profile in another language is the most "
        "visible defect a recruiter sees. Full strength: lead each role with its largest "
        "verified result, keep every figure and scope word the profile gives (team size, "
        "budget, markets), and use the posting's own terms where the profile supports them — a "
        "real achievement written vaguely is lost. No repetition: each skill once in the skills "
        "list, each achievement in one bullet; two bullets that say the same thing in other "
        "words read as padding. No inflation: never raise a level, a title, a number or a scope "
        "beyond what the profile states — the candidate is asked about every line in the "
        "interview."
    ),
    ("cv", "reviewer"): (
        "Costly: a claim the profile does not support (an invented or raised figure, title, "
        "level or scope); text in a second language; the same skill or achievement twice; a "
        "strong verified result missing or flattened. Not costly: a wording you would phrase "
        "differently, an order you would change, a translation of profile evidence into the "
        "document language — that is grounded, not altered. Raise the costly ones as blocking, "
        "the rest as minor or not at all: every blocking finding buys a rewrite, and a rewrite "
        "can lose a correct fact."
    ),
    ("cv", "corrector"): (
        "Change only what the feedback names. Keep every figure, name and scope word the draft "
        "already has right. Write the fix in the document language, translating profile "
        "evidence rather than copying it. Do not answer a duplicate finding by rewording — "
        "remove one of the two. Do not answer an underselling finding by adding anything the "
        "profile does not state."
    ),
    ("cv", "editor"): (
        "Every figure, name and scope word in the passage survives, the passage stays in the "
        "document language, and it says nothing the original did not."
    ),
    ("letter", "writer"): (
        "A strong letter answers one question: why this candidate for this job. Open with the "
        "role and the strongest verified reason, give two or three concrete results from the "
        "profile tied to what the posting asks, and close with a plain request for the "
        "conversation. Write every sentence in the document language. Do not repeat the CV "
        "bullet by bullet; pick and connect. Keep it to its page: shorter and specific beats "
        "longer and general. Never claim a level, a figure or a responsibility the profile "
        "does not state — the letter is read before the interview and checked in it."
    ),
    ("letter", "reviewer"): (
        "Costly: a claim the profile does not support; a sentence in a second language; the "
        "same point made twice; a letter that runs past its page; the candidate's strongest "
        "verified result missing. Not costly: tone you would set differently, a paragraph order "
        "you would change. Raise the costly ones as blocking, the rest as minor: every blocking "
        "finding buys a rewrite, and a rewrite can lose a correct fact."
    ),
    ("letter", "corrector"): (
        "Change only what the feedback names, keep every correct fact and figure, write in the "
        "document language, and do not make the letter longer — when you add a point, cut a "
        "weaker one."
    ),
    ("letter", "editor"): (
        "Cut repetition and general sentences first, then the weakest example. Keep the "
        "opening reason, the strongest verified results with their figures, and the closing "
        "request. Stay in the document language and add nothing new."
    ),
}


def primer_for(doc: str, role: str) -> tuple[str, str] | None:
    """The (user, assistant) primer for a document type and role, or None."""
    answer = _A.get((doc, role))
    if answer is None:
        return None
    return _Q[role].format(doc=_DOC[doc]), answer


def primer_metadata() -> dict[str, str]:
    return {"version": PRIMER_VERSION, "author_model": PRIMER_AUTHOR_MODEL, "authored": PRIMER_AUTHORED}
