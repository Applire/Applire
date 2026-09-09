# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ops layer — the operator's instance facts (ADR-086, arc42 §5.3.31).

One place that says every service is up, the worker ran, the disk is not full,
there is a backup, the provider is funded, and what last week cost.

Nothing here writes to the database and nothing here may raise: a monitoring
layer that can break the thing it watches is worse than none.
"""
