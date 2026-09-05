# Copyright (C) 2026 Tobias Rosenbaum
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

"""SF-REVIEW.5 — the Python half of the `_norm_quote` divergence detector.

ADR-081 clause 2 lets the document review surface collapse an Oracle-flagged
claim and a `keywords.present_unsupported` term into ONE row when both fold to
the same string under *the existing shared* `_norm_quote` (ADR-070 clause 1).
That fold is Python and the carve-out runs in the browser, so
``frontend/lib/norm-quote.ts`` is a PORT — ADR-066's one-implementation rule
cannot be satisfied literally across that boundary.

The risk is therefore detected rather than prevented: this test and
``frontend/lib/__tests__/norm-quote.test.ts`` read the SAME vector file and
assert the same input→output pairs. A change to either fold that is not made to
the other reddens one of the two suites.

The control's limit is stated on the FMEA row and is real: it is only as good as
the vector file's coverage. Whoever changes either fold adds a vector for the
class they changed.
"""

import json
from pathlib import Path

import pytest

from applire.services.scope_requirements import _norm_quote

_VECTORS_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "files" / "norm_quote_vectors.json"
)


def _load() -> list[dict[str, str]]:
    with _VECTORS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["vectors"]


def test_vector_file_exists_and_is_not_empty() -> None:
    """A missing or emptied vector file must fail loudly, never pass vacuously."""
    assert _VECTORS_PATH.is_file(), f"missing shared vector file: {_VECTORS_PATH}"
    vectors = _load()
    assert len(vectors) >= 20


@pytest.mark.parametrize("vector", _load(), ids=lambda v: repr(v["in"]))
def test_norm_quote_matches_the_shared_vector(vector: dict[str, str]) -> None:
    assert _norm_quote(vector["in"]) == vector["out"]


def test_two_merely_similar_terms_do_not_fold_together() -> None:
    """SF-REVIEW.3's negative direction, at the fold rather than at the surface."""
    assert _norm_quote("SAP PP") != _norm_quote("SAP PP/DS")
    assert _norm_quote("ISO 45001") != _norm_quote("ISO 9001")
