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

"""The contact block handed to every CV template and to the .docx export.

Found while closing #228 (extraction field parity): the vault's field is
``personal_info.linkedin_url`` (``PersonalInfo``, schemas/profile.py) — there
is no ``linkedin`` field and there never was one on the model. Every one of the
seven Jinja templates and the E057 Word export render ``cv.contact.linkedin``,
so a candidate whose profile carries a LinkedIn URL got a CV without it, in
every template and every format, with nothing anywhere reporting a loss.

The legacy flat ``contact`` block (pre-migration JSONB, and the flat extraction
door's own output before ``_migrate_legacy_fields`` normalises it) does use the
bare ``linkedin`` key, so the reader accepts both. The EMITTED key stays
``linkedin`` — that is the name the templates and the docx renderer bind to.
"""

from applire.schemas.profile import MasterProfileData
from applire.services.cv import _contact_from_profile


def _dump(**personal_info) -> dict:
    return MasterProfileData.model_validate(
        {"personal_info": {"name": "Test Person", **personal_info}}
    ).model_dump(mode="json")


def test_contact_carries_linkedin_from_the_vault_field_name():
    """The stored field is linkedin_url; the templates bind to contact.linkedin."""
    contact = _contact_from_profile(_dump(linkedin_url="https://linkedin.com/in/testperson"))
    assert contact["linkedin"] == "https://linkedin.com/in/testperson"


def test_contact_still_reads_a_legacy_flat_linkedin_block():
    """A pre-migration JSONB record keys it 'linkedin' under 'contact'."""
    contact = _contact_from_profile(
        {"contact": {"name": "Test Person", "linkedin": "https://linkedin.com/in/legacy"}}
    )
    assert contact["linkedin"] == "https://linkedin.com/in/legacy"


def test_contact_omits_linkedin_when_the_profile_has_none():
    """Absent stays absent — the templates' {% if %} must stay false."""
    assert "linkedin" not in _contact_from_profile(_dump(email="t@example.org"))


def test_contact_keeps_the_other_identity_fields():
    contact = _contact_from_profile(
        _dump(email="t@example.org", phone="+49 30 1234", location="Berlin")
    )
    assert contact == {
        "name": "Test Person",
        "email": "t@example.org",
        "phone": "+49 30 1234",
        "location": "Berlin",
    }
