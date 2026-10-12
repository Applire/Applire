# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — flatten a delivered CV / letter into judge-ready text items.

Every scorer reads the same list of :class:`Item` objects, so a ``loc`` printed
in an issue record means the same thing for the judge, the attribution pass and
the report. The ``loc`` vocabulary (stable across runs, index = position in the
DELIVERED document)::

    cv.summary[2]                 sentence 2 of the summary
    cv.work[2].role               job title (an L exemption candidate)
    cv.work[2].bullets[1]         a bullet
    cv.work[0].industry_context   a vault-verbatim role fact (joined in compose)
    cv.work[0].budget_managed
    cv.work[1].projects[0].name   nested project (US187)
    cv.work[1].projects[0].bullets[2]
    cv.skills[5]                  a skill chip
    cv.projects[0].bullets[1]     standalone project
    cv.education[0].degree / .field
    cv.certifications[0].name
    cv.languages[1].language / .level
    letter.header.address
    letter.recipient.title / .address / .date
    letter.signature.closing
    letter.body[3]                a whole paragraph (``sentences=False``)
    letter.body[3][1]             sentence 1 of paragraph 3 (``sentences=True``,
                                  the shape the Oracle's own locations use)

Which fields reach the page is read off ``services/cv.py::assemble_tailored_cv``
and ``_compose_document``: the writer's PROSE is summary, per-role bullets,
nested/standalone projects and skills; everything else (role, dates, education,
languages, certifications, ``industry_context``/``budget_managed``) is carried
verbatim from the vault by deterministic code. Names of people, employers and
institutions, e-mail addresses, phones, dates and ``team_size`` are not text
items: they are proper nouns or numbers and no class of the metric (Q-1) can
apply to them.

The same flattener reads a model DRAFT (``work`` keyed by vault id instead of
``work_history``) via ``index_by_id`` so draft and delivered items share a
``loc`` vocabulary.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #

#: Item kinds the prose classes (S, D, U-del) operate on. Everything else is a
#: vault-verbatim or structural field (kept for L, which judges every item).
PROSE_KINDS = frozenset({"summary", "bullet", "skill", "sentence"})


@dataclass(frozen=True)
class Item:
    """One text item of a document with a stable ``loc``."""

    loc: str
    text: str
    doc: str  # "cv" | "letter"
    kind: str
    parent: str | None = None  # paragraph loc for a letter sentence

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def select(items: Iterable[Item], kinds: Iterable[str]) -> list[Item]:
    wanted = set(kinds)
    return [i for i in items if i.kind in wanted]


# --------------------------------------------------------------------------- #
# Sentence splitter
# --------------------------------------------------------------------------- #

# Tokens (lower-case, no trailing dot) that end in "." without ending a sentence.
_ABBREVIATIONS = frozenset(
    """
    z b zb d h dh u a ua usw etc bzw ggf ca vgl evtl inkl exkl max min mind ggü
    nr str tel fax abt dr prof dipl ing mag lic jun sen mio mrd tsd approx
    e g eg ie i vs cf mr mrs ms dr st inc ltd co corp no vol fig sec dept est
    ff ca p pp ref abb gem sog bspw insb ebd
    """.split()
)
_MONTHS = frozenset(
    """januar februar märz maerz april mai juni juli august september oktober
    november dezember jan feb mär mar apr jun jul aug sep sept okt oct nov dez dec
    january february march may june july october december""".split()
)
_BOUNDARY = re.compile(r"([.!?…]+[\"'”’)\]]*)(\s+)")
_TOKEN_BEFORE = re.compile(r"([^\s]+)$")


def split_sentences(text: str) -> list[str]:
    """Split a paragraph into sentences, German/English aware.

    Deterministic and conservative: a boundary needs terminal punctuation,
    whitespace, and a next token that starts with an upper-case letter, a digit
    or an opening quote. It is NOT a boundary after a known abbreviation
    ("z. B.", "ca.", "Dr."), a single initial, a German ordinal date ("am 3.
    Mai"), a decimal, or a list marker. Whitespace runs are collapsed.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    sentences: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        end = m.end(1)
        rest = text[m.end():]
        if not rest:
            break
        nxt = rest[0]
        if not (nxt.isupper() or nxt.isdigit() or nxt in "\"'„“‚‘»«(["):
            continue
        before = text[start:end]
        punct = m.group(1)
        if punct.startswith("."):
            tok_m = _TOKEN_BEFORE.search(text[start:m.start(1)])
            tok = tok_m.group(1).lower().strip("\"'„“(") if tok_m else ""
            if tok in _ABBREVIATIONS or (len(tok) == 1 and tok.isalpha()):
                continue
            if tok.isdigit() and len(tok) <= 2:
                nxt_word = re.match(r"[\wäöüÄÖÜß]+", rest)
                if nxt_word and nxt_word.group(0).lower() in _MONTHS:
                    continue  # "am 3. Mai"
            if re.search(r"\b[a-z]\.[a-z]$", tok):  # "e.g" / "z.B" glued form
                continue
        sentences.append(before.strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


# --------------------------------------------------------------------------- #
# Normalisation and fuzzy presence
# --------------------------------------------------------------------------- #

_QUOTES = str.maketrans({"„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "–": "-", "—": "-"})
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def norm(text: str) -> str:
    """Canonical comparison form: NFKC, case-folded, punctuation -> single spaces."""
    t = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).casefold()
    return _NON_WORD.sub(" ", t).strip()


def tokens(text: str) -> list[str]:
    n = norm(text)
    return n.split() if n else []


class TextIndex:
    """Fuzzy 'is this text present in that body of text' lookups.

    Built once per draft / delivered document. Short texts (< 4 tokens: skill
    chips) match only exactly or at ratio >= 0.92 against a single item — a
    substring test would find "SAP" in every bullet. Longer texts also match as
    a whole-word substring of the joined body, or at ``threshold`` ratio against
    one item.
    """

    def __init__(self, texts: Iterable[str]):
        self.items: list[str] = []
        seen: set[str] = set()
        for t in texts:
            n = norm(t)
            if n and n not in seen:
                seen.add(n)
                self.items.append(n)
        self._set = set(self.items)
        self.blob = " " + " ".join(self.items) + " "

    def __len__(self) -> int:
        return len(self.items)

    def best_ratio(self, text: str) -> tuple[float, str | None]:
        n = norm(text)
        if not n or not self.items:
            return 0.0, None
        if n in self._set:
            return 1.0, n
        best, best_item = 0.0, None
        ln = len(n)
        for m in self.items:
            lm = len(m)
            if lm > 2.5 * ln or ln > 2.5 * lm:
                continue
            r = SequenceMatcher(None, n, m, autojunk=False).ratio()
            if r > best:
                best, best_item = r, m
        return best, best_item

    def contains(self, text: str, threshold: float = 0.9) -> bool:
        n = norm(text)
        if not n:
            return False
        if n in self._set:
            return True
        if len(n.split()) < 4:
            return self.best_ratio(text)[0] >= max(threshold, 0.92)
        if f" {n} " in self.blob:
            return True
        return self.best_ratio(text)[0] >= threshold

    def contains_word_sequence(self, text: str) -> bool:
        """Whole-word substring of the body (a chip still conveyed in prose)."""
        n = norm(text)
        return bool(n) and f" {n} " in self.blob


# --------------------------------------------------------------------------- #
# CV
# --------------------------------------------------------------------------- #


def _cv_work_list(cv: dict) -> list[dict]:
    work = cv.get("work_history")
    if work is None:
        work = cv.get("work")
    return [w for w in (work or []) if isinstance(w, dict)]


def index_by_id(cv: dict) -> dict[str, int]:
    """Map vault work-entry id -> position in the DELIVERED ``work_history``."""
    return {str(w.get("id")): i for i, w in enumerate(_cv_work_list(cv)) if w.get("id")}


def flatten_cv(cv: dict | None, *, index_map: dict[str, int] | None = None) -> list[Item]:
    """Flatten a delivered ``tailored_data`` (or a model prose draft) into items.

    ``index_map`` (vault id -> delivered index) lets a draft keyed by id share
    the delivered ``loc`` vocabulary; without it entries are indexed by their
    position in the draft.
    """
    if not isinstance(cv, dict):
        return []
    out: list[Item] = []

    def add(loc: str, text: Any, kind: str) -> None:
        t = _s(text)
        if t:
            out.append(Item(loc, t, "cv", kind))

    for i, sent in enumerate(split_sentences(_s(cv.get("summary")))):
        add(f"cv.summary[{i}]", sent, "summary")

    def project_items(prefix: str, projects: Any) -> None:
        for j, p in enumerate(projects or []):
            if not isinstance(p, dict):
                continue
            add(f"{prefix}[{j}].name", p.get("name"), "project_name")
            for k, b in enumerate(p.get("bullets") or []):
                add(f"{prefix}[{j}].bullets[{k}]", b, "bullet")

    for pos, w in enumerate(_cv_work_list(cv)):
        idx = pos
        if index_map is not None and w.get("id") is not None:
            idx = index_map.get(str(w.get("id")), pos)
        base = f"cv.work[{idx}]"
        add(f"{base}.role", w.get("role"), "role")
        add(f"{base}.industry_context", w.get("industry_context"), "fact")
        add(f"{base}.budget_managed", w.get("budget_managed"), "fact")
        for k, b in enumerate(w.get("bullets") or []):
            add(f"{base}.bullets[{k}]", b, "bullet")
        project_items(f"{base}.projects", w.get("projects"))

    project_items("cv.projects", cv.get("projects"))
    for i, s in enumerate(cv.get("skills") or []):
        add(f"cv.skills[{i}]", s, "skill")
    for i, e in enumerate(cv.get("education") or []):
        if isinstance(e, dict):
            add(f"cv.education[{i}].degree", e.get("degree"), "degree")
            add(f"cv.education[{i}].field", e.get("field"), "field")
    for i, c in enumerate(cv.get("certifications") or []):
        if isinstance(c, dict):
            add(f"cv.certifications[{i}].name", c.get("name"), "certification")
    for i, lg in enumerate(cv.get("languages") or []):
        if isinstance(lg, dict):
            add(f"cv.languages[{i}].language", lg.get("language"), "language")
            add(f"cv.languages[{i}].level", lg.get("level"), "language_level")
    return out


# --------------------------------------------------------------------------- #
# Letter
# --------------------------------------------------------------------------- #


def letter_paragraphs(letter: dict | None) -> list[str]:
    if not isinstance(letter, dict):
        return []
    body = letter.get("body")
    paras = body.get("paragraphs") if isinstance(body, dict) else body
    return [p for p in (paras or []) if isinstance(p, str)]


def flatten_letter(letter: dict | None, *, sentences: bool = True) -> list[Item]:
    """Flatten a delivered ``letter_data``.

    ``sentences=True`` (L, D, S) puts each sentence of each paragraph at
    ``letter.body[p][s]``; ``False`` yields one item per paragraph at
    ``letter.body[p]``.
    """
    if not isinstance(letter, dict):
        return []
    out: list[Item] = []

    def add(loc: str, text: Any, kind: str, parent: str | None = None) -> None:
        t = _s(text)
        if t:
            out.append(Item(loc, t, "letter", kind, parent))

    header = letter.get("header") if isinstance(letter.get("header"), dict) else {}
    recipient = letter.get("recipient") if isinstance(letter.get("recipient"), dict) else {}
    signature = letter.get("signature") if isinstance(letter.get("signature"), dict) else {}
    add("letter.header.address", header.get("address"), "header")
    add("letter.recipient.title", recipient.get("title"), "header")
    add("letter.recipient.address", recipient.get("address"), "header")
    add("letter.recipient.date", recipient.get("date"), "header")
    for p, para in enumerate(letter_paragraphs(letter)):
        ploc = f"letter.body[{p}]"
        if not sentences:
            add(ploc, para, "paragraph")
            continue
        for s, sent in enumerate(split_sentences(para)):
            add(f"{ploc}[{s}]", sent, "sentence", parent=ploc)
    add("letter.signature.closing", signature.get("closing"), "header")
    return out


def flatten_document(doc: str, payload: dict | None, **kw: Any) -> list[Item]:
    return flatten_cv(payload, **kw) if doc == "cv" else flatten_letter(payload)


def items_as_jsonl(items: Iterable[Item]) -> str:
    """The judge-facing rendering: one JSON object per line, ``loc`` + ``text``."""
    import json

    return "\n".join(json.dumps({"loc": i.loc, "text": i.text}, ensure_ascii=False) for i in items)
