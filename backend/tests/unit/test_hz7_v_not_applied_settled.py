# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 7 WP-V (UX audit round 3, collector line 789:255).

A typed review answer the model judged ``already_known`` is stored in the
profile. Its ``not_applied`` item (``no_write_already_known``) must not reach
the hub as an open "did not arrive" card (silence), and a stored note label
must never be cut mid-word.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EnrichmentRecord,
    ImportNotApplied,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.profile.health import assess_health  # noqa: E402
from applire.services.profile.label_text import truncate_at_word  # noqa: E402
from applire.services.profile.reconcile.witness import compute_no_write  # noqa: E402


def _profile(items) -> MasterProfileData:
    record = EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="interview", not_applied=items
    )
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "metadata": ProfileMetadata(enrichment_history=[record]).model_dump(mode="json"),
        }
    )


def _rendered(health):
    return [i for i in health.issues if i.thread == "not_applied" and i.delivery_class != "S"]


def test_already_known_item_is_not_an_open_hub_issue():
    health = assess_health(
        _profile([ImportNotApplied(section=None, label="Eskalationsinstanz fuer Kundenreklamationen", reason="no_write_already_known")])
    )
    assert _rendered(health) == []


def test_a_real_loss_next_to_an_already_known_item_stays_open():
    health = assess_health(
        _profile(
            [
                ImportNotApplied(section=None, label="stored note", reason="no_write_already_known"),
                ImportNotApplied(section=None, label="lost note", reason="no_write"),
            ]
        )
    )
    issues = _rendered(health)
    assert len(issues) == 1
    assert [i.reason for i in issues[0].not_applied_items] == ["no_write"]


def test_plain_no_write_is_still_open():
    health = assess_health(_profile([ImportNotApplied(section=None, label="x y z", reason="no_write")]))
    assert len(_rendered(health)) == 1


def test_truncate_at_word_boundary_with_ellipsis():
    text = "Eskalationsinstanz fuer Kundenreklamationen und Rueckfragen"
    out = truncate_at_word(text, 30)
    assert out.endswith("…")
    assert len(out) <= 30
    assert out[:-1].rstrip() in text
    assert text[len(out[:-1].rstrip())] == " "  # cut sits on a word boundary


def test_truncate_short_text_untouched():
    assert truncate_at_word("short note", 120) == "short note"


def test_no_write_label_is_cut_at_a_word():
    turn = "Ich leite die Eskalationsinstanz fuer Kundenreklamationen im Bereich Service " * 4
    items = compute_no_write(turn)
    assert items
    label = items[0].label
    assert len(label) <= 120
    assert label.endswith("…")
    assert not label[:-1].endswith("Kundenr")
    assert label[:-1].split()[-1] in turn.split()
