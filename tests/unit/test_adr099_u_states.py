# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""ADV-10 (WP-C): every row the PRODUCERS stamp class U, with the shape they
write — the fixture the review surface's "Nicht geprüft" copy is tested on
(``frontend/lib/__tests__/fixtures/adr099-u-states.json``). Built from the real
producers (terminal-review outcome → check → registry stamp; a failed ATS check
on an unclean loop), never hand-written: a producer change fails this test and
the fixture is regenerated with ``APPLIRE_REGEN_U_STATES=1``."""
from __future__ import annotations

import json
import os
import pathlib

from applire.services.finding_registry import annotate_report
from applire.services.terminal_review_outcome import TerminalReviewOutcome, build_terminal_review_check

FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "frontend/lib/__tests__/fixtures/adr099-u-states.json"

_PATHS = (None, "approved", "minor_only", "exhausted", "cycle_detected", "reviewer_call_failed",
          "generator_call_failed", "review_malformed", "review_only")


def _o(path, blocking=()):
    return TerminalReviewOutcome(chain_id="cv_terminal_review", path=path, approved=path in ("approved", "minor_only"),
                                 blocking_issues=tuple(blocking), minor_issues=(), rounds=2)


def _stamped(check: dict, loop_clean) -> dict:
    report = {"checks": [check], "keywords": {}, "delivery": {"loop_clean": loop_clean, "removals": [], "fixed": []}}
    annotate_report(report)
    return report["checks"][0]


def _produced() -> list[dict]:
    rows: dict[tuple, dict] = {}
    outcomes = [None] + [_o(p, b) for p in _PATHS for b in ((), ("Claim A is unsupported.",))]
    for outcome in outcomes:
        c = _stamped(build_terminal_review_check(outcome).model_dump(mode="json"), True)
        if c.get("delivery_class") == "U":
            rows[(c["id"], c["status"], c["details_key"])] = c
    # A class-F check that FAILED on a loop that did not finish clean (degraded:
    # False, agent door: None) — the shape the duplicate-bullets producer writes.
    for clean in (False, None):
        c = _stamped({"id": "duplicate-bullets", "label": "Duplicate bullets", "status": "fail", "severity": "warning",
                      "details": "bullets that state the same achievement twice: …", "details_key": "duplicate-bullets",
                      "details_params": {"pairs": "„A“ ~ „A.“", "count": 1}}, clean)
        assert (c["delivery_class"], c["resolver"]) == ("U", "regenerate"), c
        rows[(c["id"], c["status"], c["details_key"])] = c
    keep = ("id", "status", "details_key", "details_params", "delivery_class", "resolver")
    return [{k: c.get(k) for k in keep} for _, c in sorted(rows.items(), key=lambda kv: str(kv[0]))]


def test_the_u_rows_the_producers_write_are_the_surface_fixture():
    produced = _produced()
    if os.environ.get("APPLIRE_REGEN_U_STATES"):
        FIXTURE.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == produced


def test_every_terminal_state_stamped_u_is_named():
    """The surface maps each of these details_keys to its copy family; a new U
    state from the producer must reach that map (and this list) first."""
    keys = {r["details_key"] for r in _produced() if r["id"] == "terminal-review"}
    assert keys == {"terminal-review-not-run", "terminal-review-incomplete", "terminal-review-unnamed",
                    "terminal-review-unverified", "terminal-review-unverified-clean"}, keys
