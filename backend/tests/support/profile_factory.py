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

"""Building `MasterProfile` fixtures under the ADR-063 clause-6 strict guard.

Since #480 PR 9 the guard raises on any `profile_json` write that is not routed
through `commit_ops` or authorised by the `authorized_profile_write()` token,
and it fires on keyword construction too — so `MasterProfile(profile_json=…)`
in a test fixture is refused exactly like an unrouted production writer.

These helpers open the SAME public door production uses. They are deliberately
NOT a bypass:

* there is no test-only token and no fourth entry in
  `AUTHORIZED_PROFILE_WRITE_MODULES` — this module has no privilege the calling
  test does not already have;
* there is no autouse fixture holding the token across the suite — every
  fixture that writes the vault opts in at its own call site, so the guard still
  catches an accidental unrouted write in a test that did not mean to make one.

If you find yourself reaching for these to test *production* behaviour, the
answer is almost always `commit_ops` instead: it owns the invariant set (trail,
completeness recompute, denial floor) that a raw assignment skips.
"""
from typing import Any

from applire.models.profile import MasterProfile, authorized_profile_write


def make_master_profile(**kwargs: Any) -> MasterProfile:
    """Construct a `MasterProfile` fixture through the authorised door."""
    with authorized_profile_write():
        return MasterProfile(**kwargs)


def set_profile_json(record: MasterProfile, value: dict) -> None:
    """Assign `profile_json` on an existing fixture record, authorised."""
    with authorized_profile_write():
        record.profile_json = value
