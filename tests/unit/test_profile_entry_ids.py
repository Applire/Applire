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

"""ADR-077 clause 1 — persisted ids on the five previously id-less vault types.

A fact pin addresses a vault entry by id. `Skill`, `Certification`,
`EducationEntry`, `Language` and `Publication` therefore carry the same
persisted-id pattern as `WorkEntry`: minted once by default_factory, kept
verbatim when the blob already has one, and stable across
parse -> model_dump(mode="json") -> parse (SF-PIN.8: an id that regenerates
per parse is not an identity).
"""
import uuid

from applire.schemas.profile import (
    Certification,
    EducationEntry,
    Language,
    Publication,
    Skill,
)

ID_LESS_BLOBS = [
    (Skill, {"name": "Python"}),
    (Certification, {"name": "AWS Solutions Architect"}),
    (EducationEntry, {"institution": "TU München", "degree": "M.Sc."}),
    (Language, {"language": "Deutsch", "level": "C2"}),
    (Publication, {"title": "A paper on things"}),
]


def test_each_type_mints_an_id_when_the_blob_has_none():
    for cls, blob in ID_LESS_BLOBS:
        entry = cls.model_validate(blob)
        assert isinstance(entry.id, str) and entry.id, cls.__name__
        uuid.UUID(entry.id)  # a real UUID, not an empty sentinel


def test_existing_id_in_blob_is_preserved_verbatim():
    for cls, blob in ID_LESS_BLOBS:
        given = str(uuid.uuid4())
        entry = cls.model_validate({**blob, "id": given})
        assert entry.id == given, cls.__name__


def test_id_is_stable_across_parse_dump_parse_roundtrip():
    for cls, blob in ID_LESS_BLOBS:
        first = cls.model_validate(blob)
        reparsed = cls.model_validate(first.model_dump(mode="json"))
        assert reparsed.id == first.id, cls.__name__
