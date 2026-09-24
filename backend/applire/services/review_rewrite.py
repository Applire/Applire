# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The removal rewrite behind *Take it out for me* (ADR-090 cl. 3) — WP-B, Contract 1.

``rewrite_for_removal`` takes one section's text and the matched forms of one
group-1 finding (ADR-090 cl. 2) and returns the section with every form taken out
by a model rewrite. It never saves: the caller (``services/review_actions.py``)
writes ``after`` through the existing section-override path, awaits the re-audit
and keeps ``before`` for Undo.

* **CV** — ``section_id``/``section_text`` are exactly what
  ``PATCH /api/cv/{id}/sections/{section_id}`` takes and
  ``cv_section_editor.apply_overrides_to_tailored`` renders: ``introduction``
  (prose), ``skills`` (one entry per line), ``position::<uuid>`` (one bullet per
  line). One call for the section.
* **Cover letter** — the only patchable section is ``"body"``
  (``SectionOverridePatch.section: Literal["body"]``), the paragraphs joined by a
  blank line (the editor's ``paragraphs.join("\\n\\n")``). Only the paragraphs that
  hold a form are rewritten, one call each; the others and every separator are
  carried over byte for byte, and ``after`` is the whole reassembled body.

**All or nothing.** A result that still contains a form (under the audit's own
presence predicate, ``ats_audit.surface_present`` — the same test the awaited
re-audit applies), an empty result, or a truncated completion returns
``changed=False`` with ``after == before``: never a partial save. A paragraph the
model deleted outright (rule 2 of the prompt: it existed only to claim the wording)
is a legitimate result and is dropped with its separator.

**Not a repair pass.** Nothing here edits model output: the checks only decide
whether the model's text is offered at all (ADR-082 — no deterministic deletion of
delivered prose; ADR-062 cl. 1 — "is this form in this text" is a fact). The
occurrence list handed to the prompt is likewise a computed fact about the input.

Provider errors other than truncation propagate — the caller maps them to its
HTTP answer; nothing was written.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from applire.exceptions import LLMTruncatedError
from applire.prompts.review_rewrite import (
    REVIEW_FIGURE_REWRITE_SYSTEM_PROMPT,
    REVIEW_REWRITE_SYSTEM_PROMPT,
    build_review_rewrite_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.services.ats_audit import _norm, _verb_stem, surface_present

logger = logging.getLogger(__name__)

#: The letter body's paragraph separator as the editor writes it; the split keeps
#: whatever separator the stored body actually carries.
_PARAGRAPH_SPLIT_RE = re.compile(r"(\r?\n[ \t]*\r?\n\s*)")

#: Characters that continue a word for the purpose of naming the whole token an
#: occurrence sits in ("Power-BI-Dashboards", "AI-governance").
_WORD_CONT_RE = re.compile(r"[\w\-‐-―−]")

_TEMPERATURE = 0.2


@dataclass(frozen=True)
class RemovalRewrite:
    """Contract 1's return value. ``after == before`` whenever ``changed`` is False."""

    section_id: str
    before: str
    after: str
    changed: bool
    llm_calls: int


def form_present(form: str, text: str) -> bool:
    """The audit's presence test for one form in one passage (``surface_present``)."""
    return surface_present(form, _norm(text))


def figure_present(figure: str, text: str) -> bool:
    """Is the named figure still in ``text``? (E-1, ``figures_only`` mode.)

    Two facts, either suffices: the Oracle's own figure extractor
    (``oracle.matchers.figures.extract_figures``) finds a figure of the same kind and
    canonical value — so "40,000" is also caught when rewritten as "40000" — or the
    figure's literal spelling still stands as a whole token (a spelling the extractor
    does not read yet, e.g. "~40k"). A substring test would be wrong here: "38" is
    inside "380" and "2038".
    """
    from applire.services.oracle.matchers.figures import extract_figures

    wanted = {(f.kind, f.value) for f in extract_figures(figure)}
    if wanted and any((f.kind, f.value) in wanted for f in extract_figures(text)):
        return True
    literal = re.escape(figure.strip())
    return bool(re.search(rf"(?<![\w.,]){literal}(?![\w]|[.,]\d)", text, flags=re.IGNORECASE))


def find_occurrences(forms: list[str], text: str) -> list[str]:
    """The spellings under which ``forms`` occur in ``text``, as whole tokens.

    A fact about the input, handed to the prompt (never used to edit output):
    a form that matched through ``_fold_variants`` is found by a case- and
    hyphen-insensitive search and widened to the token it stands in; a
    single-token form that matched only through the audit's verb-stem fold is
    found as the passage's word sharing its stem ("Coaching" → "coached").
    Order of first appearance, de-duplicated.
    """
    found: list[tuple[int, str]] = []
    seen: set[str] = set()

    def _add(pos: int, span: str) -> None:
        key = span.lower()
        if key not in seen:
            seen.add(key)
            found.append((pos, span))

    for form in forms:
        tokens = _norm(form).split()
        if not tokens:
            continue
        pattern = r"[\s\-‐-―−]+".join(re.escape(t) for t in tokens) + r"s?"
        hit = False
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = m.start(), m.end()
            while start > 0 and _WORD_CONT_RE.match(text[start - 1]):
                start -= 1
            while end < len(text) and _WORD_CONT_RE.match(text[end]):
                end += 1
            _add(start, text[start:end])
            hit = True
        if hit or len(tokens) != 1:
            continue
        stem = _verb_stem(tokens[0])
        for m in re.finditer(r"\w+", text):
            if _verb_stem(_norm(m.group(0))) == stem:
                _add(m.start(), m.group(0))
    return [span for _, span in sorted(found)]


def _passage_kind(kind: str, section_id: str) -> str:
    if kind == "cover_letter":
        return "letter_paragraph"
    if section_id == "introduction":
        return "summary"
    if section_id == "skills":
        return "skills"
    if section_id.startswith("position::"):
        return "bullets"
    raise ValueError(f"Unknown CV section_id: {section_id!r}")


async def _rewrite_passage(
    passage: str,
    forms: list[str],
    provider: LLMProvider,
    *,
    passage_kind: str,
    language: str,
    figures_only: bool = False,
) -> str | None:
    """One model call. Returns the stripped text, or ``None`` on truncation."""
    prompt = build_review_rewrite_prompt(
        passage,
        forms,
        passage_kind=passage_kind,
        language=language,
        occurrences=None if figures_only else find_occurrences(forms, passage),
        figures_only=figures_only,
    )
    # Output ≈ input length; a generous ceiling so a German passage is never cut.
    max_tokens = min(4096, max(400, len(passage) // 2 + 256))
    try:
        out = await provider.acomplete(
            prompt,
            system=REVIEW_FIGURE_REWRITE_SYSTEM_PROMPT if figures_only else REVIEW_REWRITE_SYSTEM_PROMPT,
            temperature=_TEMPERATURE,
            max_tokens=max_tokens,
            disable_thinking=True,  # a bounded edit, not a generation (chrome tier)
        )
    except LLMTruncatedError:
        logger.warning("REVIEW_REWRITE_TRUNCATED kind=%s chars=%d", passage_kind, len(passage))
        return None
    return (out or "").strip()


async def rewrite_for_removal(
    kind: Literal["cv", "cover_letter"],
    record: Any,
    section_id: str,
    section_text: str,
    forms: list[str],
    provider: LLMProvider,
    *,
    language: str,
    figures_only: bool = False,
) -> RemovalRewrite:
    """Remove every matched form of one finding from one section (Contract 1).

    ``record`` is the ``GeneratedCV`` / ``GeneratedCoverLetter`` row; only its ``id``
    is read, for the provider-usage attribution (ADR-086). ``language`` is the
    document's pinned output language (``"en"``/``"de"``).

    ``figures_only`` (founder ruling E-1, 2026-09-24): ``forms`` are figures an Oracle
    verdict names ("40,000", "€2.5M", "30 %"). The figure variant of the prompt removes
    ONLY the figure (or drops the quantity) and keeps every other word; presence is
    :func:`figure_present`, not the keyword predicate; and no statement may be deleted,
    so an empty passage is refused rather than dropped.
    """
    from applire.providers.llm.debug_log import llm_log_stage
    from applire.providers.llm.usage import llm_usage_context

    if kind == "cover_letter" and section_id != "body":
        raise ValueError(f"Cover-letter section {section_id!r} is not patchable (only 'body')")
    passage_kind = _passage_kind(kind, section_id)
    forms = [f for f in dict.fromkeys(f.strip() for f in forms if f and f.strip())]
    present = figure_present if figures_only else form_present
    unchanged = RemovalRewrite(section_id, section_text, section_text, False, 0)
    if not forms or not any(present(f, section_text) for f in forms):
        return unchanged

    calls = 0
    with llm_usage_context(
        stage="review_take_out",
        document_kind=kind,
        document_id=getattr(record, "id", None),
    ), llm_log_stage("review_take_out"):
        if kind == "cv":
            calls = 1
            after = await _rewrite_passage(
                section_text, forms, provider, passage_kind=passage_kind, language=language,
                figures_only=figures_only,
            )
            if after is None:
                return RemovalRewrite(section_id, section_text, section_text, False, calls)
        else:
            pieces = _PARAGRAPH_SPLIT_RE.split(section_text)
            # pieces alternate: paragraph, separator, paragraph, …
            out_paras: list[str | None] = []
            for i in range(0, len(pieces), 2):
                para = pieces[i]
                if any(present(f, para) for f in forms):
                    calls += 1
                    new = await _rewrite_passage(
                        para.strip(), forms, provider,
                        passage_kind=passage_kind, language=language,
                        figures_only=figures_only,
                    )
                    if new is None or (figures_only and not new):
                        return RemovalRewrite(section_id, section_text, section_text, False, calls)
                    out_paras.append(new or None)
                else:
                    out_paras.append(para)
            after = _reassemble(pieces, out_paras)

    after_check = after.strip()
    still = [f for f in forms if present(f, after_check)]
    if not after_check or still:
        logger.info(
            "REVIEW_REWRITE_REFUSED kind=%s section=%s empty=%s forms_remaining=%r calls=%d",
            kind, section_id, not after_check, still, calls,
        )
        return RemovalRewrite(section_id, section_text, section_text, False, calls)
    if after == section_text:
        return RemovalRewrite(section_id, section_text, section_text, False, calls)
    return RemovalRewrite(section_id, section_text, after, True, calls)


def _reassemble(pieces: list[str], out_paras: list[str | None]) -> str:
    """Join paragraphs back with their original separators. A deleted paragraph
    (``None``) takes the separator before it with it; when the FIRST kept paragraph
    follows deleted ones it gets no separator at all."""
    out: list[str] = []
    for idx, para in enumerate(out_paras):
        if para is None:
            continue
        if out:
            out.append(pieces[2 * idx - 1])
        out.append(para)
    return "".join(out)
