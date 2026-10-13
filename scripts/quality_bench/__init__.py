# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 Document Quality Bench — generation harness (package 1).

``run.py`` runs the REAL CV and cover-letter pipelines in-process against a
throwaway SQLite DB seeded from a frozen fixture and writes one directory per
generation run (the run-output contract, see ``run.py``). ``fixture.py`` loads
and validates a fixture (format: ``tests/files/quality_bench/FIXTURE_FORMAT.md``)
and is the single reader the scorers should use. ``seed.py`` turns a loaded
fixture into DB rows.

The harness imports product code and never patches it; the only thing it
repoints is the process-wide database handle, to the per-run SQLite file.
"""
