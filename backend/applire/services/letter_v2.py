# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Letter pipeline v2 (ADR-094 clause L, US369 / #779): the model writes, code checks.

Everything here is a FACT computed by code (ADR-062 clause 1) or a field whose
truth is a vault / user / system fact. Nothing in this module rewrites a sentence
the model wrote.

* :func:`letter_pipeline` — the ``LETTER_PIPELINE`` switch (``v2`` default since
  ruling W3-7, 2026-10-18; ``v1`` opts out, documented in ``.env.example``).
* :func:`letter_language_fact` — which body sentences are not written in the
  document's language. THE per-item predicate is
  ``utils.language_detection.item_language_mismatch`` (ADR-066: the same one the
  CV's ``document-language`` check reads); this module only enumerates the
  letter's sentences for it.

  Why it exists (E066 baseline 2026-10-12, provider-less per-round attribution):
  ``ministral-8b`` wrote 17 of 18 English-target letters in German from the
  writer's FIRST draft, every later round kept it, and in the 18th the writer
  wrote English and the pre-verdict condense switched it to German. The letter
  pipeline had no language control at all, and the writer's system prompt opened
  "(German cover letter)". The prompt half of the fix lives in
  ``prompts/cover_letter.build_system_prompt``; this is the check half.
* :func:`letter_language_reviewer_prompt_fn` / :func:`letter_language_issues_fn`
  — the fact handed to the reviewer every round and, as blocking issues, to the
  corrector (ADR-076 clause 5's transport). Neither changes text.
* :func:`code_owned_fields` — the letter fields that are facts, not prose:
  the sender header from the vault's ``personal_info``. On the same baseline the
  weak tier invented a street address ("Musterstraße 123, 70563 Stuttgart") in
  5/32 letters and misspelled the candidate's name in 9/32 — fields the model was
  asked to copy and should never have been asked for.
* :func:`recorded_name_forms` / :func:`render_recorded_name_forms_block` — #709 /
  #716 letter half, RULING W1-2: the recorded alternate names of the candidate's
  own employers, titles, institutions and degrees, as source material, so the
  model can pick the recorded form that fits the document language. An alias does
  not record its language, so the pick is the model's judgement; WHICH forms are
  recorded is a fact.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

#: The switch's environment variable (declared in ``settings_registry``).
LETTER_PIPELINE_ENV = "LETTER_PIPELINE"

#: How the document language is named to the model. Written in English like the
#: rest of the prompt, with the language's own name beside it.
LANGUAGE_NAMES: dict[str, str] = {
    "de": "GERMAN (Deutsch)",
    "en": "ENGLISH",
}


def letter_pipeline() -> str:
    """``"v1"`` when ``LETTER_PIPELINE=v1``, else ``"v2"`` (default; any other value).

    v2 is the default since ruling W3-7 (founder 2026-10-18); v1 stays the
    one-setting opt-out until its tails are deleted. Read per call, not at import:
    the bench and the tests set it per run, and a generation reads it exactly once
    (``_render_cover_letter_body``)."""
    raw = os.environ.get("LETTER_PIPELINE", "v2")
    return "v1" if (raw or "").strip().lower() == "v1" else "v2"


def language_name(language: str | None) -> str:
    code = (language or "").strip().lower()
    return LANGUAGE_NAMES.get(code, code.upper() or "GERMAN (Deutsch)")


# ── the letter-language fact ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ForeignSentence:
    """One body sentence not written in the document language."""

    paragraph_index: int
    sentence: str


@dataclass(frozen=True)
class LetterLanguageFact:
    """Which body sentences are written in another language than the document's.

    ``checked`` counts only the sentences the predicate is willing to classify
    (``item_language_mismatch``'s evidence floor: ≥ 4 words), so a salutation or a
    two-word line is neither foreign nor counted."""

    language: str
    checked: int
    foreign: tuple[ForeignSentence, ...]

    @property
    def clean(self) -> bool:
        return not self.foreign

    @property
    def majority_foreign(self) -> bool:
        """More than half of the classifiable sentences are foreign — the letter
        as a whole is in the wrong language, not a stray sentence in it."""
        return bool(self.checked) and 2 * len(self.foreign) > self.checked


def _body_paragraphs(letter_data: dict[str, Any] | None) -> list[Any]:
    body = (letter_data or {}).get("body") if isinstance(letter_data, dict) else None
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    return paragraphs if isinstance(paragraphs, list) else []


def letter_language_fact(letter_data: dict[str, Any] | None, language: str | None) -> LetterLanguageFact:
    """Enumerate the letter's body sentences for THE per-item language predicate.

    Pure, deterministic, tolerant of malformed shapes (an unreadable body is
    "nothing checked", never a finding). DE/EN only, like the detector."""
    from applire.services.limit_grounding import split_sentences
    from applire.utils.language_detection import (
        ITEM_LANGUAGE_MIN_WORDS,
        _ITEM_WORD_RE,
        item_language_mismatch,
    )

    lang = (language or "").strip().lower()
    if lang not in LANGUAGE_NAMES:
        return LetterLanguageFact(language=lang, checked=0, foreign=())
    checked = 0
    foreign: list[ForeignSentence] = []
    for index, paragraph in enumerate(_body_paragraphs(letter_data)):
        if not isinstance(paragraph, str):
            continue
        for sentence in split_sentences(paragraph):
            text = sentence.strip()
            if len(_ITEM_WORD_RE.findall(text)) < ITEM_LANGUAGE_MIN_WORDS:
                continue
            checked += 1
            if item_language_mismatch(text, lang):
                foreign.append(ForeignSentence(paragraph_index=index, sentence=text))
    return LetterLanguageFact(language=lang, checked=checked, foreign=tuple(foreign))


#: How many foreign sentences one block / one round quotes. The count is always
#: stated in full; the quotes are bounded so a whole-letter mismatch does not
#: re-print the letter into the reviewer prompt.
_QUOTE_LIMIT = 3


def render_letter_language_block(fact: LetterLanguageFact) -> str:
    """The reviewer-facing fact. ``""`` when the letter is clean (silence over a
    block that says "all fine" — the convention of every other letter wrapper)."""
    if fact.clean:
        return ""
    name = language_name(fact.language)
    lines = [
        "LETTER LANGUAGE CHECK (computed by code on the draft under review — a fact, "
        f"not an opinion): the document language is {name}. "
        f"{len(fact.foreign)} of {fact.checked} body sentences are NOT written in {name}.",
    ]
    if fact.majority_foreign:
        lines.append(
            f"The letter as a whole is written in the wrong language. That is a "
            f"blocking issue: set approved=false and ask for the whole letter in {name}, "
            "same facts, same structure."
        )
    else:
        lines.append(
            f"Each of these sentences is a blocking issue: ask for it in {name}, "
            "same facts. Names (employers, titles, products, certifications) are not "
            "sentences and are never part of this finding."
        )
    for f in fact.foreign[:_QUOTE_LIMIT]:
        lines.append(f'  - "{f.sentence}"')
    if len(fact.foreign) > _QUOTE_LIMIT:
        lines.append(f"  (+{len(fact.foreign) - _QUOTE_LIMIT} more)")
    return "\n".join(lines)


def letter_language_reviewer_prompt_fn(base_fn: Callable[[str, dict], str], language: str | None):
    """Compose the language fact onto a reviewer prompt fn ``(source, draft) -> str``.

    Same composition as the letter's other deterministic reviewer wrappers
    (coverage, word floor, figure ownership): computed on the draft THIS round
    reviews, appended after the base prompt, no LLM call, no new pass."""

    def fn(source: str, draft: dict) -> str:
        prompt = base_fn(source, draft)
        block = render_letter_language_block(letter_language_fact(draft, language))
        return f"{prompt}\n\n{block}" if block else prompt

    return fn


def letter_language_issues_fn(
    language: str | None,
    compose_fn: Callable[[dict], dict] | None = None,
):
    """Bind the language fact to ``review_and_refine``'s ``signal_issues_fn``.

    Each round's own draft (composed first when ``compose_fn`` is given, so the
    terminal mount reads the delivered artefact) yields ONE blocking issue when
    any sentence is foreign — one issue, not one per sentence: a whole-letter
    mismatch is one defect with one remedy, and an unbounded deterministic demand
    is exhaustion fuel (the reason ``limit_signal_issues_fn`` caps itself)."""
    from applire.prompts.review_severity import SEVERITY_BLOCKING
    from applire.services.review_issues import ReviewIssue

    def fn(draft: dict) -> list:
        subject = compose_fn(draft) if compose_fn is not None else draft
        fact = letter_language_fact(subject, language)
        if fact.clean:
            return []
        name = language_name(fact.language)
        quotes = " ".join(f'"{f.sentence}"' for f in fact.foreign[:_QUOTE_LIMIT])
        scope = (
            f"The whole letter is not written in {name}: rewrite every sentence in {name}, "
            "keeping every fact, figure and paragraph."
            if fact.majority_foreign
            else f"Rewrite these sentence(s) in {name}, same facts: {quotes}"
        )
        return [
            ReviewIssue(
                text=(
                    f"Letter language (computed by code): {len(fact.foreign)} of "
                    f"{fact.checked} body sentences are not in {name}. {scope}"
                ),
                severity=SEVERITY_BLOCKING,
            )
        ]

    return fn


def combine_signal_fns(*fns):
    """One ``signal_issues_fn`` from several (``review_and_refine`` takes one).

    ``None`` entries are skipped; with nothing left the result is ``None`` so the
    call site's default behaviour is unchanged."""
    live = [f for f in fns if f is not None]
    if not live:
        return None
    if len(live) == 1:
        return live[0]

    def fn(draft: dict) -> list:
        out: list = []
        for f in live:
            out.extend(f(draft))
        return out

    return fn


# ── code-owned fields ────────────────────────────────────────────────────────


def vault_sender(profile_json: dict[str, Any] | None, cv_data: dict[str, Any] | None) -> dict[str, Any]:
    """The sender header, from the vault (``personal_info``), falling back to the
    tailored CV's ``contact`` field by field. The address line is the vault's
    postal address when it records one, else its location — never invented.

    A FACT: field reads only. Missing stays ``None`` (the template omits it)."""
    pi = (profile_json or {}).get("personal_info") if isinstance(profile_json, dict) else None
    pi = pi if isinstance(pi, dict) else {}
    contact = (cv_data or {}).get("contact") if isinstance(cv_data, dict) else None
    contact = contact if isinstance(contact, dict) else {}

    def pick(*values: Any) -> str | None:
        for v in values:
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    return {
        "name": pick(pi.get("name"), contact.get("name")),
        "address": pick(pi.get("address"), pi.get("location"), contact.get("location")),
        "phone": pick(pi.get("phone"), contact.get("phone")),
        "email": pick(pi.get("email"), contact.get("email")),
    }


def apply_code_owned_fields(
    letter_data: dict[str, Any],
    *,
    profile_json: dict[str, Any] | None,
    cv_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Set the v2 code-owned SENDER fields on a draft (returns a new dict).

    ``header`` and ``signature.name`` are the candidate's own record. The other
    code-owned fields (date, sign-off label, the recipient the user typed) are
    already set by the composition site's existing steps; this adds the one the
    v1 pipeline left to the model. Applied to the writer's draft BEFORE the first
    review round and again at every composition, so the reviewer reads the header
    that ships and a corrector cannot re-type it."""
    import copy

    data = copy.deepcopy(letter_data) if isinstance(letter_data, dict) else {}
    sender = vault_sender(profile_json, cv_data)
    # Code-owned means the model's value is never used — a phone the vault does
    # not record is absent, not whatever the model typed.
    header = data.get("header")
    header = dict(header) if isinstance(header, dict) else {}
    header.update(sender)
    # The letter schema types ``header.name`` / ``header.address`` and
    # ``signature.name`` as strings with "" meaning "no line" (``LetterHeader`` /
    # ``LetterSignature``); phone and email are Optional. A vault without a name
    # composed ``None`` and failed ``LetterData`` validation in the .docx export and
    # its ATS audit (W3-7 flip test, 2026-10-18). Missing stays missing — the empty
    # string, never a name the model typed.
    for key in ("name", "address"):
        if header.get(key) is None:
            header[key] = ""
    data["header"] = header
    signature = data.get("signature")
    signature = dict(signature) if isinstance(signature, dict) else {}
    signature["name"] = sender.get("name") or ""
    data["signature"] = signature
    return data


# ── recorded name forms (#709 / #716 letter half, RULING W1-2) ────────────────


@dataclass(frozen=True)
class NameForms:
    """One recorded name and its recorded alternate forms."""

    kind: str  # "role" | "company" | "organization" | "institution" | "degree"
    stored: str
    aliases: tuple[str, ...]
    owner: str  # the entity a reader recognises it by ("" for the employer itself)


def _aliases(entry: dict, key: str, stored: str) -> tuple[str, ...]:
    out: list[str] = []
    for a in entry.get(key) or []:
        if isinstance(a, str) and a.strip() and a.strip() != stored and a.strip() not in out:
            out.append(a.strip())
    return tuple(out)


def recorded_name_forms(profile_json: dict[str, Any] | None) -> list[NameForms]:
    """Every recorded name that HAS at least one recorded alternate form.

    Work (role, company), volunteering (role, organization), education
    (institution, degree). A name with no alias contributes nothing — there is no
    choice to make, and the existing rules already say "use the recorded name".
    Pure field reads (ADR-062: a FACT)."""
    if not isinstance(profile_json, dict):
        return []
    out: list[NameForms] = []
    for section, org_key, org_alias_key in (
        ("work_experience", "company", "company_aliases"),
        ("volunteer_activities", "organization", "organization_aliases"),
    ):
        for entry in profile_json.get(section) or []:
            if not isinstance(entry, dict):
                continue
            org = (entry.get(org_key) or "").strip()
            role = (entry.get("role") or "").strip()
            if org:
                aliases = _aliases(entry, org_alias_key, org)
                if aliases:
                    out.append(NameForms(org_key, org, aliases, ""))
            if role:
                aliases = _aliases(entry, "role_aliases", role)
                if aliases:
                    out.append(NameForms("role", role, aliases, org))
    for entry in profile_json.get("education") or []:
        if not isinstance(entry, dict):
            continue
        inst = (entry.get("institution") or "").strip()
        degree = (entry.get("degree") or "").strip()
        if inst:
            aliases = _aliases(entry, "institution_aliases", inst)
            if aliases:
                out.append(NameForms("institution", inst, aliases, ""))
        if degree:
            aliases = _aliases(entry, "degree_aliases", degree)
            if aliases:
                out.append(NameForms("degree", degree, aliases, inst))
    return out


_KIND_LABEL = {
    "role": "job title",
    "company": "employer",
    "organization": "organisation",
    "institution": "institution",
    "degree": "degree",
}


def render_recorded_name_forms_block(forms: list[NameForms], language: str | None) -> str:
    """The RECORDED NAME FORMS block for the writer, reviewer and corrector.

    ``""`` when the vault records no alternate form (most vaults: the block then
    changes no prompt at all)."""
    if not forms:
        return ""
    name = language_name(language)
    lines = [
        "RECORDED NAME FORMS (the candidate's own record — ground truth). Each name "
        "below is recorded in more than one form. When the letter names it, use ONE "
        f"of its recorded forms, exactly as written, choosing the form written in {name} "
        "where one exists. Never translate a name yourself and never use a form that is "
        "not listed:",
    ]
    for f in forms:
        where = f" (at {f.owner})" if f.owner else ""
        forms_txt = " | ".join(f'"{x}"' for x in (f.stored, *f.aliases))
        lines.append(f"  - {_KIND_LABEL.get(f.kind, f.kind)}{where}: {forms_txt}")
    return "\n".join(lines)


# ── W2-3: recorded-name membership for employer / institution spans ─────────
#: Leading tokens that never identify ONE institution (the Oracle's company list
#: already covers "Gruppe", "Group", …).
_GENERIC_INSTITUTION_TOKENS = frozenset({
    "university", "universität", "universitaet", "hochschule", "fachhochschule",
    "college", "school", "schule", "institute", "institut", "technische",
    "technical", "academy", "akademie", "the",
})


@dataclass(frozen=True)
class NameMembership:
    """A located employer/institution span that is not a recorded name (W2-3)."""

    kind: str  # "company" | "institution"
    span: str
    stored: str
    aliases: tuple[str, ...]

    def text(self) -> str:
        options = " or one of " + ", ".join(f'"{a}"' for a in self.aliases) if self.aliases else ""
        return (
            f'"{self.span}" is not a name the candidate recorded for {self.stored}; '
            f'use "{self.stored}"{options}.'
        )


def letter_name_findings(
    letter_data: dict[str, Any] | None, profile_json: dict[str, Any] | None
) -> list[NameMembership]:
    """RULING W2-3: an employer or institution named in the letter body must be a
    recorded name (the stored name or one of its aliases). Spans are located by the
    Oracle's anchor machinery (``oracle.extract``: exact name, legal-form-stripped
    core name, distinctive leading token) — exact surface forms only, no title-span
    guessing; roles and degrees are not located in prose. A span located only by
    the looser passes is checked with W1's ``pick_recorded``; a non-member is a
    finding. Pure string facts (ADR-062 cl. 1); nothing is rewritten."""
    from applire.services.cv_v2 import norm_name, pick_recorded, recorded_names
    from applire.services.oracle.extract import (
        _GENERIC_COMPANY_TOKENS,
        _core_company_name,
        _distinctive_token_candidates,
        _normalize_punct,
    )

    if not isinstance(profile_json, dict):
        return []
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not isinstance(paragraphs, list):
        return []
    text = _normalize_punct(" \n ".join(p for p in paragraphs if isinstance(p, str)))
    if not text.strip():
        return []

    # (kind, entity id, stored, aliases) per recorded entity
    entities: list[tuple[str, str, str, list[str]]] = []
    for section, key, alias_key, kind in (
        ("work_experience", "company", "company_aliases", "company"),
        ("education", "institution", "institution_aliases", "institution"),
    ):
        by_name: dict[str, tuple[str, str, str, list[str]]] = {}
        for e in profile_json.get(section) or []:
            if not isinstance(e, dict):
                continue
            stored = (e.get(key) or "").strip()
            if not stored:
                continue
            aliases = [a for a in (e.get(alias_key) or []) if isinstance(a, str)] if isinstance(e.get(alias_key), list) else []
            # several roles at ONE employer are one entity (else the Oracle's
            # distinctive-token pass would drop the shared token as ambiguous)
            k = norm_name(stored)
            if k in by_name:
                by_name[k][3].extend(a for a in aliases if a not in by_name[k][3])
            else:
                by_name[k] = (kind, f"{kind}:{len(by_name)}", stored, list(aliases))
        entities.extend(by_name.values())

    def _find(form: str, *, proper_noun: bool = False) -> str | None:
        flags = 0 if proper_noun else re.IGNORECASE
        m = re.search(r"\b" + re.escape(_normalize_punct(form)) + r"\b", text, flags)
        return m.group(0) if m else None

    loose = [(_core_company_name(s), eid) for _k, eid, s, _a in entities]
    tokens = dict(
        (eid, tok) for tok, eid in _distinctive_token_candidates(loose)
        if tok.casefold() not in _GENERIC_INSTITUTION_TOKENS
        and tok.casefold() not in _GENERIC_COMPANY_TOKENS
    )
    out: list[NameMembership] = []
    seen: set[tuple[str, str]] = set()
    for kind, eid, stored, aliases in entities:
        if any(_find(n) for n in recorded_names(stored, aliases)):
            continue  # a recorded form is named — member
        core = _core_company_name(stored)
        span = _find(core) if core != stored else None
        if span is None and eid in tokens and tokens[eid][:1].isupper():
            # A single token is a NAME only as the capitalised proper noun: the
            # Oracle's anchor tolerates "kleine" (fail-open for attribution), but
            # here a hit is a blocking finding — an ordinary word must not be one
            # (measured: "kleine Gruppe (…)" fired on 3 of 28 v2 letters).
            span = _find(tokens[eid], proper_noun=True)
        if span is None:
            continue
        _name, member = pick_recorded(span, stored, aliases)
        if not member:
            # RULING W2-5: a recorded name WITHOUT its legal form is the same name —
            # the Oracle's own legal-form handling (one implementation, ADR-066)
            # strips both sides before W1's membership comparison.
            # (the located span never carries a legal form: a span that did
            # would contain the stored name itself, located as a member above)
            _name, member = pick_recorded(
                span,
                _core_company_name(stored),
                [_core_company_name(a) for a in recorded_names(stored, aliases)[1:]],
            )
        if member or (eid, span.casefold()) in seen:
            continue
        seen.add((eid, span.casefold()))
        out.append(NameMembership(kind, span, stored, tuple(
            a for a in recorded_names(stored, aliases) if a != stored
        )))
    return out
