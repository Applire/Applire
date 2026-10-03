# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Identity tables (ADR-091; frozen interface F11) — classes added by package 1a.

``auth_sessions``, ``personal_tokens``, ``auth_links`` and ``reauth_grants``
(migration ``0072``). They carry ``user_id`` but are **not** ``__owned__``: they are
read before a user context exists (cookie/token/link resolution) and belong to
``applire.ownership.IDENTITY_TABLES`` (ADR-092 cl. 3).
"""
