"""No Graph sender exists here. The only executable transport is an isolated ledger.

The simulator models provider acceptance, not customer delivery. A persisted attempt
without a matching receipt is uncertain and must never be blindly retried.
"""

import json
import os
import sqlite3
from pathlib import Path

from backend.domain.service import digest


class DisabledSender:
    mode = "disabled"

    def lookup(self, key):
        return None

    def send(self, key, payload):
        raise RuntimeError(
            "Customer email sending is disabled. Mail.Send is not configured."
        )


class LocalSender:
    mode = "local_simulation"

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL)"
            )

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def lookup(self, key):
        with self._db() as db:
            row = db.execute(
                "SELECT receipt FROM receipts WHERE id=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def send(self, key, payload):
        fingerprint = digest(payload)
        result = {
            "id": key,
            "fingerprint": fingerprint,
            "mode": self.mode,
            "status": "accepted_simulation",
            "recipient": payload["recipient"],
        }
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT fingerprint, receipt FROM receipts WHERE id=?", (key,)
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise RuntimeError(
                        "A send identity cannot be reused for different content."
                    )
                return json.loads(previous[1])
            db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?)",
                (key, fingerprint, json.dumps(payload), json.dumps(result)),
            )
        return result


def configured_sender():
    if os.getenv("RIVET_ROUTINE_STATUS_TRANSPORT", "disabled") != "local_simulation":
        return DisabledSender()
    path = os.getenv("RIVET_ROUTINE_STATUS_SIMULATOR_DB", "")
    if not path or not Path(path).is_absolute():
        return DisabledSender()
    return LocalSender(path)
