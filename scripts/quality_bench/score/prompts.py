# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — the four judge prompts, Appendix A of the epic spec.

The wording of ``PREAMBLE`` and of the four defect definitions ``A1``…``A4`` is
the founder-reviewed text of SPEC Appendix A, copied verbatim. This module adds
only plumbing, and says where:

* ``FORMAT_NOTE`` — an output-format instruction (JSON only, cite ``loc``/ids
  exactly as given). No defect semantics.
* the ``user_*`` builders — the item-list rendering (one JSON object per line,
  ``{"loc", "text"}``), section headings named after the spec's own terms
  (DOCUMENT LANGUAGE, VAULT, JD, DELIVERED DOCUMENT, REMOVED passages).

A change to the defect wording itself belongs to the spec, not to this file.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .inputs import LANGUAGE_NAMES
from .items import Item, items_as_jsonl

PREAMBLE = (
    "You are an independent auditor measuring the quality of a job-application document "
    "produced by an automated system. You are not editing the document and not advising "
    "the candidate. You report defects of exactly one kind, defined below, each with its "
    "location and evidence. Report only what you can point to; when unsure, do not report. "
    "A missed defect costs less than an invented one, because invented findings make the "
    "measurement worthless."
)

# A.1 — L, language mix. ``{lang}`` is filled with the document language.
A1 = (
    "DEFECT: a text item written in a language other than the DOCUMENT LANGUAGE ({lang}). "
    "Judge every item listed, including short ones (skill entries, one-line facts, header "
    "fields). An item is in a language when its descriptive words are in that language.\n"
    "NOT a defect (report under `exempt` instead, not `issues`): proper nouns — employer, "
    "product, tool, technology, programme and institution names; job titles as held at the "
    "employer; official certificate and degree names; acronyms (ISO 9001, SAP PP, GxP, ECMO); "
    "established loanwords used in the document language's professional register "
    "(\"Stakeholder Management\", \"Lean\" in German).\n"
    "A mixed item (\"Leitung of the MES rollout\") is a defect.\n"
    "Return `{\"issues\":[{\"loc\",\"text\",\"detected_language\"}], "
    "\"exempt\":[{\"loc\",\"text\",\"reason\"}]}`."
)

# A.2 — U, underselling.
A2 = (
    "DEFECT: true, relevant evidence about the candidate that the document fails to convey "
    "or conveys weaker than the record supports. You receive the VAULT (the candidate's "
    "verified record), the JD's required and preferred items, the DELIVERED DOCUMENT, and — "
    "in part 1 — a list of REMOVED passages that an automated step deleted after the writer "
    "produced them.\n"
    "Part 1, for each REMOVED passage: is it supported by the vault (`grounded`) and does it "
    "bear on a JD item or the candidate's strongest evidence (`relevant`)? Only grounded AND "
    "relevant removals are defects.\n"
    "Part 2: list vault evidence that bears on a REQUIRED JD item and appears in neither the "
    "CV nor the letter (`omitted`), and document statements that state a vault fact weaker "
    "than the vault supports — a figure dropped, \"assisted\" for \"led\", a scope narrowed "
    "(`understated`). Do not report omissions explained by the page budget unless the omitted "
    "item is stronger evidence for a required item than something the document kept.\n"
    "Return `{\"removed\":[{\"id\",\"grounded\",\"relevant\",\"why\"}], "
    "\"omitted\":[{\"vault_ref\",\"jd_item\",\"why\"}], "
    "\"understated\":[{\"loc\",\"vault_ref\",\"why\"}]}`."
)

# A.3 — D, duplicates.
A3 = (
    "DEFECT: the same competence, fact or achievement stated more than once inside ONE "
    "document in different wording — an acronym beside its expansion (\"CSV\" / \"Computer "
    "System Validation\"), a translation beside its original (\"Große Sprachmodelle\" / "
    "\"Large Language Models\"), synonyms (\"Lean Management\" / \"Lean Manufacturing\" when "
    "the record shows one competence), the same achievement in two bullets. Related but "
    "distinct competences are NOT duplicates (\"ISO 9001\" / \"ISO 45001\"). The same fact "
    "in the CV and in the letter is NOT a defect.\n"
    "Return `{\"groups\":[{\"locs\":[...],\"texts\":[...],\"why\"}]}` — one group per "
    "competence; a group of k items counts k−1."
)

# A.4 — S, upselling.
A4 = (
    "DEFECT: a claim about the candidate that the VAULT does not support. Classify each "
    "defective claim as `unbacked` (no vault source), `inflated` (stronger than the source: "
    "a bigger figure, a higher role or scope, sole ownership of shared work, \"led\" for "
    "\"contributed\", completed for in-progress), or `misattributed` (true fact, wrong "
    "employer, role or period). A faithful translation or paraphrase of a vault fact is NOT "
    "a defect. Interpretive framing that adds no fact (\"which prepared me for…\") is NOT a "
    "defect. Statements about the hiring company are out of scope.\n"
    "Return `{\"issues\":[{\"loc\",\"claim\",\"kind\",\"vault_ref_or_null\",\"why\"}]}`."
)

# Plumbing only: output format and how identifiers are cited. No defect semantics.
FORMAT_NOTE = (
    "FORMAT: reply with a single JSON object exactly in the shape given above and nothing "
    "else (no markdown fence, no commentary). Cite `loc`, `id` and `vault_ref` values "
    "exactly as they appear in the input; a `vault_ref` is the path shown before the colon "
    "on a VAULT line. An empty list is a valid answer."
)

CLASS_TEXT = {"L": A1, "U": A2, "D": A3, "S": A4}


def language_label(language: str | None) -> str:
    lang = (language or "de").lower()
    return LANGUAGE_NAMES.get(lang, lang)


def system_prompt(cls: str, language: str | None = None) -> str:
    body = CLASS_TEXT[cls]
    if cls == "L":
        body = body.replace("{lang}", language_label(language))
    return f"{PREAMBLE}\n\n{body}\n\n{FORMAT_NOTE}"


def _lang_line(language: str | None) -> str:
    return f"DOCUMENT LANGUAGE: {language_label(language)} ({(language or 'de').lower()})"


def user_language(doc: str, language: str | None, items: Iterable[Item]) -> str:
    return (
        f"{_lang_line(language)}\nDOCUMENT: {doc}\n\n"
        f"ITEMS (one JSON object per line):\n{items_as_jsonl(items)}\n"
    )


def user_duplicates(doc: str, language: str | None, items: Iterable[Item]) -> str:
    return (
        f"{_lang_line(language)}\nDOCUMENT: {doc}\n\n"
        f"DELIVERED DOCUMENT — all items of this ONE document (one JSON object per line):\n"
        f"{items_as_jsonl(items)}\n"
    )


def user_upselling(doc: str, language: str | None, vault: str, claims: Iterable[Item]) -> str:
    return (
        f"{_lang_line(language)}\nDOCUMENT: {doc}\n\n"
        f"VAULT (path: text):\n{vault}\n\n"
        f"CLAIMS of the {doc} (one JSON object per line):\n{items_as_jsonl(claims)}\n"
    )


def user_underselling(
    language: str | None,
    vault: str,
    jd: str,
    cv_items: Iterable[Item],
    letter_items: Iterable[Item],
    removed: Iterable[dict[str, Any]],
) -> str:
    removed_lines = "\n".join(
        json.dumps({"id": r["id"], "doc": r["doc"], "text": r["text"]}, ensure_ascii=False) for r in removed
    )
    return (
        f"{_lang_line(language)}\n\n"
        f"VAULT (path: text):\n{vault}\n\n"
        f"JD (required and preferred items, each with its ledger row where one exists):\n{jd}\n\n"
        f"DELIVERED DOCUMENT — CV (one JSON object per line):\n{items_as_jsonl(cv_items) or '(none delivered)'}\n\n"
        f"DELIVERED DOCUMENT — LETTER (one JSON object per line):\n{items_as_jsonl(letter_items) or '(none delivered)'}\n\n"
        f"REMOVED passages (part 1; one JSON object per line):\n{removed_lines or '(none)'}\n"
    )
