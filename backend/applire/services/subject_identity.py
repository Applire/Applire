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

"""Subject-identity hashing for the ADR-076 clause-3 terminal reviews.

ONE canonicalisation for every document mount (#538 CV, #539 letter): the
``REVIEW_SUBJECT_IDENTITY`` instrument proves "the subject the terminal verdict
was rendered over IS the delivered content" by comparing this hash at verdict
time and at delivery time. Two mounts with two private canonicalisations would
eventually diverge in exactly the way the instrument exists to detect, so the
hash lives here and the services delegate (ADR-066: one implementation shape
per capability).
"""

import hashlib
import json


def subject_hash(content: dict) -> str:
    """Canonical content hash of a delivered-document state.

    Hashes the FULL persisted content dict (canonical JSON, sorted keys) —
    ``tailored_data`` on the CV mount, ``letter_data`` on the letter mount.
    ``default=str`` absorbs non-JSON scalars (UUIDs, dates) the ORM may hold.
    """
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
