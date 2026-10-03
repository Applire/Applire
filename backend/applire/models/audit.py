# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only audit log (ADR-091 cl. 26, S-11; frozen interface F11) — class added by 1b.

``audit_events(id, at, actor_user_id NULL, action, target_user_id NULL, detail
JSONB)`` — no IP (RD-11); migration ``0073`` adds the no-UPDATE trigger. Not
``__owned__`` (instance table referencing users).
"""
