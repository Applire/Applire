# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#669 / ADR-063 amended 2026-09-05 (BUILT 2026-09-08) — the deterministic
confirmation builders, with stable option keys and a language-independent
persisted form.

**Why this module exists.** `RequestConfirmation.options` was not a label — it
was the IDENTITY the candidate's answer was matched on, in English, by
substring, on a vault WRITE path shared by both doors (ADR-058):

    session._skill_confirmation_decision
        "separate"            -> distinct
        "keep" and "existing" -> keep
        "merge"               -> merge
        anything else         -> distinct     # the default

Localising the options without replacing that matcher makes the German
rendering of *"Keep the existing skills"* resolve to `distinct`: the candidate
asked to DISCARD the incoming skill and the vault gains it instead. Silent,
wrong, and on the truthfulness-bearing side. Pinned executably before the build
in `tests/unit/test_interview_confirmation_resolution.py::
test_a_german_rendering_of_the_same_options_mis_resolves`.

**Two collector lines this closes** (#620 interview, #602 vault): deterministic
reconcile paths emitted hard-coded English into an otherwise fully German flow —
structurally, because the op carried no locale field at all. The house pattern
for doing it right already existed (LLM-generated questions and the outcome
critic's advisories ship `{"de": …, "en": …}` pairs and follow `ui_language`);
it had simply never been applied to these paths.

**Ten builders, five ask families.** Every deterministic
`RequestConfirmation` in `applire/` is built here — `apply.py` x8 and
`attribution.py` x2 (the guard's ask, and the narrower "which of your roles at
this employer" ask its RESOLUTION raises, #723 / ADR-063 am. 2026-09-18) — so a
family's option vocabulary has one definition and an eleventh call site cannot
invent an eleventh wording. `resolve_option_key`, the one READER of that
vocabulary, lives here for the same reason (ADR-066); `services/session.py`
re-exports it. (The ADR's draft said "10 call
sites, including `stance.py:434`"; re-counted at `24ee8cd6`, `stance.py:434` is
not a confirmation at all — it is a hard-coded English `FieldChange.rationale`,
a different shape whose instrument is `rationale_key`. Fixed there, not here.)

The MODEL also emits `request_confirmation` (reconcile prompt rule 6). Its ops
carry none of these fields — `engine._strip_adapter_only` removes them from raw
model output — so a model-emitted confirmation resolves through the back-compat
English matcher, exactly as it did before this change.
"""
from __future__ import annotations

from typing import Any

from applire.services.profile.reconcile.ops import RequestConfirmation


def _join(items: list[str], sep: str = "; ") -> str:
    return sep.join(items)


def entity_dupe_confirmation(
    *,
    section: str,
    incoming_label: str,
    existing_labels: list[str],
    context: dict[str, Any],
    merge_first: bool = True,
) -> RequestConfirmation:
    """Family 1 — "is this the same entry as one you already have?"

    Six of the nine builders: work, project, volunteer, education, publication,
    certification. The two options are always the same DECISION — merge into
    the existing entry, or keep both as distinct entries — so they share one
    key pair (``merge`` / ``distinct``) even though six English wordings
    existed. ``merge_first`` preserves each site's option ORDER, because the
    order is what the answer buttons render and a reordered dialog is a UX
    change this build is not making.
    """
    joined = _join(existing_labels)
    noun = _SECTION_NOUN[section]
    question_en = (
        f"'{incoming_label}' looks close to {noun['article_en']} "
        f"{noun['en']} already on your profile ({joined}). Is it the same one?"
    )
    question_de = (
        f"„{incoming_label}“ ähnelt {noun['dative_de']} "
        f"{noun['de']} in deinem Profil ({joined}). Ist es dasselbe?"
    )
    merge = {
        "en": f"Same {noun['en']} — merge them",
        "de": f"{noun['same_de']} — zusammenführen",
    }
    distinct = {
        "en": "Different — keep both",
        "de": "Unterschiedlich — beide behalten",
    }
    pairs = [(merge, "merge"), (distinct, "distinct")]
    if not merge_first:
        pairs.reverse()
    return _build(question_de, question_en, pairs, context)


def skill_overlap_confirmation(
    *, incoming_skill: str, overlapping: list[str], context: dict[str, Any]
) -> RequestConfirmation:
    """Family 2 — the incoming skill near-dupes SEVERAL existing skills.

    ``keep`` is the option #669 exists for: its English text is *"Keep the
    existing skills"*, which the old matcher read as ``keep`` only because both
    the words "keep" and "existing" happen to be in it. The German rendering
    *"Die vorhandenen Fähigkeiten behalten"* contains neither, so it fell to the
    ``distinct`` default — the vault GAINING a skill the candidate asked it to
    discard.
    """
    joined = _join(overlapping, ", ")
    return _build(
        (
            f"„{incoming_skill}“ überschneidet sich mit mehreren Fähigkeiten in "
            f"deinem Profil ({joined}). Soll es sie ersetzen oder als eigene "
            f"Fähigkeit bestehen bleiben?"
        ),
        (
            f"'{incoming_skill}' overlaps several skills already on your profile "
            f"({joined}). Should it replace them or be kept as a separate skill?"
        ),
        [
            (
                {
                    "en": f"Merge into '{incoming_skill}'",
                    "de": f"In „{incoming_skill}“ zusammenführen",
                },
                "merge",
            ),
            (
                {
                    "en": "Keep the existing skills",
                    "de": "Die vorhandenen Fähigkeiten behalten",
                },
                "keep",
            ),
        ],
        context,
    )


def skill_containment_confirmation(
    *, incoming_skill: str, related: list[str], context: dict[str, Any]
) -> RequestConfirmation:
    """Family 3 — the incoming skill shares a WORD with an existing one.

    The US291 namesake case ("SAP PP" beside "SAP"), raised twice in one German
    run of the v0.40.0-beta acceptance pass — the occurrence evidence behind the
    #620 collector line.

    **The third option is #730's own vector** (founder UAT 2026-09-20, F-1/F-11,
    ADR-063 amended 2026-09-20). This family's two options were both WRITES, so
    "neither" was expressible only as free text — and the candidate's refusal
    *"Please do not add it as a separate skill and do not merge it into my
    existing Collaboration skill either"* was resolved by the back-compat
    English substring matcher to `distinct`, because it quoted the option it was
    rejecting. The vault gained a skill the candidate had just denied and it
    shipped in the delivered CV.

    The key is the EXISTING ``keep`` (family 2's vocabulary, already handled by
    ``session._apply_skill_confirmation`` as "discard the incoming, the existing
    skills stand"), never a new eleventh key: one vocabulary per decision
    (ADR-066).
    """
    joined = _join(related, ", ")
    return _build(
        (
            f"„{incoming_skill}“ teilt ein Wort mit Fähigkeiten in deinem Profil "
            f"({joined}), könnte aber eine eigene Fähigkeit sein. Separat "
            f"hinzufügen, in eine vorhandene zusammenführen — oder gar nicht "
            f"aufnehmen?"
        ),
        (
            f"'{incoming_skill}' shares a word with skills already on your "
            f"profile ({joined}) but may be a distinct skill. Add it separately, "
            f"merge it into an existing one — or not add it at all?"
        ),
        [
            (
                {
                    "en": f"Add '{incoming_skill}' as a separate skill",
                    "de": f"„{incoming_skill}“ als eigene Fähigkeit hinzufügen",
                },
                "distinct",
            ),
            (
                {
                    "en": "Merge into the existing skill",
                    "de": "In die vorhandene Fähigkeit zusammenführen",
                },
                "merge",
            ),
            (
                {
                    "en": f"Neither — don't add '{incoming_skill}'",
                    "de": f"Weder noch — „{incoming_skill}“ nicht aufnehmen",
                },
                "keep",
            ),
        ],
        context,
    )


def attribution_confirmation(
    *,
    sample: str,
    anchor_text: str,
    target_display: str,
    context: dict[str, Any],
) -> RequestConfirmation:
    """Family 4 — the attribution guard (#243): which employer does this belong to?

    Its own key vocabulary (``move`` / ``keep_here`` / ``discard``), because the
    decision is not the merge/distinct one. Nothing resolves these keys
    deterministically today — the profile-review interview answers this family —
    so the keys are the durable identity for a future resolver, and the
    localisation is what the collector line actually asked for.
    """
    return _build(
        (
            f"„{sample}“ klingt, als gehöre es zu {anchor_text}, nicht zu "
            f"{target_display} — die Antwort nannte {anchor_text} für diesen "
            f"Teil, dieser Eintrag steht aber unter {target_display}. Wohin "
            f"gehört es?"
        ),
        (
            f"'{sample}' reads like it belongs to {anchor_text}, not "
            f"{target_display} — the answer named {anchor_text} for this part, "
            f"but this entry is under {target_display}. Where should it go?"
        ),
        [
            (
                {"en": f"Move to {anchor_text}", "de": f"Zu {anchor_text} verschieben"},
                "move",
            ),
            (
                {
                    "en": f"Keep on {target_display}",
                    "de": f"Bei {target_display} belassen",
                },
                "keep_here",
            ),
            ({"en": "Discard it", "de": "Verwerfen"}, "discard"),
        ],
        context,
    )


def attribution_entry_confirmation(
    *,
    sample: str,
    anchor_text: str,
    candidates: list[tuple[str, str]],
    context: dict[str, Any],
) -> RequestConfirmation:
    """Family 5 — the attribution family's SECOND, narrower question (#723).

    Raised only by the RESOLUTION of a family-4 ask, never by the reconciler:
    the candidate answered "move it to <employer>" and the vault holds several
    roles at that employer (three in the #723 incident). Founder ruling V-1 /
    D-6 (2026-09-18) forbids guessing at that point — the answer's own date
    anchor places it when exactly one role's date range contains a year the held
    text itself carries, and otherwise the candidate picks the role.

    Its option keys are ``entry:<id>`` per candidate role plus ``discard``, so
    the identity the answer resolves on is an ID and stays language-independent
    exactly as #669 requires — a rendered role label may be translated, a role
    id may not. The ask sits outside the interview's question budget, like every
    confirmation turn (ADR-080 clause 5).
    """
    pairs: list[tuple[dict[str, str], str]] = [
        ({"en": label, "de": label}, f"entry:{entry_id}")
        for entry_id, label in candidates
    ]
    pairs.append(({"en": "Discard it", "de": "Verwerfen"}, "discard"))
    return _build(
        (
            f"Du hast mehrere Positionen bei {anchor_text}. Zu welcher geh\u00f6rt "
            f"\u201e{sample}\u201c?"
        ),
        (
            f"You have several positions at {anchor_text}. Which one does "
            f"'{sample}' belong to?"
        ),
        pairs,
        context,
    )


def resolve_option_key(pending_conf: dict, chosen: str) -> str | None:
    """The stable key of the option the candidate picked (#669), or ``None``.

    ADR-063 amended 2026-09-05: a confirmation's OPTIONS are the IDENTITY the
    answer is matched on, so the identity may not be a rendered string. The
    parked confirmation carries ``option_keys`` positionally paired with
    ``options``; this finds WHICH option the answer names and returns its key.

    Matched against every rendering the record carries — the plain ``options``
    AND each language of ``options_i18n`` — so an answer submitted against a
    German render resolves even if the caller re-rendered in English between
    ask and answer. Exact (case- and whitespace-folded) equality, never a
    substring: substring matching on rendered text is the defect this replaces.

    ``None`` means "this record has no keys" (persisted before #669, or
    model-emitted) — the caller falls back to the English matcher.

    **Lives here since 2026-09-18** (#723). It is the reader of the vocabulary
    the builders above write, and it has TWO callers now: the interview's answer
    dispatch (`session.py`, which re-exports it) and the applier's own
    resolution of a family-4 ask (`apply.py::_apply_resolve_confirmation`) —
    ADR-066, one implementation per capability, in the module that owns the
    keys.
    """
    keys = pending_conf.get("option_keys") or []
    if not keys:
        return None
    answer = (chosen or "").strip().casefold()
    if not answer:
        return None
    renderings: list[list[str]] = [list(pending_conf.get("options") or [])]
    i18n = pending_conf.get("options_i18n") or []
    langs = {lang for payload in i18n if isinstance(payload, dict) for lang in payload}
    for lang in sorted(langs):
        renderings.append([
            (payload.get(lang) or "") if isinstance(payload, dict) else ""
            for payload in i18n
        ])
    for rendering in renderings:
        for idx, text in enumerate(rendering):
            if idx < len(keys) and text and text.strip().casefold() == answer:
                return keys[idx]
    return None


#: The skill-dedupe decision vocabulary (families 2 and 3). ``keep`` means
#: "discard the incoming skill, the existing ones stand" — it is the only one of
#: the three that writes nothing.
SKILL_OPTION_KEYS = frozenset({"distinct", "merge", "keep"})


#: What the candidate is told when their answer named none of the options
#: (#730 / ADR-063 amended 2026-09-20). Rendered against the conversation
#: language like every other deterministic string on this surface (#669).
UNMATCHED_ANSWER_HINT: dict[str, str] = {
    "en": (
        "Your answer doesn't match any of the options, so nothing was changed. "
        "Please pick one of them."
    ),
    "de": (
        "Deine Antwort passt zu keiner der Optionen, deshalb wurde nichts "
        "geändert. Bitte wähle eine davon."
    ),
}


def render_unmatched_answer_hint(lang: str) -> str:
    """The re-ask sentence in the reader's language (fallback chain ``[lang] ??
    de ?? en``, the same one :func:`render_localized_confirmation` uses)."""
    return (
        UNMATCHED_ANSWER_HINT.get(lang)
        or UNMATCHED_ANSWER_HINT.get("de")
        or UNMATCHED_ANSWER_HINT["en"]
    )


def resolve_skill_decision(pending_conf: dict, chosen: str) -> str | None:
    """THE skill-dedupe resolution: ``distinct`` / ``merge`` / ``keep``, or
    ``None`` when the answer names no option (#730, ADR-063 amended
    2026-09-20).

    ``None`` is the whole point. Until this, the resolution ended in
    ``session._skill_confirmation_decision``'s ``return "distinct"`` — an
    unmatched answer WROTE the skill. The founder UAT of 2026-09-20 (F-1/F-11,
    #730) hit it with a refusal that quoted the option it rejected: *"Please do
    not add it as **a separate** skill and do not merge it into my existing
    Collaboration skill either"* contains the word "separate", so branch 1 of
    the English substring matcher fired and the vault gained a skill the
    candidate had explicitly denied — at ``status="confirmed"``, which is what
    the Oracle then checks every document against.

    Resolution order, and why the substring matcher survives at all:

    1. ``resolve_option_key`` — exact, case-folded equality against every
       rendering the record carries. This is ADR-063's 2026-09-05 identity rule
       and it is the ONLY path that can decide for a record built after #669.
    2. **The record carries keys and none of them was named ⇒ ``None``.** Never
       fall through to substring matching: the options ARE the identity, so an
       answer that is not one of them is not an answer. Every confirmation that
       can reach the vault write carries keys — ``context["incoming_skill"]`` is
       emitted only by ``apply.py``'s two deterministic builders, both of which
       go through :func:`_build` — so this branch is the real one.
    3. No keys at all (a record parked before #669) ⇒ the back-compat English
       substring matcher, unchanged in what it MATCHES and changed in what it
       does when nothing matches: ``None``, never ``"distinct"``.

    ADR-066: this is the one implementation. ``session`` re-exports it and its
    own ``_skill_confirmation_decision`` is now a thin wrapper kept for the
    tests that pin the back-compat matcher's behaviour directly.
    """
    key = resolve_option_key(pending_conf, chosen)
    if key in SKILL_OPTION_KEYS:
        return key
    if pending_conf.get("option_keys"):
        return None
    return match_skill_decision_text(chosen)


def match_skill_decision_text(chosen: str) -> str | None:
    """The pre-#669 English substring matcher — back-compat only, and no longer
    a writing default.

    Reachable for a confirmation persisted before #669 (which carries no
    ``option_keys``). The three branches are byte-identical to the ones
    ``session._skill_confirmation_decision`` has always had; only the final
    ``return "distinct"`` is gone. It was the second half of #730: *any*
    unmatched free-text answer minted the skill.
    """
    c = (chosen or "").strip().lower()
    if "separate" in c:
        return "distinct"
    if "keep" in c and "existing" in c:
        return "keep"
    if "merge" in c:
        return "merge"
    return None


# ── internals ────────────────────────────────────────────────────────────────

#: Per-section nouns for family 1's six wordings. German carries grammatical
#: gender, so the "same X" option is stored as a WHOLE phrase (`same_de`) rather
#: than assembled from a fixed determiner plus a noun — "Dasselbe Position" is
#: the bug that shape produces, and it is invisible to a key-parity test.
_SECTION_NOUN: dict[str, dict[str, str]] = {
    "work_experience": {
        "en": "position", "article_en": "a", "de": "Position",
        "dative_de": "einer", "same_de": "Dieselbe Position",
    },
    "projects": {
        "en": "project", "article_en": "a", "de": "Projekt",
        "dative_de": "einem", "same_de": "Dasselbe Projekt",
    },
    "volunteer_activities": {
        "en": "volunteer activity", "article_en": "a", "de": "Ehrenamt",
        "dative_de": "einem", "same_de": "Dasselbe Ehrenamt",
    },
    "education": {
        "en": "education entry", "article_en": "an", "de": "Ausbildung",
        "dative_de": "einer", "same_de": "Dieselbe Ausbildung",
    },
    "publications": {
        "en": "publication", "article_en": "a", "de": "Publikation",
        "dative_de": "einer", "same_de": "Dieselbe Publikation",
    },
    "certifications": {
        "en": "certification", "article_en": "a", "de": "Zertifizierung",
        "dative_de": "einer", "same_de": "Dieselbe Zertifizierung",
    },
}


def _build(
    question_de: str,
    question_en: str,
    pairs: list[tuple[dict[str, str], str]],
    context: dict[str, Any],
) -> RequestConfirmation:
    """Assemble the op so the three parallel lists stay positionally aligned.

    ``options`` keeps the English rendering as the plain field: it is what a
    pre-#669 reader (a persisted record's consumer, the back-compat matcher, an
    agent door that has not been updated) sees, and it is byte-identical to what
    those callers saw before, so nothing regresses while the localized half
    rolls out.
    """
    return RequestConfirmation(
        question=question_en,
        options=[payload["en"] for payload, _ in pairs],
        context=dict(context),
        question_i18n={"de": question_de, "en": question_en},
        options_i18n=[dict(payload) for payload, _ in pairs],
        option_keys=[key for _, key in pairs],
    )
