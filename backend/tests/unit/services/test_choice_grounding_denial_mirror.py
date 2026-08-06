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

"""ADR-064 clause 6 as amended 2026-08-05 (#347) — the denial mirror guard.

``filter_ungrounded_choices`` exempts a "denial"-tagged chip's denial clause
from term-evidence grounding (a denial names the term to deny it). #347's
charter run showed the missing mirror: nothing checked whether the CURRENT
profile *contradicts* the denial. Record 30 offered "I haven't worked with
Digitalisierung or Industrie 4.0 directly, but ..." while the profile carried
`Industrie 4.0` as a confirmed skill — one click from a false, immutable
denial.

The guard drops a denial chip whose denial clause names a cluster/JD term the
profile evidences. Longest-match scoping protects the legitimate
compound-containment shape (#351): a denial of "Tailwind CSS" must survive
profile evidence for "CSS".

These tests are the named regression net for the guard — per the
verification-hierarchy rule, each asserts behaviour that deleting or
inverting the mirror check would flip.
"""

import logging

from applire.services.choice_grounding import filter_ungrounded_choices


def _profile(**overrides):
    base = {
        "skills": [{"name": "Industrie 4.0"}, {"name": "SMED"}, {"name": "CSS"}],
        "work_experience": [
            {
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "technologies": ["MES"],
                "responsibilities": ["MES-Einfuehrung an 14 Spritzgussmaschinen"],
                "achievements": ["OEE von 61 auf 73 % gesteigert"],
            }
        ],
    }
    base.update(overrides)
    return base


_CLUSTER = {
    "id": "digital-transformation",
    "label": "Digital Transformation",
    "gaps": ["Digitalisierung", "Industrie 4.0"],
    "jd_skills": ["Digitalisierung"],
    "jd_context": "",
}


def _denial(text):
    return {"text": text, "level": "denial"}


class TestMirrorDropsContradictedDenials:
    def test_pure_denial_naming_an_evidenced_term_is_dropped(self):
        kept = filter_ungrounded_choices(
            [_denial("I haven't worked with Industrie 4.0.")],
            _CLUSTER,
            _profile(),
            "C",
        )
        assert kept is None

    def test_record_30_bridging_shape_is_dropped(self):
        # The exact #347 record-30 shape: denial clause contradicts the
        # profile, affirmative half is fully grounded (SMED is a skill).
        kept = filter_ungrounded_choices(
            [
                _denial(
                    "I haven't worked with Digitalisierung or Industrie 4.0 "
                    "directly, but I have led process improvements like SMED."
                )
            ],
            _CLUSTER,
            _profile(),
            "C",
        )
        assert kept is None

    def test_drop_is_logged_as_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            filter_ungrounded_choices(
                [_denial("I haven't worked with Industrie 4.0.")],
                _CLUSTER,
                _profile(),
                "C",
            )
        assert any("denies" in r.message and "347" in r.message for r in caplog.records)

    def test_other_choices_survive_when_the_denial_is_dropped(self):
        kept = filter_ungrounded_choices(
            [
                {"text": "I led the MES rollout using SMED.", "level": "partial"},
                _denial("I haven't worked with Industrie 4.0."),
            ],
            _CLUSTER,
            _profile(),
            "C",
        )
        assert kept == ["I led the MES rollout using SMED."]


class TestLegitimateDenialsAreKept:
    def test_pure_denial_of_an_unevidenced_term_is_kept(self):
        cluster = {
            "id": "iso",
            "label": "Quality and Safety Standards",
            "gaps": ["IFS", "BRC"],
            "jd_skills": [],
            "jd_context": "",
        }
        kept = filter_ungrounded_choices(
            [_denial("I haven't worked with IFS or BRC.")],
            cluster,
            _profile(),
            "C",
        )
        assert kept == ["I haven't worked with IFS or BRC."]

    def test_compound_containment_tailwind_css_denial_survives_css_evidence(self):
        # #351's trap, applied to this guard: the profile evidences CSS, the
        # chip denies "Tailwind CSS". "CSS" is a substring of the longer
        # mentioned term, so it must NOT count as an independently denied,
        # evidenced concept.
        cluster = {
            "id": "frontend",
            "label": "Frontend Styling",
            "gaps": ["Tailwind CSS", "CSS"],
            "jd_skills": [],
            "jd_context": "",
        }
        kept = filter_ungrounded_choices(
            [_denial("I haven't worked with Tailwind CSS.")],
            cluster,
            _profile(),
            "C",
        )
        assert kept == ["I haven't worked with Tailwind CSS."]

    def test_evidenced_term_only_in_affirmative_half_is_fine(self):
        # The mirror check is scoped to the DENIAL clause. An evidenced term
        # appearing after the pivot is an assertion, handled by the existing
        # grounding pipeline — not a contradicted denial.
        cluster = {
            "id": "iso",
            "label": "Quality and Safety Standards",
            "gaps": ["IFS", "BRC"],
            "jd_skills": [],
            "jd_context": "",
        }
        kept = filter_ungrounded_choices(
            [
                _denial(
                    "I haven't worked with IFS or BRC, but I have improved "
                    "processes with SMED."
                )
            ],
            cluster,
            _profile(),
            "C",
        )
        assert kept is not None and len(kept) == 1

    # NOTE — employer-scoped denials of an evidenced term ("not X at
    # NordPharm, but I've run it at Applire") must survive the mirror. That
    # keep-behaviour is pinned by tests/unit/test_choice_grounding.py::
    # TestEmployerScopedAttributionGuard::test_denial_naming_an_employer_to_
    # deny_is_kept (plus two sibling over-drop pins), which FAIL when the
    # mirror runs on employer-named clauses — verified during this change.

    def test_bare_string_choices_still_run_the_full_pipeline(self):
        # Backward compatibility: an untagged choice asserting an evidenced
        # term is kept by the pre-existing pipeline, untouched by the mirror.
        kept = filter_ungrounded_choices(
            ["I have worked with SMED."],
            _CLUSTER,
            _profile(),
            "C",
        )
        assert kept == ["I have worked with SMED."]
