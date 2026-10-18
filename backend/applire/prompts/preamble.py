# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The shared prompt context for every generation and review call (E068 US370, E066 1.6).

Three arms, chosen by ``PROMPT_CONTEXT`` (read at call time, like ``CV_PIPELINE``).
The default is ``c`` (ruling W3-6 = B, founder 2026-10-18, on the 10-17 US370 screen:
one-language and letter length better on Luna, confirmed by the Sonnet judge).
``off`` is the opt-out: every call is then byte-identical to the call without this
module.

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
import json
import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

PROMPT_CONTEXT_ENV = "PROMPT_CONTEXT"
ARMS = ("off", "a", "b", "c")
DEFAULT_ARM = "c"

#: Extra output budget for arm C's analysis field (≤ 120 words asked).
ANALYSIS_EXTRA_TOKENS = 512
ANALYSIS_KEY = "analysis"


def prompt_context() -> str:
    """The active arm: ``c`` (default), ``a``, ``b`` or ``off`` (read at call time).

    An unset, empty or unknown value is the default; only ``off`` opts out.
    """
    raw = (os.environ.get("PROMPT_CONTEXT", DEFAULT_ARM) or "").strip().lower()
    return raw if raw in ARMS else DEFAULT_ARM


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


_TASK_MARKER = "--- YOUR TASK ---\n"


def own_system(system: str | None) -> str:
    """The call site's own system prompt, without the preamble in front (for test doubles)."""
    text = system or ""
    if text.startswith("CONTEXT") and _TASK_MARKER in text:
        return text.split(_TASK_MARKER, 1)[1]
    return text


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
    'strength undersold. Write it as plain sentences with no double quotation marks inside it. '
    'The system removes "analysis"; it is never shown or delivered. Then give '
    "every other key exactly as specified above."
)
_ANALYSIS_REVIEWER = (
    'Your JSON object begins with the key "analysis", before every other key: a string of at '
    "most 120 words in which you work out, before you judge, (1) what would make this document "
    "fail with its reader, and (2) for each finding you are about to raise, the source line "
    "that backs it. Write it as plain sentences with no double quotation marks inside it. "
    'The system removes "analysis"; it is never shown. Then give every other key '
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


_ANALYSIS_OPEN = re.compile(r'^\s*\{\s*"analysis"\s*:\s*"')
_KEY_BOUNDARY = re.compile(r'"\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*:')


def repair_broken_analysis(doc: str | None) -> dict[str, Any] | None:
    """The reply without its ``analysis`` value, when that value is what broke the JSON.

    The 10-17 screen's failure shape (ministral-8b, letter writer): a double quote
    inside the analysis text ends the string early, and the parser stops at the next
    word ("Expecting ',' delimiter"). Everything after the analysis value is intact.

    A fact about syntax, not a judgement about content (ADR-062 cl. 1): the analysis
    value is cut at the FIRST key boundary after the stray quote and the rest is parsed
    as it stands. Nothing of the document's own fields is rewritten (ADR-094). Only that
    one boundary is tried — a later one could parse a suffix and silently drop real
    fields — so anything else returns None and the caller retries the call.
    """
    if not doc:
        return None
    text = doc.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    opened = _ANALYSIS_OPEN.match(text)
    if not opened:
        return None
    stray = text.find('"', opened.end())  # where the parser believed the string ended
    if stray < 0:
        return None
    boundary = _KEY_BOUNDARY.search(text, stray)
    if boundary is None:
        return None
    try:
        rest = json.loads("{" + text[boundary.start() + 1:].lstrip(" \t\r\n,"))
    except json.JSONDecodeError:
        return None
    return rest if isinstance(rest, dict) and rest else None


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
    if arm != "c":
        with _primer(site, arm):
            return await provider.aparse_json(prompt, **kwargs)
    plain_prompt, plain_kwargs = prompt, dict(kwargs)
    c_kwargs = dict(kwargs)
    c_kwargs["max_tokens"] = int(kwargs.get("max_tokens", 4096)) + ANALYSIS_EXTRA_TOKENS
    if kwargs.get("json_schema"):
        c_kwargs["json_schema"] = with_analysis_schema(kwargs["json_schema"])
    try:
        result = await provider.aparse_json(f"{prompt}\n\n{_analysis_instruction(site)}", **c_kwargs)
    except json.JSONDecodeError as exc:
        # A reply broken by the analysis field never ends a run (ruling W3-6 (2)): repair
        # the JSON when the break is inside the analysis value, otherwise retry the call
        # once WITHOUT the field — the call the site made before arm c. A retry that
        # breaks too raises as it always did, into the site's own handling.
        repaired = repair_broken_analysis(getattr(exc, "doc", None))
        if repaired is not None:
            logger.warning("PROMPT_CONTEXT_ANALYSIS_REPAIRED site=%s error=%s", site, exc.msg)
            return repaired
        logger.warning("PROMPT_CONTEXT_ANALYSIS_RETRY site=%s error=%s", site, exc.msg)
        return await provider.aparse_json(plain_prompt, **plain_kwargs)
    return strip_analysis(result)


async def acomplete(provider: Any, site: str, prompt: str, **kwargs: Any) -> Any:
    """``provider.acomplete`` with the active arm applied (no analysis field: the reply is text)."""
    arm = prompt_context()
    if arm == "off" or site not in SITES:
        return await provider.acomplete(prompt, **kwargs)
    kwargs["system"] = frame_system(kwargs.get("system"), site, arm)
    with _primer(site, arm):
        return await provider.acomplete(prompt, **kwargs)
