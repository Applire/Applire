# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signed document links for the agent door (ADR-091 cl. 18, D-5; frozen interface F3).

``?exp=<unix>&sig=<b64url>`` with
``sig = HMAC(doc-link key, "<kind>:<doc_id>:<user_id>:<link_epoch>:<exp>")`` — the key
is derived from ``instance_state.auth.instance_secret``; ``users.link_epoch`` bumps
on agent-token revoke, disable and delete so outstanding links die with the token.
Lifetime ``AGENT_LINK_TTL_MINUTES`` (60, RD-8). REST URLs stay unsigned. Filled by 1c.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import User

#: ``cv`` → ``/api/cv/{id}/{html,pdf,docx}``; ``cover_letter`` → ``/api/cover-letter/{id}/…``.
DocumentKind = Literal["cv", "cover_letter"]


class LinkExpired(Exception):
    """``exp`` in the past or non-numeric — the route answers 410 ``link_expired``."""


def sign_document_url(kind: DocumentKind, doc_id: uuid.UUID, user: User, base: str) -> str:
    """Append ``exp``/``sig`` to the absolute document URL ``base`` for ``user``.

    ``base`` is the unsigned URL the door would otherwise return (it may already
    carry a query string). Pure function of the instance secret, the user's
    ``link_epoch`` and the clock.
    """
    raise NotImplementedError("auth.links.sign_document_url — filled by package 1c")


async def verify_document_link(
    kind: DocumentKind, doc_id: uuid.UUID, exp: str, sig: str, db: AsyncSession
) -> User | None:
    """The user a valid link was signed for, if that user is active and still owns
    the document; ``None`` for a bad signature or a foreign/missing document;
    raises ``LinkExpired`` for a past or non-numeric ``exp`` (410)."""
    raise NotImplementedError("auth.links.verify_document_link — filled by package 1c")
