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

"""F-7 (#674, founder edge UAT 2026-09-20, `Documents/Runs/Nougat/final/
2026-09-20-founder-uat-edge-agent-channel.md`) — ``merge_status`` used to be a
bare truthiness check over ``not_applied``: ANY item, cosmetic or not, flipped
the status to ``partial``. A LinkedIn import that only carried
``no_op_carried_entry`` items (languages the vault already had) then reported
``partial``, and ``mcp/AGENT_GUIDE.md`` told the agent to report that as a
loss to the candidate — a false alarm over the documented false-positive
shape (``ImportNotApplied``'s own docstring: "the known false-positive shape,
not a bug").

Pinned here:

1. ``LOSS_REASONS``/``is_loss_reason`` — the FACT classification (ADR-062
   clause 1) of which of the ten ``ImportNotApplied.reason`` values are
   evidence that incoming content did not reach the vault.
2. ``_merge_status_from_not_applied`` (the single derivation seam, ADR-066) —
   an all-bookkeeping list reads ``applied`` with ``not_applied`` still
   listing every item and ``not_applied_loss_count == 0``; one loss item
   flips it to ``partial``; a mixed list counts only the loss items; an empty
   list reads ``applied``.
3. A lockstep test over ``typing.get_args`` of the ``reason`` Literal, so an
   eleventh reason cannot be added to the schema without a classification
   decision landing in the same PR.
"""

from __future__ import annotations

import typing

from applire.schemas.profile import ImportNotApplied, LOSS_REASONS, is_loss_reason
from applire.services.profile import _merge_status_from_not_applied

# The ten reasons this file pins against, split exactly as the schema's own
# per-value docstrings (`schemas/profile.py:730-795`) and the founder's F-7
# ruling classify them.
_LOSS = {
    "op_rejected",
    "summary_populated",
    "no_write",
    "confirmation_held",
    "confirmation_unresolvable",
}
_NOT_LOSS = {
    "no_op_carried_entry",
    "no_write_already_known",
    "no_write_question_only",
    "confirmation_discarded",
    "confirmation_already_present",
}


def _item(reason: str, label: str = "x", section: str | None = "skills") -> ImportNotApplied:
    return ImportNotApplied(section=section, label=label, reason=reason)


class TestLossReasonClassification:
    def test_loss_reasons_matches_the_founder_ruling_exactly(self):
        assert LOSS_REASONS == _LOSS

    def test_every_declared_loss_reason_is_loss(self):
        for reason in _LOSS:
            assert is_loss_reason(reason) is True, reason

    def test_every_declared_not_loss_reason_is_not_loss(self):
        for reason in _NOT_LOSS:
            assert is_loss_reason(reason) is False, reason

    def test_none_is_not_loss(self):
        assert is_loss_reason(None) is False

    def test_lockstep_every_schema_reason_value_is_classified(self):
        """An eleventh `reason` value added to the Literal without updating
        `LOSS_REASONS`/`_LOSS`/`_NOT_LOSS` fails this test rather than
        silently defaulting to "not loss" — the mutation kill for the
        classification set itself, not just for the derivation."""
        reason_field = ImportNotApplied.model_fields["reason"]
        declared = set(typing.get_args(reason_field.annotation))
        assert declared, "could not read the reason Literal's args"
        assert declared == _LOSS | _NOT_LOSS
        for reason in declared:
            expected = reason in _LOSS
            assert is_loss_reason(reason) is expected, reason


class TestMergeStatusFromNotApplied:
    def test_empty_list_is_applied(self):
        status, count = _merge_status_from_not_applied([])
        assert status == "applied"
        assert count == 0

    def test_only_no_op_carried_entry_items_stay_applied_but_receipt_is_untouched(self):
        """The captured shape from the founder's edge UAT: a LinkedIn import
        of two ALREADY-PRESENT languages. `not_applied` keeps listing both —
        the receipt is a fact channel, not filtered — but `merge_status` no
        longer reads `partial` for a false-positive-shaped, non-loss batch."""
        items = [
            _item("no_op_carried_entry", label="English", section="languages"),
            _item("no_op_carried_entry", label="German", section="languages"),
        ]
        status, count = _merge_status_from_not_applied(items)
        assert status == "applied"
        assert count == 0
        # not_applied itself is a separate list the caller still owns in full —
        # this function must not mutate or filter it.
        assert len(items) == 2

    def test_one_op_rejected_item_is_partial(self):
        status, count = _merge_status_from_not_applied([_item("op_rejected")])
        assert status == "partial"
        assert count == 1

    def test_mixed_list_counts_only_the_loss_items(self):
        items = [
            _item("no_op_carried_entry", label="English", section="languages"),
            _item("op_rejected", label="bad-op"),
            _item("confirmation_already_present", label="Acme Corp"),
            _item("no_write", label="stated but not recorded"),
        ]
        status, count = _merge_status_from_not_applied(items)
        assert status == "partial"
        assert count == 2  # op_rejected + no_write; the other two are bookkeeping

    def test_all_five_not_loss_reasons_together_stay_applied(self):
        items = [_item(reason) for reason in sorted(_NOT_LOSS)]
        status, count = _merge_status_from_not_applied(items)
        assert status == "applied"
        assert count == 0

    def test_all_five_loss_reasons_together_count_all_five(self):
        items = [_item(reason) for reason in sorted(_LOSS)]
        status, count = _merge_status_from_not_applied(items)
        assert status == "partial"
        assert count == 5
