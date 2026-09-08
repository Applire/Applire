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

**Nine builders, four ask families.** Every deterministic
`RequestConfirmation` in `applire/` is built here — `apply.py` x8 and
`attribution.py` x1 — so a family's option vocabulary has one definition and a
tenth call site cannot invent a tenth wording. (The ADR's draft said "10 call
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
    """
    joined = _join(related, ", ")
    return _build(
        (
            f"„{incoming_skill}“ teilt ein Wort mit Fähigkeiten in deinem Profil "
            f"({joined}), könnte aber eine eigene Fähigkeit sein. Separat "
            f"hinzufügen oder in eine vorhandene zusammenführen?"
        ),
        (
            f"'{incoming_skill}' shares a word with skills already on your "
            f"profile ({joined}) but may be a distinct skill. Add it separately, "
            f"or merge it into an existing one?"
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
