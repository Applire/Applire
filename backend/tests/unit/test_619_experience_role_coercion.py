# Copyright (C) 2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""#619 — a null ``role`` must not reject the entry, on ANY ExperienceBase kind.

Found by the first real import through the FLAT extraction door carrying a
Projects section (2026-08-31, `openai/gpt-5.6-luna`): the model returned three
projects with ``"role": null`` and the whole import raised ValidationError, so
opening the projects schema turned a SILENT section loss into a HARD import
failure — strictly worse than the bug #619 set out to fix.

Why null is the model's honest answer: ``role`` is the only field in the
projects block of both extraction prompts with no "or null" affordance, while
every neighbour (description, start_date, end_date, url, associated_experience)
has one. A project section that states no per-project role leaves nothing else
to say.

Why it only bit ``projects``: ``WorkEntry`` has coerced a non-str ``role`` since
#155 — but the validator was written on ``WorkEntry`` rather than on
``ExperienceBase``, which is where ``role`` is DECLARED. ProjectEntry and
VolunteerActivity extend the same base and inherited the field without its
normalisation: a rule written against one of N implementations.
"""

import pytest
from pydantic import ValidationError

from applire.schemas.profile import (
    MasterProfileData,
    ProjectEntry,
    VolunteerActivity,
    WorkEntry,
)


@pytest.mark.parametrize("cls", [WorkEntry, ProjectEntry, VolunteerActivity])
def test_a_null_role_coerces_to_empty_on_every_experience_kind(cls):
    """The regression proper: every ExperienceBase subclass, not just WorkEntry."""
    entry = cls.model_validate({"role": None})
    assert entry.role == ""


@pytest.mark.parametrize("cls", [WorkEntry, ProjectEntry, VolunteerActivity])
def test_a_real_role_is_untouched(cls):
    """The coercion must not swallow a stated role — it normalises absence only."""
    assert cls.model_validate({"role": "Teilprojektleitung"}).role == "Teilprojektleitung"


def test_the_whole_import_payload_survives_null_project_roles():
    """The shape that actually failed: a full extraction dict, three projects,
    every one of them role-less. Asserting on the entry alone would not have
    caught it — the ValidationError surfaced while validating the profile."""
    profile = MasterProfileData.model_validate(
        {
            "work_experience": [{"company": "Nordwerk Anlagenbau GmbH", "role": "Leiterin"}],
            "projects": [
                {"name": "MES-Rollout Werk Hannover", "role": None},
                {"name": "Konsolidierung der Stammdaten", "role": None},
                {"name": "Energiemonitoring Pilotlinie", "role": None},
            ],
        }
    )
    assert [p.role for p in profile.projects] == ["", "", ""]
    assert len(profile.projects) == 3


def test_a_non_string_role_that_is_not_none_also_normalises():
    """The validator's contract is 'a str or the field's own absent value' —
    pinned so a future narrowing to `is None` does not reopen the class."""
    assert ProjectEntry.model_validate({"role": 42}).role == ""
    with pytest.raises(ValidationError):
        ProjectEntry.model_validate({"name": None})  # unrelated field: still strict
