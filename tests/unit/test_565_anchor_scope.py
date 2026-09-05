# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#565 — the employer anchor's scope is the PARAGRAPH's employer run, not the sentence.

Ground truth (ship-gate blind run, 2026-08-19, `controlling_emma_de`, real
provider): the delivered letter opened **eight consecutive body sentences** with
"Bei der Schwarzwald Präzision GmbH". Both blind panel reviewers, independently,
named it — HR *"wirkt mechanisch/unpoliert … eher wie eine automatisiert erzeugte
Aufzählung"*, Fachbereich *"acht Mal als Satzanfang wiederholt … evtl. KI-gestützt
ohne Politur"*. The driver was this loop's own rule, obeyed: the terminal review's
recorded issues read *"Each position-owned responsibility or figure must name
Schwarzwald Präzision GmbH in the same sentence."*

Two properties are pinned here, and the second is why the relaxation is affordable
at all (ADR-021 amended 2026-09-05, clause 3):

1. **The four prompt statements agree, and none of them justifies itself with a
   guard that does not exist.** The writer, the shared reviewer check 2 (both
   doors), the corrector bullet and the condense REQUIRED-CONTENT list all state
   the paragraph scope. The false premise — *"an unanchored figure is silently
   dropped/stripped by a deterministic guard"* — is gone from every letter prompt
   string, not only from the corrector where #534 fixed it in 2026-08-13.
2. **The shape the prompts now ask for is fully attributable by the instruments
   that grade it afterwards.** `oracle/extract.py` already carries the paragraph
   anchor (#237 run-4, docstring point 5) and `letter_figure_guard` already fails
   open on a sentence that names no employer (#299). Both are asserted here
   BEHAVIOURALLY, on a letter written in the new shape — not by reading a
   docstring.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts import cover_letter as writer  # noqa: E402
from applire.prompts import review_cover_letter as reviewer  # noqa: E402
from applire.services.letter_figure_guard import guard_letter_figures  # noqa: E402
from applire.services.oracle.extract import extract_claims_from_letter  # noqa: E402


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


# ── the profile and the two letter shapes ───────────────────────────────────
#
# Two employers, because a single-employer letter takes `_allowed_owner_ids`'
# whole-letter escape and would prove nothing about the carry.

PROFILE = {
    "personal_info": {"name": "Katrin Vogel"},
    "work_experience": [
        {
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Senior Controllerin",
            "achievements": [
                "Verantwortete die Konsolidierung von zwei Produktionsstandorten.",
                "Führte ein Team von sechs Mitarbeitenden im Konzerncontrolling.",
            ],
        },
        {
            "id": "w-nordwerk",
            "company": "Nordwerk GmbH",
            "role": "Controllerin",
            "achievements": [
                "Baute das monatliche Reporting für vier Werke auf.",
            ],
        },
    ],
}

#: The delivered 2026-08-19 shape, in miniature: every sentence repeats the name.
LETTER_SENTENCE_ANCHORED = {
    "recipient": {"company": "Arnsberg Kunststoff AG"},
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Bei der Schwarzwald Präzision GmbH verantwortete ich die Konsolidierung "
            "von zwei Produktionsstandorten. Bei der Schwarzwald Präzision GmbH "
            "führte ich ein Team von sechs Mitarbeitenden im Konzerncontrolling.",
            "Bei der Nordwerk GmbH baute ich das Reporting für vier Werke auf.",
        ]
    },
}

#: The shape #565 asks for: one anchor per employer run, per paragraph.
LETTER_PARAGRAPH_ANCHORED = {
    "recipient": {"company": "Arnsberg Kunststoff AG"},
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Bei der Schwarzwald Präzision GmbH verantwortete ich die Konsolidierung "
            "von zwei Produktionsstandorten. Dort führte ich ein Team von sechs "
            "Mitarbeitenden im Konzerncontrolling.",
            "Bei der Nordwerk GmbH baute ich das Reporting für vier Werke auf.",
        ]
    },
}


def _sentence_initial_employer_share(letter: dict) -> float:
    """The #565 measurement contract, as code: the share of body sentences that
    OPEN with an employer name, over the paragraphs that carry any."""
    from applire.services.oracle.extract import split_sentences

    names = [w["company"] for w in PROFILE["work_experience"]]
    total = opening = 0
    for para in letter["body"]["paragraphs"][1:]:  # skip the salutation
        for sentence in split_sentences(para):
            total += 1
            head = sentence.lstrip()[:60]
            if any(n in head for n in names):
                opening += 1
    return opening / total if total else 0.0


# ── 1. the four prompt statements agree on the paragraph scope ──────────────


def test_the_writer_asks_for_one_anchor_per_employer_run_per_paragraph():
    low = _flat(writer.SYSTEM_PROMPT)
    assert "employer run" in low
    assert "paragraph" in low
    # The relaxation is explicit, not merely implied by the absence of the old rule.
    assert "refer back" in low or "back-refer" in low
    assert "one paragraph, one employer" in low


def test_the_shared_reviewer_check_2_scopes_the_anchor_to_the_paragraph():
    """`_CHECKS` is shared by BOTH doors (prose + terminal), so one edit reaches
    the door whose recorded issues drove #565."""
    for prompt in (reviewer.REVIEW_SYSTEM_PROMPT, reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT):
        low = _flat(prompt)
        assert "wrong or missing owner" in low
        assert "paragraph" in low
        # the guardrails that predate #565 and must survive it
        assert "add the anchor" in low
        assert "never instruct the writer to delete" in low


def test_the_reviewer_is_told_not_to_flag_a_back_reference_inside_an_anchored_run():
    """The half that actually changes the reviewer's behaviour. Without it the
    check merely acquires a new word and keeps demanding the old thing."""
    low = _flat(reviewer.REVIEW_SYSTEM_PROMPT)
    assert "never a back-referring sentence" in low or "not a back-referring sentence" in low


def test_the_corrector_bullet_carries_the_same_scope():
    low = _flat(reviewer.COVER_LETTER_REFINEMENT_PROMPT)
    assert "employer run" in low
    assert "paragraph" in low
    # #299's real reason survives the rescope
    assert "fail open" in low
    assert "never quietly delete" in low or "never let an achievement" in low


def test_the_condense_required_content_list_carries_the_same_scope():
    entry = [e for e in writer.LETTER_REQUIRED_CONTENT if "anchor" in e.lower()]
    assert len(entry) == 1, "exactly one REQUIRED-CONTENT entry owns the anchor"
    low = _flat(entry[0])
    assert "paragraph" in low
    assert "employer run" in low or "run" in low


# ── 2. no letter prompt justifies the rule with a guard that does not exist ──


def _letter_prompt_strings() -> dict[str, str]:
    """Every module-level prompt string of the two letter prompt modules.

    An enumeration, not a hand-picked list: #534 fixed exactly ONE of the three
    copies of the false premise in 2026-08-13 and the other two survived for
    three weeks. Enumerating the positive set is what makes the absence claim
    provable rather than estimated.
    """
    out: dict[str, str] = {}
    for mod in (writer, reviewer):
        for name in dir(mod):
            if name.startswith("__"):
                continue
            value = getattr(mod, name)
            if isinstance(value, str) and len(value) > 200:
                out[f"{mod.__name__}.{name}"] = value
            elif isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
                out[f"{mod.__name__}.{name}"] = "\n".join(value)
    return out


def test_no_letter_prompt_claims_a_guard_silently_drops_an_unanchored_figure():
    """#534's correction, applied to the whole positive set.

    `_allowed_owner_ids` returns ``None`` for a sentence naming no employer in a
    multi-employer letter — the docstring's own words are *"the sentence is left
    ALONE (#299)"*. A prompt that motivates its rule with the opposite is stating
    something false to the model, and the model has no way to find out.
    """
    offenders = []
    for name, text in _letter_prompt_strings().items():
        low = _flat(text)
        for phrase in ("silently dropped by a deterministic guard",
                       "silently stripped by a downstream guard",
                       "downstream truthfulness guard silently drops",
                       "silently strips an unattributable"):
            if phrase in low:
                offenders.append((name, phrase))
    assert offenders == [], f"a letter prompt still names the #299-deleted strip: {offenders}"


def test_the_positive_set_is_not_empty():
    """A guard against the previous test passing because the enumeration found
    nothing to look at."""
    found = _letter_prompt_strings()
    assert len(found) >= 4, found.keys()
    assert any("SYSTEM_PROMPT" in k for k in found)


# ── 3. the new shape is fully gradeable by the instruments that run after ────


def test_the_oracle_anchors_every_clause_of_a_paragraph_anchored_letter():
    """ADR-021 amended 2026-09-05 clause 3: the relaxation is granted only on
    ground the post-hoc instrument already covers. Behavioural, not a docstring
    read — the back-referring sentence must carry the SAME
    ``source_experience_id`` as the sentence that anchored the run.
    """
    claims = extract_claims_from_letter(LETTER_PARAGRAPH_ANCHORED, PROFILE)
    # the "Dort führte ich ein Team von sechs …" content
    carried = [c for c in claims if "sechs" in c.text]
    assert carried, "the continuation sentence produced no claim"
    assert all(c.source_experience_id == "w-schwarzwald" for c in carried), [
        (c.text, c.source_experience_id) for c in carried
    ]
    # and the carry does NOT cross the paragraph break
    nordwerk = [c for c in claims if "vier Werke" in c.text]
    assert nordwerk
    assert all(c.source_experience_id == "w-nordwerk" for c in nordwerk)


def test_the_figure_guard_drops_nothing_from_a_paragraph_anchored_letter():
    """#299's fail-open, asserted on the new shape: relaxing the prompt rule must
    not hand the deterministic guard anything new to delete. The guard is the
    control whose paragraph carry #299 REMOVED — this pins that we did not put it
    back by the side door."""
    before = LETTER_PARAGRAPH_ANCHORED["body"]["paragraphs"]
    after = guard_letter_figures(LETTER_PARAGRAPH_ANCHORED, PROFILE)["body"]["paragraphs"]
    assert after == before

    # ... and the sentence-anchored shape it replaces loses nothing either, so the
    # measured difference between the two shapes is readability, never grounding.
    before_s = LETTER_SENTENCE_ANCHORED["body"]["paragraphs"]
    after_s = guard_letter_figures(LETTER_SENTENCE_ANCHORED, PROFILE)["body"]["paragraphs"]
    assert after_s == before_s


def test_the_measurement_instrument_separates_the_two_shapes():
    """The #565 measurement contract's own instrument, validated on a known
    positive and a known negative before it is used on replay output (the
    ADR-083 scorer discipline: a scorer that has not been shown to separate its
    two classes is not evidence)."""
    assert _sentence_initial_employer_share(LETTER_SENTENCE_ANCHORED) == 1.0
    assert _sentence_initial_employer_share(LETTER_PARAGRAPH_ANCHORED) == 2 / 3


# ── 4. the prompt-size ratchets are NOT raised to pay for this ──────────────


def test_the_letter_reviewer_ratchets_are_not_raised_by_the_rescope():
    """The prose door had 25 characters of headroom (12,475 / 12,500) when this
    change started. The rescope is paid for inside `_CHECKS`, not by moving the
    ceiling — 'map the new content to a row and REPLACE, do not append'."""
    assert len(reviewer.REVIEW_SYSTEM_PROMPT) < 12_500, len(reviewer.REVIEW_SYSTEM_PROMPT)
    assert len(reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT) < 16_100, len(
        reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT
    )


def test_the_writer_prompt_does_not_grow_for_the_rescope():
    """The writer prompt is already LARGER than its reviewer (14,333 vs 12,475 at
    the start of this change) — the 2026-07-30 audit calls that a smell. This
    change is a correction, so it may not make it worse."""
    assert len(writer.SYSTEM_PROMPT) <= 14_333, len(writer.SYSTEM_PROMPT)
