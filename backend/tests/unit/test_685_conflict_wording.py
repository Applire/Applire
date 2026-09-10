# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#685 — how a parked dispute is spoken to the candidate.

Founder UAT on a real install, 2026-09-06: answering ONE gap question produced

    "Your profile has two values for professional_summary.en: currently
     '<~1,200 characters>', but an import suggested '15+ years …'.
     Which is correct?"

Three defects in one sentence, and all three are BACKEND-generated, so a
frontend fix would leave the agent door (ADR-058 parity) with the raw key and
1,200-character option strings:

1. the raw `section.field` key is shown instead of what the field IS;
2. "an import suggested" is said whatever raised the dispute — here an INTERVIEW
   answer. `Conflict.source` carried the truth all along;
   `session._open_conflicts` simply never copied it into the cluster dict;
3. both values are interpolated in full, into the question AND both answer
   buttons.

Written after a mutation sweep found the source-threading ungated: reverting
`"source": c.source` in `_open_conflicts` reddened nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    Conflict,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.interview_graph import (  # noqa: E402
    build_conflict_clusters,
    conflict_question,
)

LONG_SUMMARY = (
    "Erfahrener IT-Manager mit Schwerpunkt auf regulierten Produktionsumgebungen "
    "und langjähriger Erfahrung in der Qualifizierung von Systemen. "
) * 9
ANSWER = "15+ Jahre in der GMP-regulierten Pharmaproduktions-IT"


# ── 1. the section is named in words, never the raw key ──────────────────────


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("en", "your self-description (English)"), ("de", "deine Selbstbeschreibung (Englisch)")],
)
def test_a_summary_dispute_names_the_section_in_words(lang, expected):
    q = conflict_question("professional_summary", "en", "old", "new", lang, source="interview")
    assert expected in q["question"]
    assert "professional_summary" not in q["question"]
    assert "professional_summary" not in " ".join(q["choices"])


# ── 2. the REAL source, in the candidate's words ─────────────────────────────


def test_an_interview_answer_is_not_called_an_import():
    """The founder-observed sentence, in both languages."""
    en = conflict_question("professional_summary", "en", "old", "new", "en", source="interview")
    assert "your interview answer" in en["question"]
    assert "import" not in en["question"].lower()

    de = conflict_question("professional_summary", "en", "old", "new", "de", source="interview")
    assert "deiner Interview-Antwort" in de["question"]
    assert "import" not in de["question"].lower()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("cv_upload", "a CV you uploaded"),
        ("linkedin_import", "your LinkedIn import"),
        ("testimony", "your own notes"),
        ("manual_edit", "an edit you made"),
    ],
)
def test_every_named_source_is_spoken_as_itself(source, expected):
    q = conflict_question("work_experience", "end_date", "2020", "2021", "en", source=source)
    assert expected in q["question"]


@pytest.mark.parametrize("source", [None, "", "migration", "some_future_intake"])
def test_an_unmapped_source_degrades_to_honest_vagueness_never_to_an_import(source):
    """`reconcile/migrate.py` passes "migration", which is outside the
    `EnrichmentRecord.source` literal set. An unmapped value must not be
    described as an import — that is the whole defect, one step removed."""
    q = conflict_question("work_experience", "end_date", "2020", "2021", "en", source=source)
    assert "a later change" in q["question"]
    assert "import" not in q["question"].lower()


# ── 3. the values are excerpted on the ASK surface ───────────────────────────


def test_a_long_self_description_is_excerpted_in_the_question_and_both_buttons():
    q = conflict_question(
        "professional_summary", "en", LONG_SUMMARY, ANSWER, "de", source="interview"
    )
    assert len(LONG_SUMMARY) > 1000  # the fixture really is the offending shape
    assert len(q["question"]) < 600
    for choice in q["choices"]:
        assert len(choice) < 260
    # Excerpted, not mangled: it still starts with the candidate's own opening
    # and ends on a word boundary with an ellipsis.
    keep = q["choices"][0]
    assert keep.startswith("Aktuellen behalten: Erfahrener IT-Manager")
    assert keep.endswith("…")
    # A short value is untouched — the excerpt is a ceiling, not a formatter.
    assert ANSWER in q["choices"][1]


# ── the seam the mutation sweep found ungated ────────────────────────────────


@pytest.mark.asyncio
async def test_open_conflicts_carries_the_real_source_through_to_the_question():
    """The SEAM, driven end to end: a `Conflict` with `source="interview"` on a
    profile record must reach `conflict_question` as "your interview answer".

    Reverting `"source": c.source` in `session._open_conflicts` reddens exactly
    this test — the mutation that previously survived every other test in the
    tree.
    """
    from tests.support.profile_factory import make_master_profile

    from applire.services.session import _open_conflicts

    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Emma Vogt"},
            "professional_summary": {"en": LONG_SUMMARY},
            "metadata": ProfileMetadata(
                pending_conflicts=[
                    Conflict(
                        section="professional_summary",
                        field="en",
                        existing_value=LONG_SUMMARY,
                        incoming_value=ANSWER,
                        source="interview",
                    )
                ]
            ).model_dump(mode="json"),
        }
    )
    # Through the test factory, which wraps `authorized_profile_write()` — a bare
    # `MasterProfile(profile_json=…)` is refused by the ADR-063 clause-6 guard,
    # correctly (the guard sees keyword construction too, #480 PR 9).
    record = make_master_profile(profile_json=profile.model_dump(mode="json"))

    rows = await _open_conflicts(record)
    assert len(rows) == 1
    assert rows[0]["source"] == "interview"

    _ids, _cats, by_id = build_conflict_clusters(rows, "en")
    cluster = next(iter(by_id.values()))
    assert "your interview answer" in cluster["question"]
    assert "an import suggested" not in cluster["question"]
    assert "professional_summary" not in cluster["question"]
