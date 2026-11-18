# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The access log is written at INFO and carries ids only (ADR-087 cl. 9 amended
2026-11-18). ``auth/logfilter.py`` keeps a query value only for a parameter on
its allow-list, and only when the value has the shape of an id, number, date or
code; every other value, and every name the application does not read, is
redacted. This census keeps the allow-list honest: it is exactly the route
table's structured parameters (non-string types, and the string parameters
named below with the reason they hold no text) plus the signed link's, and no
credential or text parameter is ever on it.

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

import pytest
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


def _route_table_params() -> dict[str, bool]:
    """Every query parameter name → whether the application reads it as a string."""
    from applire.main import app

    def walk(dep):
        yield dep
        for sub in dep.dependencies:
            yield from walk(sub)

    found: dict[str, bool] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in walk(route.dependant):
            for field in dep.query_params:
                annotation = getattr(field, "annotation", None) or field.field_info.annotation
                found[field.alias] = found.get(field.alias, False) or _stringy(annotation)
    return found


CREDENTIAL_OR_TEXT = {"q", "next", "code", "state", "sig", "token", "access_token", "setup_token"}


def test_the_allow_list_is_the_structured_census():
    params = _route_table_params()
    assert {"q", "next", "code", "state"} <= set(params), "census blind: known parameters missing"
    structured = {name for name, stringy in params.items() if not stringy}
    structured |= {name for name, stringy in params.items() if stringy and name in STRUCTURED}
    structured |= {n for names in HAND_READ.values() for n in names if n in STRUCTURED}
    assert set(logfilter.STRUCTURED_QUERY_PARAMS) == structured


def test_no_credential_or_text_parameter_is_on_the_allow_list():
    assert CREDENTIAL_OR_TEXT & set(logfilter.STRUCTURED_QUERY_PARAMS) == set()
    assert CREDENTIAL_OR_TEXT & set(STRUCTURED) == set()


def test_every_string_parameter_is_redacted_or_has_a_reason():
    params = _route_table_params()
    for name, stringy in params.items():
        if stringy and name in logfilter.STRUCTURED_QUERY_PARAMS:
            assert name in STRUCTURED, name


def test_hand_read_query_parameters_are_known():
    readers = {
        str(p.relative_to(_BACKEND)) for p in _BACKEND.rglob("*.py")
        if "query_params" in p.read_text(encoding="utf-8")
    }
    assert readers == set(HAND_READ)
    for names in HAND_READ.values():
        for name in names:
            assert name in CREDENTIAL_OR_TEXT or name in STRUCTURED, name


CANARY = "Rotkohl7901"
CANARY_SHAPED = "Kanarien-Weberit-Schichtleitung-4711"  # fits a loose "code" shape
CURSOR = "MjAyNi0xMS0xOFQxMDowMDowMCswMDowMHw2ZjFjMmE5ZS0wYjdkLTRjNTUtOWE4ZS0yZjNkNGM1YjZhNzE"


@pytest.mark.parametrize(
    "target",
    [
        f"/api/applications?%71={CANARY}",  # an encoded name the application reads as q
        f"/x?%63ode={CANARY}",
        f"/api/nonexistent?jd_text={CANARY}&text={CANARY}&url={CANARY}&title={CANARY}",
        f"/api/applications?Q={CANARY}",
        f"/api/applications?status={CANARY}%20und%20mehr",  # allowed name, text value
        f"/api/applications?{CANARY}",  # a bare word
        f"/api/applications?{CANARY}=1",  # a word as a name
        f"/api/applications?status=open&q={CANARY}#{CANARY}",
        f"/api/cv/1/pdf?exp=1&uid=u&sig={CANARY}",
        f"/api/documents?status={CANARY}",
        f"/api/admin/audit?cursor={CANARY}&action={CANARY}&session_id={CANARY}&error={CANARY}",
    ]
    + [f"/x?{name}={CANARY_SHAPED}" for name in sorted(logfilter.STRUCTURED_QUERY_PARAMS)],
)
def test_text_never_survives_in_a_request_target(target):
    out = logfilter.redact(f'GET {target} HTTP/1.1')
    assert CANARY not in out and CANARY_SHAPED not in out and logfilter.REDACTED in out


#: One valid value per allow-listed parameter, as a client sends it.
VALID = {
    "action": "user.deleted",
    "active": "true",
    "actor_id": "6f1c2a9e-0b7d-4c55-9a8e-2f3d4c5b6a71",
    "basis_updated_at": "2026-11-18T10:00:00Z",
    "berufsbild_code": "26112",
    "consent": "false",
    "cursor": CURSOR,
    "cx": "0.5",
    "cy": "0.25",
    "days": "30",
    "error": "access_denied",
    "exp": "1763460000",
    "job_id": "6f1c2a9e0b7d4c559a8e2f3d4c5b6a71",
    "limit": "50",
    "page": "2",
    "page_size": "25",
    "session_id": "6f1c2a9e-0b7d-4c55-9a8e-2f3d4c5b6a71",
    "since": "2026-11-18",
    "source": "linkedin",
    "status": "ready",
    "target_user_id": "6f1c2a9e-0b7d-4c55-9a8e-2f3d4c5b6a71",
    "top_n": "5",
    "uid": "6f1c2a9e-0b7d-4c55-9a8e-2f3d4c5b6a71",
    "until": "2026-11-18T10:00:00.123+01:00",
    "user_status": "applied",
    "workflow_status": "completed",
    "zoom": "1.5",
}
SMUGGLED = "Rübe-Weberit"  # typed text; its letters lie in the base64url alphabet except ü


def _smuggling(valid: str) -> dict[str, str]:
    from urllib.parse import quote

    enc = quote(SMUGGLED)
    half = len(valid) // 2
    return {
        "appended": valid + SMUGGLED,
        "appended-in-alphabet": valid + "Weberit",
        "appended-encoded": valid + enc,
        "prefixed": SMUGGLED + valid,
        "space": valid + "+" + enc,
        "encoded-space": valid + "%20" + enc,
        "newline": valid + "%0A" + enc,
        "nul": valid + "%00" + enc,
        "inside": valid[:half] + enc + valid[half:],
        # characters a lenient decoder skips: non-alphabet bytes between valid ones
        "mixed-alphabet": valid[:half] + quote("äöü~.!") + valid[half:],
        "mixed-alphabet-ascii": valid[:half] + "~.!*(),;:@$" + valid[half:],
        "mixed-alphabet-ascii-encoded": valid[:half] + quote("~.!*(),;:@$ ", safe="") + valid[half:],
        "other-script-digits": quote("١٢٣٤"),
        "invalid-utf8": valid + "%FF%FE",
        "overlong": valid + "0" * 200,
    }


@pytest.mark.parametrize("name", sorted(VALID))
def test_a_valid_value_is_shown_and_nothing_rides_along(name):
    from urllib.parse import quote

    assert set(VALID) == set(logfilter.STRUCTURED_QUERY_PARAMS)
    shown = logfilter.redact(f"GET /x?{name}={quote(VALID[name], safe='')} HTTP/1.1")
    assert shown == f"GET /x?{name}={VALID[name]} HTTP/1.1"
    for how, value in _smuggling(VALID[name]).items():
        out = logfilter.redact(f"GET /x?{name}={value}&limit=5 HTTP/1.1")
        assert out == f"GET /x?{name}={logfilter.REDACTED}&limit=5 HTTP/1.1", (how, out)


def test_a_cursor_decodes_strictly_and_encodes_back_to_itself():
    import base64

    good = base64.urlsafe_b64decode(CURSOR + "=" * (-len(CURSOR) % 4))
    shape = logfilter.STRUCTURED_QUERY_PARAMS["cursor"]
    assert shape(CURSOR) and shape(CURSOR + "=")
    # the same bytes with a non-canonical last character (unused bits set)
    last = CURSOR[-1]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    twin = CURSOR[:-1] + alphabet[alphabet.index(last) + 1]
    assert base64.urlsafe_b64decode(twin + "=" * (-len(twin) % 4)) == good
    assert not shape(twin)
    assert not shape(CURSOR[:10] + "." + CURSOR[10:])
    assert not shape(base64.urlsafe_b64encode(b"2026-11-18|Rotkohl").decode())


def test_an_action_is_one_the_audit_log_writes():
    from applire.services.audit import ACTIONS

    shape = logfilter.STRUCTURED_QUERY_PARAMS["action"]
    assert all(shape(a) for a in ACTIONS)
    assert not shape("rotkohl.weberit_schicht") and not shape("user.deleted.rotkohl")


def test_the_enum_shapes_are_the_application_enums():
    from applire.models.application import UserStatus, WorkflowStatus
    from applire.models.cover_letter import CoverLetterStatus

    shapes = logfilter.STRUCTURED_QUERY_PARAMS
    for name, enum_cls in (("user_status", UserStatus), ("workflow_status", WorkflowStatus), ("status", CoverLetterStatus)):
        assert all(shapes[name](e.value) for e in enum_cls), name
        assert not shapes[name]("rotkohl"), name


def test_structured_values_stay_readable():
    out = logfilter.redact(
        "/api/admin/audit?since=2026-11-18T10%3A00%3A00%2B00%3A00&limit=50&user_status=applied"
        f"&cursor={CURSOR}&exp=1763460000&uid=6f1c2a9e-0b7d-4c55-9a8e-2f3d4c5b6a71&action=user.deleted"
    )
    assert logfilter.REDACTED not in out
