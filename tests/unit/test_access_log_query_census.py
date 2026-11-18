# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The access log is written at INFO and carries ids only (ADR-087 cl. 9 amended
2026-11-18). Every query parameter the API reads as a string is either redacted
by ``auth/logfilter.py`` (a secret, text a person typed, a page address) or named
below as a structured value with the reason it holds neither. A new route with a
string query parameter in neither list fails here.

The census is taken from FastAPI's route table (every ``Query`` parameter of
every route, dependencies included) plus the one module that reads
``request.query_params`` by hand (the signed document link). The frontend's own
page addresses never reach this log; nginx writes ``$uri`` without its query
string (``test_nginx_error_log_level.py``)."""

from __future__ import annotations

import enum
import types
import typing
from pathlib import Path

from fastapi.routing import APIRoute

from applire.auth import logfilter

_BACKEND = Path(__file__).resolve().parents[2] / "backend" / "applire"

#: String query parameters that hold neither a secret nor a person's text.
STRUCTURED = {
    "action": "audit action names, the admin's filter",
    "berufsbild_code": "a KldB occupation code",
    "cursor": "base64 of a timestamp and a row id (audit paging)",
    "error": "the identity provider's OAuth error code",
    "session_id": "a review session id",
    "status": "a document status filter",
    # read by hand in auth/deps_links.py, next to the redacted ``sig``
    "exp": "a signed link's expiry (epoch seconds)",
    "uid": "a signed link's owner id",
}
#: Modules that read ``request.query_params`` directly (outside the route table).
HAND_READ = {"auth/deps_links.py": ("exp", "sig", "uid")}


def _stringy(tp: typing.Any) -> bool:
    origin = typing.get_origin(tp)
    if origin is typing.Literal:
        return False
    if origin in (typing.Union, types.UnionType, list, tuple, set, frozenset):
        return any(_stringy(a) for a in typing.get_args(tp) if a is not type(None))
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return False
    return tp is str


def _route_table_string_params() -> dict[str, set[str]]:
    from applire.main import app

    def walk(dep):
        yield dep
        for sub in dep.dependencies:
            yield from walk(sub)

    found: dict[str, set[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in walk(route.dependant):
            for field in dep.query_params:
                annotation = getattr(field, "annotation", None) or field.field_info.annotation
                if _stringy(annotation):
                    found.setdefault(field.alias, set()).add(route.path)
    return found


def _redacted() -> set[str]:
    return set(logfilter.SECRET_QUERY_PARAMS) | set(logfilter.TEXT_QUERY_PARAMS)


def test_every_string_query_parameter_is_redacted_or_named_structured():
    params = _route_table_string_params()
    assert {"q", "next", "code", "state"} <= set(params), "census blind: known parameters missing"
    unclassified = {
        name: sorted(paths) for name, paths in params.items()
        if name not in _redacted() and name not in STRUCTURED
    }
    assert unclassified == {}


def test_the_sign_in_and_text_parameters_are_redacted():
    assert {"code", "state", "sig", "token", "access_token", "setup_token"} <= set(
        logfilter.SECRET_QUERY_PARAMS
    )
    assert {"q", "next"} <= set(logfilter.TEXT_QUERY_PARAMS)


def test_no_parameter_is_both_redacted_and_called_structured():
    assert _redacted() & set(STRUCTURED) == set()


def test_hand_read_query_parameters_are_known():
    readers = {
        str(p.relative_to(_BACKEND)) for p in _BACKEND.rglob("*.py")
        if "query_params" in p.read_text(encoding="utf-8")
    }
    assert readers == set(HAND_READ)
    for names in HAND_READ.values():
        for name in names:
            assert name in _redacted() or name in STRUCTURED, name
