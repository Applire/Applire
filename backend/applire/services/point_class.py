# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DH-1b — the class of each OPEN finding the final review left (truth | coverage | form).

Founder ruling DH-1 (2026-11-25, "reviewer tags, truth holds"): only truth points (and
the Oracle/ATS truth keys) hold the download; coverage and form points are advice on
the same card. The class comes from ONE separate call after the terminal review, never
from the reviewer itself: a class field inside the reviewer's schema made it stricter
(4 approved CVs: 1/20 → 11/20 blocking verdicts, replayed on the persisted input), so
the reviewer's prompt stays byte-identical and this call only labels what it found.

- Runs only when the review left blocking issues: a clean document costs no call.
- Fails closed: a failed or timed-out call, a wrong shape, a missing index or an
  unknown class leaves that finding untagged — and untagged is truth
  (``review_signals``).
- Stage ``terminal_point_class`` labels the LLM log record AND the ``llm_usage`` row
  (the generation's attribution is re-opened with this stage, document and application
  kept). The call runs on the review's own provider inside the same generation, so the
  allowance counts it with that job — no debit of its own.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

from applire.content_log import content_logger

logger = logging.getLogger(__name__)
clog = content_logger(__name__)

STAGE = "terminal_point_class"
CLASSES = ("truth", "coverage", "form")
#: An answer is a handful of short JSON entries; a reasoning model still has room.
_MAX_TOKENS = 1024
_TIMEOUT_S = 90.0


def _open_findings(outcome) -> tuple[list[str], list[int]]:
    """The reviewer's own open findings: the blocking issues that are not code facts
    (``[check] …``), and the ``finding`` notes (the shortened letter's round)."""
    from applire.services.terminal_review_outcome import _CHECK_ISSUE_RE, TerminalNote

    texts = [
        t for t in dict.fromkeys(outcome.blocking_issues)
        if isinstance(t, str) and t.strip() and not _CHECK_ISSUE_RE.match(t.strip())
    ]
    note_idx = [
        i for i, n in enumerate(outcome.notes)
        if isinstance(n, TerminalNote) and n.kind == "finding" and (n.text or "").strip()
    ]
    return texts, note_idx


def parse_classes(response: Any, n: int) -> list[str]:
    """``{"classes": [{"index", "class"}]}`` → one class per finding, ``""`` where the
    answer has none (read as truth).

    MD-G32 (X-6): a class is accepted only as the exact word after strip/lower — a
    hedged answer ("form, but the claim is also untrue", "coverage/truth") is truth. An
    index answered twice with different classes is truth."""
    out = [""] * n
    entries = response.get("classes") if isinstance(response, dict) else None
    if not isinstance(entries, list):
        return out
    for e in entries:
        if not isinstance(e, dict):
            continue
        idx, raw = e.get("index"), e.get("class")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < n:
            continue
        cls = raw.strip().lower() if isinstance(raw, str) else ""
        if cls not in CLASSES:
            cls = "truth"
        if out[idx] and out[idx] != cls:
            cls = "truth"
        out[idx] = cls
    return out


async def classify_open_points(outcome, *, document: dict | None, provider) -> Any:
    """``outcome`` with ``point_tags`` (and the ``finding`` notes' ``tag``) set for its
    open findings. Returns ``outcome`` unchanged when nothing is open or the call fails."""
    if outcome is None or not outcome.blocking_issues or provider is None:
        return outcome
    texts, note_idx = _open_findings(outcome)
    items = texts + [outcome.notes[i].text for i in note_idx]
    if not items:
        return outcome
    classes = await _classify(items, document, provider)
    tags = tuple((t, c) for t, c in zip(texts, classes) if c)
    notes = list(outcome.notes)
    for j, i in enumerate(note_idx):
        c = classes[len(texts) + j]
        if c:
            notes[i] = dataclasses.replace(notes[i], tag=c)
    return dataclasses.replace(outcome, point_tags=outcome.point_tags + tags, notes=tuple(notes))


async def _classify(items: list[str], document: dict | None, provider) -> list[str]:
    from applire.prompts.point_class import POINT_CLASS_SYSTEM_PROMPT, build_point_class_prompt
    from applire.providers.llm.debug_log import current_call_site, llm_log_stage, set_review_call_meta

    from applire.providers.llm.usage import current_attribution, llm_usage_context

    _, role, attempt = current_call_site()
    set_review_call_meta(None, None)
    # MD-G32 (X-10): the generation's usage attribution (stage "cv"/"cover_letter")
    # wins over the log stage, so the usage row is re-labelled here — same document,
    # same application, same person; only the stage names this call. Nothing debits by
    # stage (allowance counts the job), so the cost is visible without a debit of its own.
    att = current_attribution()
    try:
        with llm_usage_context(
            stage=STAGE, document_kind=att.document_kind, document_id=att.document_id,
            application_id=att.application_id, user_id=att.user_id,
        ), llm_log_stage(STAGE):
            response = await asyncio.wait_for(
                provider.aparse_json(
                    build_point_class_prompt(items, document),
                    system=POINT_CLASS_SYSTEM_PROMPT,
                    temperature=0.1,
                    max_tokens=_MAX_TOKENS,
                ),
                timeout=_TIMEOUT_S,
            )
    except Exception as exc:  # fails closed: every finding stays truth
        logger.warning("POINT_CLASS call failed (%s) — %d open finding(s) stay truth", type(exc).__name__, len(items))
        clog.debug("POINT_CLASS call failed", exc_info=True)
        return [""] * len(items)
    finally:
        set_review_call_meta(role, attempt)
    classes = parse_classes(response, len(items))
    logger.info(
        "POINT_CLASS %d open finding(s): %s",
        len(items), {c or "none": classes.count(c) for c in dict.fromkeys(classes)},
    )
    return classes
