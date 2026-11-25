# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which line of a held ``terminal-review`` card is a POINT the person decides, and which
is ADVICE that never holds the download (founder ruling DH-1, 2026-11-25, "truth holds";
main-session default MD-G32 for code facts).

One source for the producer (``terminal_review_outcome._check_notes``, which never hides
a point behind a "… and N more" line) and the hold (``review_signals``).

* A reviewer ``finding`` is advice only when the class call (``services/point_class``)
  classed it ``coverage`` or ``form``. A missing or unknown class is truth.
* A deterministic code fact (``kind: code``) is classed by its check KEY. The two lists
  below say what each check asserts. A key on neither list is a point (fails closed),
  and ``tests/unit/test_dh_fix2_point_rules.py`` fails until the key is put on one.
* The hold is MONOTONE: adding advice never releases a point. A truth-type code fact is a
  point whatever any finding's class is.
"""
from __future__ import annotations

#: Card lines that are always points: the Oracle/ATS truth keys, an audit that did not
#: re-check the shipped content, a review state that did not read it.
POINT_KEY_PREFIXES = ("oracle.", "audit.", "review.")

#: Code facts about TRUTH: the document states something the profile does not back as
#: written. Always a point.
TRUTH_CODE_KEYS: dict[str, str] = {
    "cv.identifier_not_in_profile": "a standard or certificate identifier the profile never states",
    "cv.skill_module_unbacked": "a skills entry joins a module the profile does not record",
    "cv.summary_posting_term": "the summary claims posting words the candidate never used or confirmed",
    "cv.skill_posting_term": "a skills entry in posting words the candidate never used or confirmed",
    "cv.skill_untied": "a skills entry with no tie to any competence the profile records",
    "letter.names": "an employer or institution named in the body that is not a recorded name",
    "letter.identifier": "a standard or certificate identifier the profile never states",
    "letter.duration": "a duration the candidate never stated",
    "letter.new_figure": "a figure no source states",
    "letter.figure": "a figure tied to an owner the profile does not record for it",
    "letter.critic_numeric": "a figure that differs from the one the profile records",
    "letter.availability": "an availability or start date the candidate did not give",
    "letter.limit": "a limit (a denial) the candidate never stated",
}

#: Code facts about COVERAGE or FORM: what the document shows of the posting, or its
#: shape, length and spelling — the claims themselves are backed. Never a point.
ADVICE_CODE_KEYS: dict[str, str] = {
    # MD-G36 (X-3a): these two report a value the WRITER returned that the COMPOSER
    # refused — ``cv_v2.compose_v2`` delivers the stored value on every path
    # (``pick_recorded`` → stored name for role, employer, degree, institution and
    # language name; the stored line for a budget whose figure changed; the stored
    # level for an upsold level code). The page is true; nothing to decide.
    "cv.level_code_changed": "the composer kept the stored language level; the writer's other level is not on the page",
    "cv.recorded_name": "the composer kept the stored name (or budget line); the writer's other form is not on the page",
    "cv.role_budget": "form — more bullets in a role than its budget",
    "cv.page_length": "form — the rendered page count over its target",
    "cv.empty_project": "form — a project listed without a bullet",
    "cv.language_shape": "form — the translation changed the number of entries",
    "cv.language_mixed": "form — items not written in the document language",
    "cv.skill_spelling": "form — a recorded skill written differently; the competence is backed",
    "cv.skills_cap": "form — the skills list over its cap",
    "cv.skills_cap_held": "form — entries kept out for the skills cap",
    "cv.skill_duplicate": "form — two skills entries that repeat each other",
    "cv.vault_field_missing": "coverage — a profile field the writer did not return",
    "cv.scope_evidence_missing": "coverage — an attested scale line the CV leaves out",
    "cv.claim_missing": "coverage — a claimable term the profile backs is absent",
    "cv.skill_without_bullet": "coverage — a backed skill no bullet states",
    "cv.project_missing": "coverage — a project the posting asks for is absent",
    "cv.project_not_mentioned": "coverage — a profile project neither shown nor mentioned",
    "cv.skill_named_in_bullet": "coverage — a skill named in a bullet, absent from the list",
    "letter.language": "form — sentences not in the letter's language",
    "letter.page_length": "form — the rendered letter over its page norm",
    "letter.salutation": "form — the salutation missing or run into the opening sentence",
    "letter.signoff": "form — the sign-off repeated in the body",
    "letter.closing": "form — no closing paragraph",
}

ADVICE_CLASSES = ("coverage", "form")


def is_advice(note: dict) -> bool:
    """An advice line: a finding classed coverage/form, a coverage/form code fact, or a
    "… and N more" line that hides advice only (stored with ``advice: true``)."""
    kind, key = note.get("kind"), str(note.get("key") or "")
    if key == "more":
        return note.get("advice") is True
    if kind == "finding":
        return str(note.get("tag") or "").strip().lower() in ADVICE_CLASSES
    if kind == "code":
        return key in ADVICE_CODE_KEYS
    return False


def is_point(note: dict) -> bool:
    """A point the person decides. Findings and code facts are points unless they are
    advice; an unknown code key is a point. A ``note`` line is never one."""
    kind, key = note.get("kind"), str(note.get("key") or "")
    if kind not in ("finding", "code"):
        return False
    if key.startswith(POINT_KEY_PREFIXES):
        return True
    return not is_advice(note)

#: MD-G32 (X-9): the sentence the agent relays for a code fact — the same words as the
#: web card's EN catalog (``frontend/messages/en.json`` ``ats.terminalReview.code``), never
#: the corrector's instruction (which names profile ids). ``{term}`` = the first subject,
#: ``{terms}`` = all of them. A key without a sentence gets :data:`GENERIC_CODE_SENTENCE`.
CODE_SENTENCES: dict[str, str] = {
    "cv.language_mixed": "Some items of the CV are not written in the document language.",
    "cv.language_shape": "The translated CV does not have the same entries as the original.",
    "cv.skills_cap_held": "Some skills were left out of the list to keep it within its cap.",
    "letter.language": "Some sentences of the letter are not written in its language.",
    "letter.critic_numeric": "A figure in the letter differs from the one your profile records.",
    "cv.claim_missing": "Your profile backs “{term}”, but the CV does not show it.",
    "cv.project_missing": "The role asks for “{term}”, and a project in your profile backs it – the CV does not show it.",
    "cv.project_not_mentioned": "This project from your profile is not in the CV: “{term}”.",
    "cv.skill_untied": "“{term}” is in the skills list but is not a skill in your profile. If your profile backs it, say it in the bullet with the evidence.",
    "cv.skill_spelling": "“{term}” is spelled differently in the skills list than in your profile.",
    "cv.skill_posting_term": "“{term}” is the job posting’s word, and you have not confirmed it as your skill – your own words stay in the bullets.",
    "cv.summary_posting_term": "“{term}” in the summary is the job posting’s word, and you have not confirmed it as yours – the summary says it in your own words.",
    "cv.skills_cap": "The skills list is longer than planned – the least relevant entries: {terms}.",
    "cv.skill_duplicate": "The skills list names the same thing twice: {terms}.",
    "cv.skill_named_in_bullet": "“{term}” is in a bullet but not in the skills list.",
    "cv.skill_without_bullet": "“{term}” is in the skills list or summary and your profile backs it in a role – no bullet in the CV shows it.",
    "cv.scope_evidence_missing": "Your profile states “{term}” – the posting asks about this scale, and the CV does not show it.",
    "cv.skill_module_unbacked": "“{term}” joins a module your profile does not record.",
    "cv.role_budget": "{term} has more bullets than planned.",
    "cv.page_length": "The CV is longer than its target page count.",
    "cv.recorded_name": "“{term}” was not used – the CV keeps the wording from your profile.",
    "cv.identifier_not_in_profile": "“{term}” is not written like this in your profile – check the spelling.",
    "cv.empty_project": "The project “{term}” has no bullet at all.",
    "cv.level_code_changed": "The CV shows the language level from your profile; a different level was not used.",
    "cv.vault_field_missing": "An entry from your profile is missing from the CV.",
    "letter.page_length": "The letter is longer than one page.",
    "letter.salutation": "The salutation is missing or not alone on the first line.",
    "letter.signoff": "The sign-off appears twice – the template prints it itself.",
    "letter.closing": "The last paragraph is not a real closing (interest and an invitation to talk).",
    "letter.figure": "In your profile, the figure “{term}” belongs to a different role than the sentence names.",
    "letter.limit": "The letter says you lack “{term}” – you never told us that.",
    "letter.names": "“{term}” is not the name your profile records for it.",
    "letter.identifier": "“{term}” is not written like this in your profile – check the spelling.",
    "letter.availability": "The letter names a start date you did not give.",
    "letter.new_figure": "The figure “{term}” is not in anything you told us – check it before you send the letter.",
    "letter.duration": "The duration “{term}” is not one you stated – give it as you state it yourself, or leave it out.",
}
#: MD-G36 (item 7): the sentence for a pipeline NOTE (``kind: note``) the agent relays as
#: advice — the web card's ``ats.terminalReview.note`` catalog where it has one.
NOTE_SENTENCES: dict[str, str] = {
    "letter.limit_cut": "We removed a sentence from the letter: it said you lack something you never told us. Removed: {terms}",
    "letter.limit_kept": "The letter says you lack something you never told us. We could not remove the sentence without emptying the letter – check it yourself: {terms}",
    "letter.missing_terms": "Terms from the role your profile backs that the shortened letter does not use: {terms}.",
    "cv.skill_twin_left_out": "A skill was left out of the skills list because a job-posting term you confirmed stands in for it; your profile keeps both.",
}
#: An unlisted code fact is a point (fails closed), so its fallback says to look.
GENERIC_CODE_SENTENCE = 'An automatic check still flags something here – look at that part of the document.'
#: MD-G36 (item 7): the neutral fallback for a line that holds nothing (a note, or an
#: advice fact without the subject its sentence names) — the web's ``note.generic``.
NEUTRAL_SENTENCE = "Something else came up while finishing the document – read it once yourself."
GENERIC_FINDING_SENTENCE = 'A note from the final check – look at that part of the document yourself.'
MORE_SENTENCE = "… and {count} more."


def plain_sentence(note: dict) -> str:
    """MD-G32 (X-9): the one sentence a card line says to the person (EN, the agent's
    words): the reviewer's own note for a finding, the catalog sentence for a code fact,
    "… and N more." for a more line. Never the corrector's instruction text."""
    import re

    kind, key = note.get("kind"), str(note.get("key") or "")
    if key == "more":
        return MORE_SENTENCE.format(count=note.get("count") or 0)
    plain = note.get("plain")
    if kind == "finding":
        return plain.strip() if isinstance(plain, str) and plain.strip() else GENERIC_FINDING_SENTENCE
    if key.startswith(POINT_KEY_PREFIXES) and isinstance(plain, str) and plain.strip():
        return plain.strip()
    if kind == "code":
        template = CODE_SENTENCES.get(key)
        fallback = NEUTRAL_SENTENCE if key in ADVICE_CODE_KEYS else GENERIC_CODE_SENTENCE
    else:
        template = NOTE_SENTENCES.get(key)
        fallback = NEUTRAL_SENTENCE
    if not template:
        return fallback
    subjects = [str(s) for s in (note.get("subjects") or []) if str(s).strip()]
    if ("{term}" in template or "{terms}" in template) and not subjects:
        return fallback
    out = template.format(term=subjects[0] if subjects else "", terms=", ".join(subjects))
    # a subject is data the person reads; a profile id is not (never relayed)
    return fallback if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", out) else out
