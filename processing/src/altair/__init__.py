"""Altair: automated astrophotography pre-processing (docs/SPEC.md).

This package currently implements the Hub integration of SPEC v0.8 (§17):
configuration, the SQLite catalog's Hub tables, target resolution, the
outbox, commands, config sync, reconciliation, previews and `altair index`.
The collector, storage engine, planner and PixInsight executor (SPEC phases
0-8) plug into the same catalog.
"""

__version__ = "0.8.0"
