# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#675 line 42 / ruling V-1 — `professional_summary` is a REACHABLE field gap.

The completeness model has two readers of one fact. The SCORE
(`_has_meaningful_data`) asked it correctly from the start —
`bool(value.de or value.en)` on the model. The AGENDA (`completeness.field_gaps`)
asked it on the dumped profile with bare truthiness, and the dumped value is
`{"de": None, "en": None}` — a non-empty dict, so always truthy. The gap could
therefore never fire: the Health hub never listed it, and Mode C, which #683
makes its only asker, was never handed it to ask.

The tests below are written on the PERSISTED shape (a round-tripped
`model_dump(mode="json")`), because that is the shape the defect needed: every
in-memory construction with an explicit `professional_summary=None` would have
made the old code look right.
"""
import pytest

from applire.services.profile.completeness import field_gaps, summary_present
from applire.schemas.profile import MasterProfileData


def _persisted(summary=None) -> dict:
    """A profile as it comes back out of the database — not as a test builds it."""
    data = {
        "personal_info": {"name": "Marcus Schmidt"},
        "work_experience": [
            {
                "company": "Acme GmbH",
                "role": "Produktionsleiter",
                "start_date": "2020-01",
                "end_date": "2024-06",
                "achievements": ["Ramp-up der zweiten Linie"],
            }
        ],
    }
    if summary is not None:
        data["professional_summary"] = summary
    return MasterProfileData.model_validate(data).model_dump(mode="json")


def test_the_persisted_shape_is_the_one_that_hid_the_defect():
    """The premise, asserted rather than assumed: an empty summary round-trips
    to a truthy dict. If this ever stops being true the fix below is moot and
    the test should say so out loud."""
    profile = _persisted()
    assert profile["professional_summary"] == {"de": None, "en": None}
    assert bool(profile["professional_summary"]) is True, (
        "the old predicate's bug: a non-empty dict of empty values"
    )


def test_empty_summary_is_a_gap_on_the_persisted_shape():
    assert "professional_summary" in field_gaps(_persisted())


@pytest.mark.parametrize(
    "summary",
    [
        {"de": None, "en": None},
        {"de": "", "en": ""},
        {"de": "   ", "en": ""},
    ],
    ids=["nulls", "empty-strings", "whitespace"],
)
def test_every_empty_spelling_is_a_gap(summary):
    assert "professional_summary" in field_gaps(_persisted(summary))


@pytest.mark.parametrize(
    "summary",
    [
        {"de": "Produktionsleiter mit 12 Jahren Erfahrung.", "en": None},
        {"de": None, "en": "Operations lead, 12 years."},
    ],
    ids=["de-only", "en-only"],
)
def test_one_language_is_enough_to_close_the_gap(summary):
    assert "professional_summary" not in field_gaps(_persisted(summary))


def test_the_score_and_the_agenda_now_agree():
    """The defect was a DISAGREEMENT, so the regression test is the agreement.

    Whatever the summary is, `professional_summary` is on the agenda exactly
    when the score does not credit it.
    """
    from applire.schemas.profile import _has_meaningful_data

    for summary in (
        None,
        {"de": None, "en": None},
        {"de": "", "en": ""},
        {"de": "Ein Satz.", "en": None},
        {"de": None, "en": "A sentence."},
    ):
        model = MasterProfileData.model_validate(
            {**_persisted(summary), "professional_summary": summary or {}}
        )
        scored_present = _has_meaningful_data(model, "professional_summary")
        on_agenda = "professional_summary" in field_gaps(_persisted(summary))
        assert scored_present != on_agenda, (
            f"score and agenda disagree for {summary!r}: "
            f"present={scored_present}, gap={on_agenda}"
        )


def test_na_fields_still_suppresses_it():
    """The user said "not applicable" — that must still silence the question."""
    profile = _persisted()
    profile["_meta"] = {"na_fields": ["professional_summary"]}
    assert "professional_summary" not in field_gaps(profile)


def test_no_work_experience_means_no_summary_question():
    """Unchanged behaviour: the tail is only emitted for a profile that has
    something to summarise (parity with gap_detector_mode_c)."""
    empty = MasterProfileData.model_validate(
        {"personal_info": {"name": "Marcus Schmidt"}}
    ).model_dump(mode="json")
    assert field_gaps(empty) == []


def test_mode_c_now_receives_the_question():
    """#683: Mode C is the ONLY surface that asks for the summary. It reads the
    same list — so the fix is what makes the question askable at all."""
    from applire.services.interview_graph import gap_detector_mode_c

    assert "professional_summary" in gap_detector_mode_c(_persisted())


def test_the_health_hub_lists_it():
    """US179 pins the hub's count and the interview's question count to one
    function — so the hub must show the gap the interview will ask."""
    from applire.services.profile.health import assess_health

    profile = MasterProfileData.model_validate(_persisted())
    health = assess_health(profile)
    assert "professional_summary" in health.completeness.field_gaps


def test_summary_present_tolerates_every_persisted_shape():
    assert summary_present(None) is False
    assert summary_present({}) is False
    assert summary_present({"de": None, "en": None}) is False
    assert summary_present({"de": "", "en": "  "}) is False
    assert summary_present({"de": "text", "en": None}) is True
    assert summary_present("a legacy bare string") is True
    assert summary_present("   ") is False
    from applire.schemas.profile import ProfessionalSummary

    assert summary_present(ProfessionalSummary()) is False
    assert summary_present(ProfessionalSummary(en="hi")) is True
