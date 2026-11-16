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

**MD-Q17 (adversarial finding F7): the words must be a CLAIM, not just present.**
Every kept window — the name, the own words of a composition, the ``said``
phrase — lies inside ONE sentence of the answer, and that sentence is
affirmative: it holds no negation ("I never did penetration testing") and no
wish or hypothetical ("Ich würde gern Kubernetes lernen"). The stored name
holds at least one content word ("habe das nie" holds none). A composition with
a held skill's words ("SAP" + "MM") needs the op's ``said`` phrase in an
affirmative sentence and the own words inside that same sentence. The word
lists are lexical on purpose: they can only BLOCK (no skill is stored; the
bullets keep what the answer said), never let a name through.
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
#: A sentence ends at ".", "!", "?", ";" or a line break followed by space or
#: the end ("4.0" and "z.B." do not end one: no space after the dot).
_SENTENCE_END = re.compile(r"[.!?;](?=\s|$)|\n")
#: Negation words (German, English): a sentence holding one is a denial.
_NEGATORS = frozenset({
    "nie", "niemals", "nicht", "nichts", "kein", "keine", "keinen", "keinem", "keiner",
    "keines", "ohne", "weder", "niemand", "nirgends",
    "no", "not", "never", "none", "nor", "neither", "without", "nothing", "nobody",
    "cannot", "didn", "doesn", "don", "isn", "wasn", "aren", "weren", "hasn", "haven",
    "hadn", "couldn", "wouldn", "shouldn",
})
#: Wish / hypothetical / plan words: a sentence holding one claims nothing yet.
_WISH = frozenset({
    "würde", "würden", "möchte", "möchten", "gern", "gerne", "hätte", "hätten", "wäre",
    "wären", "könnte", "könnten", "wünsche", "hoffe", "will", "wollen", "vielleicht", "lernen",
    # never "plan"/"planning"/"planen": a noun in "materials planning" (replay)
    "would", "wish", "hope", "want", "wants", "wanted", "could", "might", "maybe", "someday",
    "learn",
})
#: Words that name no capability: a stored name needs one word outside this set.
_FUNCTION_WORDS = _NEGATORS | _WISH | frozenset({
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "mir", "mein", "meine", "meinen",
    "meinem", "meiner", "uns", "unser", "unsere", "das", "der", "die", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "einer", "und", "oder", "aber", "auch", "mit", "von",
    "für", "bei", "im", "in", "an", "am", "auf", "aus", "zu", "zum", "zur", "da", "dort",
    "so", "wie", "was", "habe", "hat", "haben", "hatte", "hatten", "bin", "ist", "sind",
    "war", "waren", "wurde", "wurden", "werde", "wird", "dabei", "dann", "noch", "schon",
    "sehr", "gemacht", "getan",
    "i", "me", "my", "we", "our", "you", "he", "she", "it", "they", "the", "a", "an", "and",
    "or", "but", "also", "with", "of", "for", "at", "in", "on", "to", "from", "by", "as",
    "that", "this", "there", "have", "has", "had", "do", "did", "does", "done", "is", "are",
    "was", "were", "be", "been", "very", "so",
})


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


def _sentences(answer: str) -> list[tuple[int, int, bool]]:
    """``(start, end, affirmative)`` per sentence of the answer: a sentence is
    affirmative when it holds no negation and no wish word."""
    raw = unicodedata.normalize("NFC", answer)
    out, start = [], 0
    for m in [*_SENTENCE_END.finditer(raw), None]:
        end = m.end() if m else len(raw)
        words = {w.casefold() for w in _WORD.findall(raw[start:end])}
        if raw[start:end].strip():
            out.append((start, end, not (words & _NEGATORS or words & _WISH)))
        start = end
    return out


def _claimed_phrase(
    hay: list[tuple[str, int, int]], needle: list[str], sentences: list[tuple[int, int, bool]],
) -> tuple[int, int] | None:
    """The character span of the first contiguous window of ``hay`` whose words
    are ``needle`` (word by word, :func:`_same_word`) and which lies inside ONE
    affirmative sentence (MD-Q17), or ``None``."""
    k = len(needle)
    if not k or k > len(hay):
        return None
    for i in range(len(hay) - k + 1):
        if all(_same_word(hay[i + j][0], needle[j]) for j in range(k)):
            a, b = hay[i][1], hay[i + k - 1][2]
            if any(s <= a and b <= e and ok for s, e, ok in sentences):
                return a, b
    return None


def _has_content_word(words: list[str]) -> bool:
    return any(w not in _FUNCTION_WORDS for w in words)


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
    sentences = _sentences(answer)
    skills = list(getattr(profile, "skills", None) or [])
    held_ids = {str(getattr(s, "id", "") or "") for s in skills} - {""}
    held_words = [w for s in skills for w, _a, _b in _words(getattr(s, "name", ""))]

    out: list[Any] = []
    for index, op in enumerate(ops):
        if not isinstance(op, UpsertSkill) or (op.target and op.target in held_ids):
            out.append(op)
            continue
        name = [w for w, _a, _b in _words(op.name)]
        if _has_content_word(name) and _claimed_phrase(hay, name, sentences) is not None:
            out.append(op)
            continue
        said = [w for w, _a, _b in _words(op.said)]
        span = (
            _claimed_phrase(hay, said, sentences)
            if 0 < len(said) <= MAX_SAID_WORDS and _has_content_word(said) else None
        )
        own = [w for w in name if not any(_same_word(w, h) for h in held_words)]
        # A composition (held words + the answer's own words) needs the
        # person's affirmative ``said`` and the own words in ITS sentence.
        said_sentence = [x for x in sentences if span is not None and x[0] <= span[0] and span[1] <= x[1]]
        if (
            span is not None and own and len(own) < len(name) and _has_content_word(own)
            and _claimed_phrase(hay, own, said_sentence) is not None
        ):
            out.append(op)
            continue
        if span is not None:
            phrase = unicodedata.normalize("NFC", answer)[span[0]:span[1]]
            # Never the skill name or the answer's words in the application
            # log (personal data): only the op position and word counts.
            logger.info(
                "skill_names: op %d's name is not the person's word — stored as their own "
                "%d-word phrase (MD-Q15)", index, len(said),
            )
            out.append(op.model_copy(update={"name": phrase}))
            continue
        logger.info(
            "skill_names: op %d's name (%d words) is not the person's word and no own phrase "
            "was given — no skill written (MD-Q15)", index, len(name),
        )
    return out
