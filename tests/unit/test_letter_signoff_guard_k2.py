# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ruling K-2 (2026-09-27) — a sign-off the writer repeated as the letter's
last body paragraph is dropped; every template prints ``signature.closing``.

Fixture provenance: ``tests/files/letter_signoff_k2/k1_delivery_letter.json``
is the persisted ``letter_data`` of the K-1 delivery run's cover letter
(k1stack, 2026-09-27, synthetic case ``panel_review_case/operations_marcus_de``),
read from the stack's database dump. Its ``body.paragraphs[-1]`` is "Mit
freundlichen Grüßen" and the rendered PDF printed the sign-off twice; both
blind reviewers flagged it.
"""

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from applire.services.cover_letter import _compose_letter, _drop_trailing_signoff

_FIXTURE = Path(__file__).resolve().parents[1] / "files" / "letter_signoff_k2" / "k1_delivery_letter.json"


def _letter(paragraphs, name="Stefan Brandt"):
    return {
        "header": {"name": name},
        "recipient": {"company": "Rheinwerk"},
        "body": {"paragraphs": list(paragraphs)},
        "signature": {"name": name, "closing": "Mit freundlichen Grüßen"},
    }


_BODY = ["Sehr geehrte Damen und Herren,", "Ich bewerbe mich als Leiter Operations.",
         "Ich freue mich auf ein Gespräch."]


@pytest.mark.parametrize(
    "tail",
    [
        ["Mit freundlichen Grüßen"],
        ["Mit freundlichen Grüßen,"],
        ["Mit freundlichen Grüssen"],
        ["Beste Grüße"],
        ["Mit freundlichen Grüßen\nStefan Brandt"],
        ["Mit freundlichen Grüßen, Stefan Brandt"],
        ["Mit freundlichen Grüßen", "Stefan Brandt"],
        ["Mit freundlichen Grüßen", "", "  "],
        ["Kind regards,"],
        ["Yours sincerely"],
    ],
)
def test_a_trailing_sign_off_paragraph_is_dropped(tail):
    out = _drop_trailing_signoff(_letter([*_BODY, *tail]))
    assert out["body"]["paragraphs"] == _BODY


@pytest.mark.parametrize(
    "paragraphs",
    [
        # a sign-off inside a sentence is prose
        [*_BODY[:-1], "Ich freue mich auf ein Gespräch und verbleibe mit freundlichen Grüßen."],
        # a paragraph with more than the sign-off
        [*_BODY, "Beste Grüße aus Koblenz"],
        # a name alone without a sign-off before it is not dropped
        [*_BODY, "Stefan Brandt"],
        # the body is never emptied
        ["Mit freundlichen Grüßen"],
    ],
)
def test_anything_but_a_whole_sign_off_paragraph_stays(paragraphs):
    out = _drop_trailing_signoff(_letter(paragraphs))
    assert out["body"]["paragraphs"] == paragraphs


def test_the_delivery_runs_persisted_letter_loses_only_its_repeated_sign_off():
    letter = json.loads(_FIXTURE.read_text())
    before = list(letter["body"]["paragraphs"])
    assert before[-1] == "Mit freundlichen Grüßen"
    out = _compose_letter(
        copy.deepcopy(letter), profile_json={}, cv_data={"contact": {}}, profile=None,
        pre_gen={}, language="de", today=date(2026, 9, 27),
    )
    assert out["body"]["paragraphs"] == before[:-1]
    assert out["signature"]["closing"] == "Mit freundlichen Grüßen", "the template's own sign-off stays"


def test_the_guard_is_idempotent_at_the_composition_site():
    letter = json.loads(_FIXTURE.read_text())

    def compose(data):
        return _compose_letter(copy.deepcopy(data), profile_json={}, cv_data={"contact": {}},
                               profile=None, pre_gen={}, language="de", today=date(2026, 9, 27))

    once = compose(letter)
    assert compose(once) == once
