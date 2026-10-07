# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#688 (WP-M) — the admin panel's qualification data agrees with itself and with the guide.

``backend/applire/data/model_qualification.json`` is read by the admin settings
panel (CONTRACT-CHANGE MD2-6) and written by hand from the measurements in
``docs/llm-models.md``. Two drifts would mislead an admin silently: an entry
the reader cannot interpret (a typo in ``qualification`` reads as a valid
value nowhere and falls through to the enum's validation at request time), and
an entry the public guide does not back.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "backend" / "applire" / "data" / "model_qualification.json"
GUIDE = REPO_ROOT / "docs" / "llm-models.md"


def _data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_every_entry_is_readable_by_the_panel():
    from applire.schemas.admin import Qualification
    from applire.services.instance_settings import PROVIDERS
    from typing import get_args

    data = _data()
    top = date.fromisoformat(data["as_of"])
    seen = set()
    for entry in data["entries"]:
        assert entry["provider"] in PROVIDERS, entry
        assert entry["match"] in ("exact", "contains"), entry
        assert entry["qualification"] in get_args(Qualification), entry
        assert entry["qualification"] != "unmeasured", "an unmeasured model has no entry"
        assert (entry.get("reason") or "").strip(), entry
        measured = date.fromisoformat(entry["as_of"])
        assert measured <= top, entry
        key = (entry["provider"], entry["model"].lower())
        assert key not in seen, f"duplicate entry {key}"
        seen.add(key)
    assert max(date.fromisoformat(e["as_of"]) for e in data["entries"]) == top


def test_every_entry_is_named_in_the_public_guide():
    guide = GUIDE.read_text(encoding="utf-8")
    for entry in _data()["entries"]:
        # The guide names models by their short name ("claude-haiku-4.5"), the
        # data by the provider's id ("anthropic/claude-haiku-4.5").
        short = entry["model"].split("/")[-1]
        short = re.sub(r"-instruct$", "", short)
        candidates = {entry["model"], short, short.replace("-2512", "").replace("-2603", "")}
        assert any(c in guide for c in candidates), f"{entry['model']} is not in docs/llm-models.md"
