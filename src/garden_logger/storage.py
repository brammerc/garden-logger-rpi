"""SQLite append-only history and durable FIFO outbox."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
from typing import IO, Iterator

from .models import Reading
from .timekeeper import TimeAnchor


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL CHECK(sequence_number >= 0),
    observed_at TEXT,
    air_temp_f REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    soil_temp_f REAL,
    light_pct REAL,
    battery_v REAL,
    wifi_rssi INTEGER,
    status_bits INTEGER NOT NULL,
    firmware_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(device_id, sequence_number)
);

CREATE TRIGGER IF NOT EXISTS observations_no_update
BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations are append-only');
END;

CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations are append-only');
END;

CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY REFERENCES observations(event_id),
    payload TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TRIGGER IF NOT EXISTS outbox_payload_must_match_history
BEFORE INSERT ON outbox
WHEN NEW.payload != (SELECT payload FROM observations WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'outbox payload must match immutable history');
END;

CREATE TRIGGER IF NOT EXISTS outbox_payload_no_update
BEFORE UPDATE OF event_id, payload, queued_at ON outbox
BEGIN
    SELECT RAISE(ABORT, 'queued payload is immutable');
END;

CREATE TABLE IF NOT EXISTS delivery_receipts (
    event_id TEXT PRIMARY KEY REFERENCES observations(event_id),
    acknowledged_at TEXT NOT NULL,
    response_code INTEGER NOT NULL CHECK(response_code >= 200 AND response_code < 300)
);
"""


CSV_FIELDS = (
    "event_id",
    "device_id",
    "sequence_number",
    "observed_at",
    "air_temp_f",
    "humidity_pct",
    "pressure_hpa",
    "soil_temp_f",
    "light_pct",
    "battery_v",
    "wifi_rssi",
    "status_bits",
    "firmware_version",
)


@dataclass(frozen=True)
class PendingPayload:
    event_id: str
    payload: str
    attempt_count: int


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self.connection.close()

    def _state(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def reserve_identity(self, device_id: str) -> tuple[int, str]:
        """Reserve a monotonic sequence; a crash may create a gap, never a duplicate."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            installation_id = self._state("installation_id")
            if installation_id is None:
                installation_id = secrets.token_hex(4)
                self.connection.execute(
                    "INSERT INTO state(key, value) VALUES('installation_id', ?)",
                    (installation_id,),
                )
            sequence = int(self._state("last_sequence") or "0") + 1
            if sequence > 4_294_967_295:
                raise OverflowError("sequence_number exhausted the telemetry v1 range")
            self.connection.execute(
                "INSERT INTO state(key, value) VALUES('last_sequence', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(sequence),),
            )
        return sequence, f"{device_id}-{installation_id}-{sequence}"

    def time_anchor(self) -> TimeAnchor | None:
        boot_id = self._state("time_boot_id")
        epoch = self._state("time_observed_epoch")
        monotonic = self._state("time_monotonic_seconds")
        if boot_id is None or epoch is None or monotonic is None:
            return None
        try:
            return TimeAnchor(boot_id, float(epoch), float(monotonic))
        except ValueError:
            return None

    def persist(
        self,
        reading: Reading,
        payload: str,
        queue_for_upload: bool,
        boot_id: str,
        monotonic_seconds: float,
    ) -> None:
        """Atomically append permanent history and enqueue the exact wire payload."""
        measurements = reading.sensors
        recorded_at = _utc_now()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO observations(
                    event_id, device_id, sequence_number, observed_at,
                    air_temp_f, humidity_pct, pressure_hpa, soil_temp_f,
                    light_pct, battery_v, wifi_rssi, status_bits,
                    firmware_version, payload, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reading.event_id,
                    reading.device_id,
                    reading.sequence_number,
                    reading.observed_at,
                    measurements.air_temp_f,
                    measurements.humidity_pct,
                    measurements.pressure_hpa,
                    measurements.soil_temp_f,
                    measurements.light_pct,
                    measurements.battery_v,
                    reading.wifi_rssi,
                    reading.status_bits,
                    reading.firmware_version,
                    payload,
                    recorded_at,
                ),
            )
            if queue_for_upload:
                self.connection.execute(
                    "INSERT INTO outbox(event_id, payload, queued_at) VALUES (?, ?, ?)",
                    (reading.event_id, payload, recorded_at),
                )
            if reading.observed_epoch is not None:
                for key, value in (
                    ("time_boot_id", boot_id),
                    ("time_observed_epoch", repr(reading.observed_epoch)),
                    ("time_monotonic_seconds", repr(monotonic_seconds)),
                ):
                    self.connection.execute(
                        "INSERT INTO state(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value),
                    )

    def pending(self, limit: int) -> list[PendingPayload]:
        rows = self.connection.execute(
            "SELECT event_id, payload, attempt_count FROM outbox "
            "ORDER BY rowid LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            PendingPayload(str(row["event_id"]), str(row["payload"]), row["attempt_count"])
            for row in rows
        ]

    def mark_attempt_failed(self, event_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE outbox SET attempt_count = attempt_count + 1, last_error = ? "
                "WHERE event_id = ?",
                (error[:500], event_id),
            )

    def acknowledge(self, event_id: str, response_code: int) -> None:
        if not 200 <= response_code < 300:
            raise ValueError("only an HTTP 2xx response can acknowledge an outbox item")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            present = self.connection.execute(
                "SELECT 1 FROM outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
            if present is None:
                return
            self.connection.execute(
                "INSERT OR IGNORE INTO delivery_receipts"
                "(event_id, acknowledged_at, response_code) VALUES (?, ?, ?)",
                (event_id, _utc_now(), response_code),
            )
            self.connection.execute("DELETE FROM outbox WHERE event_id = ?", (event_id,))

    def observation_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"])

    def pending_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM outbox").fetchone()
        return int(row["count"])

    def iter_csv_rows(self) -> Iterator[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT " + ", ".join(CSV_FIELDS) + " FROM observations ORDER BY rowid"
        )
        for row in rows:
            result = dict(row)
            for field in (
                "air_temp_f",
                "humidity_pct",
                "pressure_hpa",
                "soil_temp_f",
                "light_pct",
                "battery_v",
            ):
                if result[field] is None:
                    result[field] = "NaN"
            if result["observed_at"] is None:
                result["observed_at"] = ""
            if result["wifi_rssi"] is None:
                result["wifi_rssi"] = ""
            yield result

    def export_csv(self, output: IO[str]) -> None:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(self.iter_csv_rows())

    def raw_payload(self, event_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT payload FROM observations WHERE event_id = ?", (event_id,)
        ).fetchone()
        return None if row is None else str(row["payload"])

    def status(self) -> dict[str, object]:
        last = self.connection.execute(
            "SELECT event_id, observed_at, recorded_at FROM observations ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return {
            "database": str(self.path),
            "observations": self.observation_count(),
            "pending": self.pending_count(),
            "last_observation": None if last is None else dict(last),
        }
