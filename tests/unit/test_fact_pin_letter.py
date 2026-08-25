# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""ADR-077 clauses 2 + 3 + 5 — the letter side of fact pins.

The letter has no deterministic bullet model: reach is the PINNED FACTS
block + `positioning_requested["pinned_facts"]`, presence is a measurement
over the paragraphs, a truth-floor deletion of a pin carrier escalates to a
report entry naming the floor (SF-PIN.6), and the condense-regenerate names
the pins in its REQUIRED-content list.
"""

import uuid

from applire.schemas.application import FactPin
from applire.services.ats_audit import _audit_letter_text

QUOTE = "Cut deployment time by 70% across 12 teams"


def _pin(quote=QUOTE, targets=None, stale=False) -> FactPin:
    return FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type="work",
        entry_id="w1",
        quote=quote,
        targets=targets or ["cv", "letter"],
        stale=stale,
    )


def _letter(paragraphs) -> dict:
    return {"body": {"paragraphs": list(paragraphs)}}


def test_letter_report_measures_presence_over_the_paragraphs():
    report = _audit_letter_text(
        "text",
        _letter([f"In my last role I {QUOTE.lower()}."]),
        [],
        None,
        page_count=1,
        pins=[_pin(), _pin(quote="Absent fact nobody wove in")],
    )
    by_quote = {e.quote: e for e in report.pinned_facts}
    assert by_quote[QUOTE].present is True
    assert by_quote["Absent fact nobody wove in"].present is False


def test_cv_only_pins_do_not_appear_on_the_letter_report():
    report = _audit_letter_text(
        "text", _letter(["Hello."]), [], None, page_count=1,
        pins=[_pin(targets=["cv"])],
    )
    assert report.pinned_facts == []


def test_truth_floor_hit_is_named_on_the_report_entry():
    pin = _pin()
    report = _audit_letter_text(
        "text", _letter(["The sentence is gone."]), [], None, page_count=1,
        pins=[pin],
        truth_floor_hits={pin.pin_id},
    )
    entry = report.pinned_facts[0]
    assert entry.present is False
    assert entry.removed_by_truth_floor is True


def test_classify_signal_has_a_pinned_fact_cue():
    from applire.services.review_compliance import SignalClass, classify_signal

    assert (
        classify_signal("the pinned fact 'X' was not delivered")
        is SignalClass.UNADDRESSED_REQUIREMENT
    )


def test_condense_prompt_names_the_pinned_quotes_as_required():
    from applire.prompts.cover_letter import build_condense_prompt

    letter = _letter(["Some long paragraph."])
    prompt = build_condense_prompt(
        letter, 250, 2, 2, pinned_quotes=[QUOTE]
    )
    assert QUOTE in prompt


def test_positioning_entry_lists_every_active_letter_pin():
    from applire.services.pin_reach import pinned_facts_positioning_entry

    pins = [
        _pin(),
        _pin(quote="Second fact", targets=["letter"]),
        _pin(quote="CV only", targets=["cv"]),
        _pin(quote="Stale one", stale=True),
    ]
    entry = pinned_facts_positioning_entry(pins)
    assert entry["required"] is True
    assert QUOTE in entry["instruction"] and "Second fact" in entry["instruction"]
    assert "CV only" not in entry["instruction"]
    assert "Stale one" not in entry["instruction"]


def test_positioning_entry_is_none_without_letter_pins():
    from applire.services.pin_reach import pinned_facts_positioning_entry

    assert pinned_facts_positioning_entry([_pin(targets=["cv"])]) is None
