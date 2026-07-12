# This file is part of rah-example-randomizer, an example handler for rah.
# Copyright (c) 2026 Center for Healthy Minds
# Distributed under the MIT license; see LICENSE in the project root.

"""The sqlite claim store that keeps a retry from re-rolling the dice.

A handler that times out is abandoned, not killed: the watcher counts the
attempt as a transient failure and comes around again while the first thread
may still be finishing its import. So the value a record gets has to be
decided and written down before the import runs, not after, or two attempts
could pick two different values and both send them to REDCap.

That splits the work in two. `claim` records the record and a candidate value
in one transaction and hands back whatever value is now stored, which on a
retry is the value the first attempt already chose. The caller imports that
stored value, then calls `mark_completed` once REDCap has accepted it. A row
with a null `completed_at` is a claim that was made but never confirmed done.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_CONNECT_TIMEOUT = 30.0

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS randomizations (
    record_id TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    completed_at TEXT
)
"""


@dataclass(frozen=True, slots=True)
class Claim:
    """The outcome of claiming a record: the stored value, and whether it is done.

    `value` is what the store holds for this record, which is the candidate on a
    fresh claim and the first attempt's choice on a retry. `completed` is true
    once `mark_completed` has run, so a caller can see a record is already
    randomized and stop.
    """

    value: str
    completed: bool


def claim(db_path: Path, record_id: str, candidate_value: str) -> Claim:
    """Record a candidate value for a record and return whatever is now stored.

    The insert only takes effect the first time a record is seen; a later call
    for the same record leaves the stored value alone and reads it back, so a
    retry imports the value the first attempt chose rather than a fresh roll.
    """
    with closing(sqlite3.connect(db_path, timeout=_CONNECT_TIMEOUT)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(_CREATE_TABLE)
        with connection:
            connection.execute(
                "INSERT INTO randomizations (record_id, value) VALUES (?, ?) "
                "ON CONFLICT (record_id) DO NOTHING",
                (record_id, candidate_value),
            )
            row = connection.execute(
                "SELECT value, completed_at FROM randomizations WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        value, completed_at = row
        return Claim(value=value, completed=completed_at is not None)


def mark_completed(db_path: Path, record_id: str) -> None:
    """Stamp a record's claim as confirmed, once REDCap has accepted the import."""
    with closing(sqlite3.connect(db_path, timeout=_CONNECT_TIMEOUT)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(_CREATE_TABLE)
        with connection:
            connection.execute(
                "UPDATE randomizations SET completed_at = ? WHERE record_id = ?",
                (datetime.now(UTC).isoformat(), record_id),
            )
