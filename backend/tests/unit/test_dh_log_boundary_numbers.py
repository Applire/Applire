# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DH item 6 (Sorbet invite-gate delivery run, 2026-11-25): the backend log printed
uvicorn's "Cancel %s running task(s), timeout graceful shutdown exceeded" with a bare
``%s``. A foreign record's arguments stay withheld — except numbers, which carry no
content."""
import logging

from applire.log_boundary import reduce_record


def _rec(msg, args):
    return logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, msg, args, None)


def test_a_foreign_count_is_formatted():
    out = reduce_record(_rec("Cancel %s running task(s), timeout graceful shutdown exceeded", (2,)))
    assert out.getMessage().startswith("Cancel 2 running task(s), timeout graceful shutdown exceeded [error_id=")


def test_a_foreign_string_argument_stays_withheld():
    out = reduce_record(_rec("Bad request from %s: %s", ("10.0.0.1", "secret body")))
    assert "secret body" not in out.getMessage() and "10.0.0.1" not in out.getMessage()
    assert out.getMessage().startswith("Bad request from %s [error_id=")


def test_an_int_subclass_with_its_own_text_stays_withheld():
    """MD-G32 X-11: only the exact numeric types are formatted in."""

    class Tagged(int):
        def __str__(self):
            return "SECRET-PERSON-TEXT"

    out = reduce_record(_rec("val %s", (Tagged(3),)))
    assert "SECRET" not in out.getMessage() and out.getMessage().startswith("val %s [error_id=")


def test_a_bool_argument_stays_withheld():
    out = reduce_record(_rec("flag %s", (True,)))
    assert "True" not in out.getMessage()
