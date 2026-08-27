from io import StringIO
import sqlite3

import pytest

from garden_logger.models import Reading, SensorSnapshot
from garden_logger.storage import Storage
from garden_logger.transport import UploadResult, drain_queue


def reading(sequence=1, event_id="node-deadbeef-1"):
    return Reading(
        event_id=event_id,
        device_id="node",
        sequence_number=sequence,
        observed_epoch=1_800_000_000.0,
        sensors=SensorSnapshot(
            air_temp_f=70.0,
            humidity_pct=50.0,
            pressure_hpa=1000.0,
            soil_temp_f=68.0,
            light_pct=25.0,
            battery_v=4.0,
        ),
        wifi_rssi=-55,
        status_bits=0,
        firmware_version="test",
    )


def persist(storage, item, payload):
    storage.persist(item, payload, True, "boot-a", 100.0)


def test_history_and_exact_outbox_payload_survive_reopen(tmp_path):
    database = tmp_path / "garden.db"
    original = '{"exact":true,"spacing":"is-preserved"}'
    store = Storage(database)
    persist(store, reading(), original)
    store.close()

    reopened = Storage(database)
    assert reopened.observation_count() == 1
    assert reopened.pending_count() == 1
    assert reopened.pending(1)[0].payload == original
    assert reopened.raw_payload("node-deadbeef-1") == original
    reopened.close()


def test_history_is_database_enforced_append_only(tmp_path):
    store = Storage(tmp_path / "garden.db")
    item = reading()
    persist(store, item, item.payload())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("UPDATE observations SET status_bits=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("DELETE FROM observations")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute("UPDATE outbox SET payload='changed'")
    store.close()


def test_only_2xx_acknowledges_and_history_remains(tmp_path):
    store = Storage(tmp_path / "garden.db")
    item = reading()
    persist(store, item, item.payload())
    with pytest.raises(ValueError):
        store.acknowledge(item.event_id, 500)
    assert store.pending_count() == 1
    store.acknowledge(item.event_id, 204)
    assert store.pending_count() == 0
    assert store.observation_count() == 1
    store.close()


class ResultTransport:
    def __init__(self, results):
        self.results = iter(results)
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return next(self.results)


def test_failed_delivery_retries_same_event_after_reopen(tmp_path):
    database = tmp_path / "garden.db"
    item = reading()
    store = Storage(database)
    persist(store, item, item.payload())
    failed = ResultTransport([UploadResult(False, None, "dns timeout")])
    assert drain_queue(store, failed, 10) == 0
    assert store.pending(1)[0].attempt_count == 1
    store.close()

    reopened = Storage(database)
    accepted = ResultTransport([UploadResult(True, 200)])
    assert drain_queue(reopened, accepted, 10) == 1
    assert failed.payloads == accepted.payloads
    assert reopened.pending_count() == 0
    assert reopened.observation_count() == 1
    reopened.close()


def test_csv_uses_nan_and_blank_for_missing_values(tmp_path):
    store = Storage(tmp_path / "garden.db")
    item = Reading(
        event_id="node-deadbeef-1",
        device_id="node",
        sequence_number=1,
        observed_epoch=None,
        sensors=SensorSnapshot(),
        wifi_rssi=None,
        status_bits=0x2F,
        firmware_version="test",
    )
    store.persist(item, item.payload(), False, "boot-a", 100.0)
    output = StringIO()
    store.export_csv(output)
    assert ",,NaN,NaN,NaN,NaN,NaN,NaN,," in output.getvalue()
    store.close()
