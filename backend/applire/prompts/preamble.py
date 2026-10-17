# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The shared prompt context for every generation and review call (E068 US370, E066 1.6).

Three experiment arms, chosen by ``PROMPT_CONTEXT`` (read at call time, like
``CV_PIPELINE``). The default is ``off``: every call is byte-identical to the call
without this module. An arm becomes the default only once it wins on the bench
(ADR-062 clause 7).

* ``a`` — the big-picture preamble: what Applire is, the honesty contract, the
  pipeline map with *you are here*, and what happens to this output next. It goes in
  front of the call's own system prompt.
* ``b`` — ``a`` plus a primer turn: a pre-built user/assistant exchange
  (:mod:`applire.prompts.primers`, versioned, with author model and date) between the
  system prompt and the task. Only OpenAI-compatible providers that build a message
  list read it (OpenRouter today); elsewhere ``b`` acts as ``a``.
* ``c`` — ``a`` plus an ``analysis`` field first in the JSON response: the model
  reasons in it before the real fields. The field is removed before any caller sees
  the result, so no document, receipt or store ever holds it.

Each call site hooks in with one line: :func:`aparse_json` / :func:`acomplete` in
place of ``provider.aparse_json`` / ``provider.acomplete``, naming its site. A site
not in :data:`SITES` is passed through untouched, so the shared review loop can hook
once and leave the extraction and interview chains as they are.
"""

from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

PROMPT_CONTEXT_ENV = "PROMPT_CONTEXT"
ARMS = ("off", "a", "b", "c")

#: Extra output budget for arm C's analysis field (≤ 120 words asked).
ANALYSIS_EXTRA_TOKENS = 512
ANALYSIS_KEY = "analysis"


def prompt_context() -> str:
    """The active arm: ``off`` (default), ``a``, ``b`` or ``c`` (read at call time)."""
    raw = (os.environ.get(PROMPT_CONTEXT_ENV, "off") or "").strip().lower()
    return raw if raw in ARMS else "off"


# --------------------------------------------------------------------------- #
# the pipeline map
# --------------------------------------------------------------------------- #

PIPELINE_STEPS: tuple[str, ...] = (
    "Job analysis — reads the job posting and lists what it requires.",
    "Gap analysis — matches the candidate's verified profile against those requirements.",
    "CV writer — writes the tailored CV from verified profile facts only.",
    "CV review — reviewers check grounding, language and duplicates; a corrector revises.",
    "Letter writer — writes the cover letter from the CV and the verified profile.",
    "Letter review — reviewers check grounding and language; the letter is condensed to its page.",
    "Audit and hand-over — every claim is checked against the profile; the candidate "
    "reads, edits and sends the documents under their own name.",
)


@dataclass(frozen=True)
class Site:
    step: int  # 1-based index into PIPELINE_STEPS
    doc: str  # jd | gap | cv | letter
    role: str  # writer | reviewer | corrector | editor
    task: str  # what this call does, one clause
    next: str  # what happens to this output next


_CV_REVIEW_NEXT = (
    "Your verdict decides whether the CV is revised again. A finding you raise costs a "
    "rewrite, and a rewrite can lose a correct fact — raise what a reader would notice."
)
_LETTER_REVIEW_NEXT = (
    "Your verdict decides whether the letter is revised again. A finding you raise costs "
    "a rewrite, and a rewrite can lose a correct fact — raise what a reader would notice."
)
_CV_CORRECTOR_NEXT = (
    "Your revision goes back to the reviewer, then to the audit and to the candidate. "
    "Fix what the feedback names; keep every correct fact you were given."
)
_LETTER_CORRECTOR_NEXT = (
    "Your revision goes back to the reviewer, then to the audit and to the candidate. "
    "Fix what the feedback names; keep every correct fact you were given."
)

SITES: dict[str, Site] = {
    "job_analysis:writer": Site(1, "jd", "writer", "you extract the posting's requirements",
                                "The gap analysis, the CV and the letter are all built on your list."),
    "job_analysis:reviewer": Site(1, "jd", "reviewer", "you review the extracted requirements",
                                  "Your verdict decides whether the extraction is corrected."),
    "job_analysis:corrector": Site(1, "jd", "corrector", "you correct the extracted requirements",
                                   "The gap analysis, the CV and the letter are all built on your list."),
    "gap_analysis:writer": Site(2, "gap", "writer", "you classify each requirement against the profile",
                                "The CV and letter writers use your classification to choose what to stress."),
    "cv:writer": Site(3, "cv", "writer", "you write the tailored CV",
                      "Reviewers check your draft next; then the audit; then the candidate sends it."),
    "cv_tailoring:reviewer": Site(4, "cv", "reviewer", "you review the CV draft for grounding", _CV_REVIEW_NEXT),
    "cv_tailoring:corrector": Site(4, "cv", "corrector", "you revise the CV draft", _CV_CORRECTOR_NEXT),
    "cv_language:reviewer": Site(4, "cv", "reviewer", "you check the CV is in one language", _CV_REVIEW_NEXT),
    "cv_language:corrector": Site(4, "cv", "corrector", "you fix the CV's language", _CV_CORRECTOR_NEXT),
    "cv_language_recheck:reviewer": Site(4, "cv", "reviewer", "you re-check the CV is in one language",
                                         _CV_REVIEW_NEXT),
    "cv_language_recheck:corrector": Site(4, "cv", "corrector", "you fix the CV's language", _CV_CORRECTOR_NEXT),
    "cv_terminal_review:reviewer": Site(4, "cv", "reviewer", "you review the assembled CV before delivery",
                                        _CV_REVIEW_NEXT),
    "cv_terminal_review:corrector": Site(4, "cv", "corrector", "you revise the assembled CV", _CV_CORRECTOR_NEXT),
    "cover_letter:writer": Site(5, "letter", "writer", "you write the cover letter",
                                "Reviewers check your draft next; then it is condensed to its page, "
                                "audited and sent by the candidate."),
    "cover_letter:reviewer": Site(6, "letter", "reviewer", "you review the letter for grounding",
                                  _LETTER_REVIEW_NEXT),
    "cover_letter:corrector": Site(6, "letter", "corrector", "you revise the letter", _LETTER_CORRECTOR_NEXT),
    "letter_terminal_review:reviewer": Site(6, "letter", "reviewer",
                                            "you review the assembled letter before delivery", _LETTER_REVIEW_NEXT),
    "letter_terminal_review:corrector": Site(6, "letter", "corrector", "you revise the assembled letter",
                                             _LETTER_CORRECTOR_NEXT),
    "letter_condense:writer": Site(6, "letter", "editor", "you shorten the letter to fit its page",
                                   "The shortened letter is audited and sent; nothing restores what you cut."),
    "review_rewrite:editor": Site(4, "cv", "editor", "you rewrite one passage a reviewer flagged",
                                  "Your passage replaces the flagged one in the delivered document."),
}

#: The shared review loop names its two calls by chain id + role; its generator is
#: the corrector here.
_LOOP_ROLES = {"reviewer": "reviewer", "generator": "corrector"}


def loop_site(chain_id: str, loop_role: str) -> str:
    """The site key for one call of :func:`applire.services.reviewer.review_and_refine`."""
    return f"{chain_id}:{_LOOP_ROLES.get(loop_role, loop_role)}"


_HEAD = """\
CONTEXT — read this before your task.

Applire helps one candidate apply for one job. It keeps the candidate's verified
profile, writes a CV and a cover letter tailored to the posting, and checks every
claim against that profile before the candidate sends the documents under their
own name.

What wins: a truthful, strong document. The candidate is interviewed on every line.
A claim the profile does not support fails in that interview and costs the job; a
real strength left out, or written so weakly that a reader misses it, costs the
interview. Write in ONE language — the document's — and say each thing once.

The pipeline (you are at step {step}):
{steps}

Your step: {task}. Next: {next}

--- YOUR TASK ---
"""


def render_preamble(site: str) -> str:
    """The preamble text for ``site`` ("" for a site not in :data:`SITES`)."""
    s = SITES.get(site)
    if s is None:
        return ""
    steps = "\n".join(
        f"{i}. {text}{'   <- you are here' if i == s.step else ''}"
        for i, text in enumerate(PIPELINE_STEPS, start=1)
    )
    return _HEAD.format(step=s.step, steps=steps, task=s.task, next=s.next)


def frame_system(system: str | None, site: str, arm: str | None = None) -> str | None:
    """``system`` with the preamble in front (arms a/b/c); unchanged for ``off``."""
    arm = prompt_context() if arm is None else arm
    head = render_preamble(site) if arm != "off" else ""
    if not head:
        return system
    return head + (system or "")


# --------------------------------------------------------------------------- #
# arm B — the primer turn
# --------------------------------------------------------------------------- #

_primer_turns: ContextVar[tuple[dict[str, str], ...]] = ContextVar("prompt_primer_turns", default=())


def primer_turns() -> list[dict[str, str]]:
    """The primer messages for the call in flight (read by the provider's message builder)."""
    return list(_primer_turns.get())


@contextmanager
def _primer(site: str, arm: str) -> Iterator[None]:
    turns: tuple[dict[str, str], ...] = ()
    s = SITES.get(site)
    if arm == "b" and s is not None:
        from applire.prompts.primers import primer_for

        p = primer_for(s.doc, s.role)
        if p is not None:
            turns = ({"role": "user", "content": p[0]}, {"role": "assistant", "content": p[1]})
    token = _primer_turns.set(turns)
    try:
        yield
    finally:
        _primer_turns.reset(token)


# --------------------------------------------------------------------------- #
# arm C — the analysis field
# --------------------------------------------------------------------------- #

_ANALYSIS_WRITER = (
    'Your JSON object begins with the key "analysis", before every other key: a string of at '
    "most 120 words in which you work out, before you write anything else, (1) what this "
    "posting most needs, (2) which verified facts carry it, and (3) the trap at this step — "
    "another language slipping in, a point made twice, a claim the facts do not support, a real "
    'strength undersold. The system removes "analysis"; it is never shown or delivered. Then give '
    "every other key exactly as specified above."
)
_ANALYSIS_REVIEWER = (
    'Your JSON object begins with the key "analysis", before every other key: a string of at '
    "most 120 words in which you work out, before you judge, (1) what would make this document "
    "fail with its reader, and (2) for each finding you are about to raise, the source line "
    'that backs it. The system removes "analysis"; it is never shown. Then give every other key '
    "exactly as specified above."
)


def _analysis_instruction(site: str) -> str:
    s = SITES[site]
    return _ANALYSIS_REVIEWER if s.role == "reviewer" else _ANALYSIS_WRITER


def with_analysis_schema(json_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """An OpenAI-style ``json_schema`` block with ``analysis`` as its first property."""
    if not json_schema or not isinstance(json_schema.get("schema"), dict):
        return json_schema
    out = copy.deepcopy(json_schema)
    sch = out["schema"]
    if sch.get("type") != "object" or not isinstance(sch.get("properties"), dict):
        return json_schema
    sch["properties"] = {ANALYSIS_KEY: {"type": "string"}, **sch["properties"]}
    sch["required"] = [ANALYSIS_KEY, *[r for r in sch.get("required", []) if r != ANALYSIS_KEY]]
    return out


def strip_analysis(result: Any) -> Any:
    """``result`` without the arm-C ``analysis`` key (in place for a dict)."""
    if isinstance(result, dict):
        result.pop(ANALYSIS_KEY, None)
    return result


# --------------------------------------------------------------------------- #
# the one-line hooks
# --------------------------------------------------------------------------- #

async def aparse_json(provider: Any, site: str, prompt: str, **kwargs: Any) -> Any:
    """``provider.aparse_json`` with the active arm applied for ``site``."""
    arm = prompt_context()
    if arm == "off" or site not in SITES:
        return await provider.aparse_json(prompt, **kwargs)
    kwargs["system"] = frame_system(kwargs.get("system"), site, arm)
    if arm == "c":
        prompt = f"{prompt}\n\n{_analysis_instruction(site)}"
        kwargs["max_tokens"] = int(kwargs.get("max_tokens", 4096)) + ANALYSIS_EXTRA_TOKENS
        if kwargs.get("json_schema"):
            kwargs["json_schema"] = with_analysis_schema(kwargs["json_schema"])
    with _primer(site, arm):
        result = await provider.aparse_json(prompt, **kwargs)
    return strip_analysis(result) if arm == "c" else result


async def acomplete(provider: Any, site: str, prompt: str, **kwargs: Any) -> Any:
    """``provider.acomplete`` with the active arm applied (no analysis field: the reply is text)."""
    arm = prompt_context()
    if arm == "off" or site not in SITES:
        return await provider.acomplete(prompt, **kwargs)
    kwargs["system"] = frame_system(kwargs.get("system"), site, arm)
    with _primer(site, arm):
        return await provider.acomplete(prompt, **kwargs)
