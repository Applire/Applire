# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G36 fix round 3, X-3a (adv re-probe 1 on e7e7cfa3): ``cv.recorded_name`` and
``cv.level_code_changed`` report a value the WRITER returned that the COMPOSER refused.
Every path that raises them delivers the stored value, so the page is true and the line
holds nothing. Proved by composing a CV whose writer returned another role, employer,
degree, institution, language name, budget figure and level code, and reading the
delivered document. Item 7: every listed key and every pipeline note the agent relays has
its own sentence; the fallback for a line that holds nothing is neutral. Synthetic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
sys.path.insert(0, str(Path(__file__).parent))

from applire.services import point_rules  # noqa: E402
from applire.services.cv_v2 import ComposeNotes, compose_v2, v2_checks  # noqa: E402
from test_dh_fix2_point_rules import _held  # noqa: E402
from test_e068_cv_pipeline_v2 import E1, L2, W1, _ctx, _prose, _vault  # noqa: E402

WRITER = {
    "role": "Production Manager", "company": "Nivalo Holding SE", "budget": "approx. €9 million",
    "degree": "Doktor der Technik", "institution": "ETH Zürich", "language": "Englishe", "level": "C2",
}


def _upsold():
    vault = _vault()
    vault["work_experience"][0]["budget_managed"] = "ca. 6 Mio. €"
    prose = _prose()
    w = prose["work"][0]
    w["role"], w["company"], w["budget_managed"] = WRITER["role"], WRITER["company"], WRITER["budget"]
    e = prose["education"][0]
    e["degree"], e["institution"] = WRITER["degree"], WRITER["institution"]
    lang = next(x for x in prose["languages"] if x["id"] == L2)
    lang["language"], lang["level"] = WRITER["language"], WRITER["level"]
    return prose, vault


def test_every_path_of_the_two_keys_delivers_the_stored_value():
    prose, vault = _upsold()
    notes = ComposeNotes()
    delivered = compose_v2(prose, vault, notes=notes)
    w, e = delivered.work_history[0], delivered.education[0]
    en = delivered.languages[1]  # the vault's second language (L2)
    assert (w.role, w.company, w.budget_managed) == ("Leiter Produktion", "Nivalo GmbH", "ca. 6 Mio. €")
    assert (e.degree, e.institution) == ("Diplom-Ingenieur", "TU Wien")
    assert (en.language, en.level) == ("Englisch", "C1")
    page = json.dumps(delivered.model_dump(mode="json"), ensure_ascii=False)
    for value in WRITER.values():
        assert value not in page, value
    # ... and the checks still report the writer's attempt under the two keys
    keys = {f.check for f in v2_checks(prose, delivered, notes, _ctx(profile_json=vault))}
    assert {"cv.recorded_name", "cv.level_code_changed"} <= keys
    locs = [loc for loc, *_ in notes.names_not_recorded]
    assert {f"work {W1} role", f"work {W1} company", f"work {W1} budget_managed",
            f"education {E1} degree", f"education {E1} institution", f"languages {L2} language"} <= set(locs)


def test_the_two_keys_hold_nothing_and_do_not_say_the_page_differs():
    for key in ("cv.recorded_name", "cv.level_code_changed"):
        assert key in point_rules.ADVICE_CODE_KEYS and key not in point_rules.TRUTH_CODE_KEYS
        assert _held([], [], [key]) == 0
        assert _held(["an unbacked claim."], [("an unbacked claim.", "truth")], [key]) == 1
    level = point_rules.plain_sentence({"kind": "code", "key": "cv.level_code_changed", "subjects": []})
    name = point_rules.plain_sentence({"kind": "code", "key": "cv.recorded_name", "subjects": ["Production Manager"]})
    assert "from your profile" in level and "differently" not in level
    assert name.startswith("“Production Manager” was not used")


# ── item 7: a sentence for every relayed line ───────────────────────────────


def test_every_listed_key_and_every_note_key_has_its_own_sentence():
    import re

    listed = set(point_rules.TRUTH_CODE_KEYS) | set(point_rules.ADVICE_CODE_KEYS)
    assert listed - set(point_rules.CODE_SENTENCES) == set()
    src = "".join((_backend / "applire" / "services" / f).read_text(encoding="utf-8")
                  for f in ("cv_v2.py", "cv.py", "cover_letter.py"))
    note_keys = {m.group(1) for m in re.finditer(r'key="((?:cv|letter)\.[a-z_]+)"', src)
                 if 'kind="note"' in src[max(0, m.start() - 200):m.start()]}
    note_keys |= {"letter.limit_cut", "letter.limit_kept"}  # written as key=f"letter.{…}"
    assert note_keys - set(point_rules.NOTE_SENTENCES) == set()


def test_a_harmless_note_never_relays_the_flag_sentence():
    twin = {"kind": "note", "key": "cv.skill_twin_left_out", "text": "Left out of …", "subjects": []}
    assert point_rules.plain_sentence(twin).startswith("A skill was left out of the skills list")
    unknown_note = {"kind": "note", "key": "cv.some_new_note", "text": "x"}
    assert point_rules.plain_sentence(unknown_note) == point_rules.NEUTRAL_SENTENCE
    advice_without_subject = {"kind": "code", "key": "cv.skill_duplicate", "subjects": []}
    assert point_rules.plain_sentence(advice_without_subject) == point_rules.NEUTRAL_SENTENCE
    # an unlisted CODE fact is a point: its fallback still says to look
    assert point_rules.plain_sentence({"kind": "code", "key": "cv.brand_new"}) == point_rules.GENERIC_CODE_SENTENCE
