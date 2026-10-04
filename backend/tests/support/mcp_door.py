# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test support for package 4b — the MCP door (ADR-091 cl. 17/18, ADR-092 cl. 10).

* ``mcp_signing_secret`` — an **autouse** fixture: importing it into a test module
  installs a test instance secret for every test there, so the door can sign the
  document links it returns (it fails closed without one). Reset afterwards.
* ``assert_signed_document_url`` — the URL is the unsigned one plus ``exp``,
  ``uid`` and a ``sig`` that verifies for that user.
* ``bound_identity`` — bind the process agent identity for a block (and unbind).
* ``grant_posting_access`` — stub the door's posting check (``_owned_job``) for
  tests about a tool's OWN logic with a seeded job that has no application link.
  Access itself is pinned by ``tests/unit/test_cross_user_isolation.py``.
"""

from __future__ import annotations

import contextlib
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from applire.auth import links as links_module

TEST_SECRET = "test-instance-secret-4b"


@pytest.fixture(autouse=True)
def mcp_signing_secret():
    links_module.set_instance_secret(TEST_SECRET)
    try:
        yield TEST_SECRET
    finally:
        links_module.set_instance_secret(None)


def assert_signed_document_url(url: str, unsigned: str, *, user_id: uuid.UUID) -> dict:
    """``url`` == ``unsigned`` + ``?exp&uid&sig`` for ``user_id``; returns the query."""
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == unsigned, (url, unsigned)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert set(query) == {"exp", "uid", "sig"}, query
    assert query["uid"] == str(user_id), query
    assert query["exp"].isdigit() and query["sig"], query
    return query


@contextlib.contextmanager
def bound_identity(identity):
    from applire.mcp import identity as mcp_identity

    previous = mcp_identity.bound()
    mcp_identity.bind(identity)
    try:
        yield identity
    finally:
        mcp_identity.bind(previous)


def grant_posting_access(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import applire.mcp.server as server

    monkeypatch.setattr(server, "_owned_job", AsyncMock())


@pytest.fixture
def posting_access_granted(monkeypatch):
    """Opt-in (``pytest.mark.usefixtures``) form of :func:`grant_posting_access`."""
    grant_posting_access(monkeypatch)
