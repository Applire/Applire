# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The migration chain has exactly one head (Strawberry F10).

W0 commits 0071..0079 as no-op stubs chained on 0070 so every package builds on
a real head; two packages adding a revision on the same parent would fork the
chain and ``alembic upgrade head`` would refuse to run in the lifespan.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
STRAWBERRY_CHAIN = ["0070", "0071", "0072", "0073", "0074", "0075", "0076", "0077", "0078", "0079"]


def _script() -> ScriptDirectory:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_single_alembic_head():
    heads = _script().get_heads()
    assert len(heads) == 1, f"alembic chain forked — heads: {heads}"


def test_strawberry_revisions_chain_linearly_from_0070():
    script = _script()
    for parent, child in zip(STRAWBERRY_CHAIN, STRAWBERRY_CHAIN[1:]):
        rev = script.get_revision(child)
        assert rev is not None, f"revision {child} missing"
        assert rev.down_revision == parent, f"{child} must follow {parent}, follows {rev.down_revision}"


def test_head_is_at_or_after_the_strawberry_range():
    # Later packages may append after 0079; nothing may branch inside it.
    script = _script()
    head = script.get_current_head()
    walked = [r.revision for r in script.walk_revisions("base", head)]
    for rev in STRAWBERRY_CHAIN:
        assert rev in walked, f"{rev} is not on the path to head {head}"
