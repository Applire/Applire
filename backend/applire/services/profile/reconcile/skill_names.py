# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-Q15 (Sorbet quality wave 2b) — a skill an answer creates is named in the
person's own words.

**Why (prompt-first category: a rule present and violated).** The reconcile
prompt has said since Hazelnut build 4 that a skill's name is "the new
information's own word for the capability … never a term only the question or
the gap label uses, never its expansion, translation or wider category". On the
captured answer turns of the three delivery-2a runs (luna, thinking off), 26 of
97 new skills still carried a name the answer does not contain; 15 of them were
the gap label or a wider category ("Security auditing" for "I ran our quarterly
dependency-audit rotation", whose CV chip the Oracle then grounded on the vault).
The prompt's own example named one of them ("Arbeitssicherheitsmanagement") as a
capability to record; that contradiction was removed, and the op gained a
verbatim ``said`` (the answer's own noun phrase). This module is the gate behind
the prompt: a name is code-checked against the answer, structurally (a contiguous
phrase of the answer), never by a word list.

**The rule, on an answer turn only** (interview, agent interview, testimony —
the doors whose NEW INFORMATION carries the person's ``answer``):

* an ``upsert_skill`` that targets a skill the profile holds is kept (the V-6
  alias path; its name is the profile's);
* a new skill's name is kept when it is a contiguous phrase of the answer (a
  word may differ in its ending: "MES-Systeme" for "MES-Systems"), or when the
  words that are not a held skill's own words are (the profile's "SAP" + the
  answer's "MM" → "SAP MM");
* otherwise the person's own phrase is stored: the op's ``said``, when it is a
  contiguous phrase of the answer of at most six words, is cut from the ANSWER
  (the person's spelling, not the model's) and becomes the name;
* otherwise no skill is written (the bullets keep what the answer said).

A doubtful case therefore stores the person's own phrase or nothing — never a
name the person did not say. The question's and the posting's words are never
evidence (ADR-061 / ADR-048 as amended).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Sequence

from applire.services.profile.reconcile.ops import UpsertSkill

logger = logging.getLogger(__name__)

#: The doors whose NEW INFORMATION is the person's answer to a question.
ANSWER_SOURCES = frozenset({"interview", "agent_interview", "testimony"})
#: The longest ``said`` phrase stored as a skill name (a noun phrase, not a clause).
MAX_SAID_WORDS = 6
_WORD = re.compile(r"\w+")


def _words(text: Any) -> list[tuple[str, int, int]]:
    """``(folded word, start, end)`` over the ORIGINAL text, so a matched
    window can be cut back out in the person's own spelling."""
    raw = unicodedata.normalize("NFC", str(text or ""))
    return [(m.group().casefold(), m.start(), m.end()) for m in _WORD.finditer(raw)]


def _same_word(a: str, b: str) -> bool:
    """Equal, or the same word with another ending (a plural or case ending):
    both at least four letters and differing only in the last letter or two."""
    if a == b:
        return True
    short = min(len(a), len(b))
    if short < 4 or abs(len(a) - len(b)) > 2:
        return False
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common >= short - 1


def _find_phrase(hay: list[tuple[str, int, int]], needle: list[str]) -> tuple[int, int] | None:
    """The character span of the first contiguous window of ``hay`` whose words
    are ``needle`` (word by word, :func:`_same_word`), or ``None``."""
    k = len(needle)
    if not k or k > len(hay):
        return None
    for i in range(len(hay) - k + 1):
        if all(_same_word(hay[i + j][0], needle[j]) for j in range(k)):
            return hay[i][1], hay[i + k - 1][2]
    return None


def _answer_of(new_info: Any) -> str:
    if isinstance(new_info, dict):
        answer = new_info.get("answer")
        return answer if isinstance(answer, str) else ""
    return ""


def enforce_skill_names(ops: Sequence[Any], *, profile: Any, new_info: Any, source: str) -> list[Any]:
    """The ops with every new skill named in the person's own words (module doc)."""
    ops = list(ops)
    answer = _answer_of(new_info)
    if source not in ANSWER_SOURCES or not answer.strip():
        return ops
    hay = _words(answer)
    skills = list(getattr(profile, "skills", None) or [])
    held_ids = {str(getattr(s, "id", "") or "") for s in skills} - {""}
    held_words = [w for s in skills for w, _a, _b in _words(getattr(s, "name", ""))]

    out: list[Any] = []
    for op in ops:
        if not isinstance(op, UpsertSkill) or (op.target and op.target in held_ids):
            out.append(op)
            continue
        name = [w for w, _a, _b in _words(op.name)]
        if _find_phrase(hay, name) is not None:
            out.append(op)
            continue
        own = [w for w in name if not any(_same_word(w, h) for h in held_words)]
        if own and len(own) < len(name) and _find_phrase(hay, own) is not None:
            out.append(op)
            continue
        said = [w for w, _a, _b in _words(op.said)]
        span = _find_phrase(hay, said) if 0 < len(said) <= MAX_SAID_WORDS else None
        if span is not None:
            phrase = unicodedata.normalize("NFC", answer)[span[0]:span[1]]
            logger.info(
                "skill_names: %r is not the person's word — stored as their own phrase %r (MD-Q15)",
                op.name, phrase,
            )
            out.append(op.model_copy(update={"name": phrase}))
            continue
        logger.info(
            "skill_names: %r is not the person's word and no own phrase was given — "
            "no skill written (MD-Q15)", op.name,
        )
    return out
