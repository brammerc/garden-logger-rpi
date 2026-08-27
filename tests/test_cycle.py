from dataclasses import replace

from garden_logger.config import AppConfig, CycleConfig, TelemetryConfig
from garden_logger.constants import ERR_WIFI, WARN_TIME_ESTIMATED
from garden_logger.cycle import run_cycle
from garden_logger.models import SensorSnapshot
from garden_logger.storage import Storage
from garden_logger.timekeeper import TimeChoice
from garden_logger.transport import UploadResult


class FakeSensors:
    def read(self):
        return SensorSnapshot(
            air_temp_f=70,
            humidity_pct=50,
            pressure_hpa=1000,
            soil_temp_f=68,
            light_pct=25,
            battery_v=4,
        )


class FakeNetwork:
    def __init__(self, online=True):
        self.online = online

    def wait_online(self, timeout_seconds):
        return self.online

    def wifi_rssi(self):
        return -50 if self.online else None


class FakeClock:
    def __init__(self, epoch=1_800_000_000, estimated=False):
        self.epoch = epoch
        self.estimated = estimated

    def wait_for_synchronization(self, timeout_seconds):
        return not self.estimated

    def choose(self, synchronized, anchor, interval_seconds):
        return TimeChoice(self.epoch, self.estimated, "boot-a", 100.0)


class PersistCheckingTransport:
    def __init__(self, storage):
        self.storage = storage
        self.calls = 0

    def send(self, payload):
        self.calls += 1
        # Transmission cannot happen until both permanent history and outbox exist.
        assert self.storage.observation_count() == 1
        assert self.storage.pending_count() == 1
        assert self.storage.raw_payload("node-fixed000-1") == payload
        return UploadResult(True, 200)


class DeterministicStorage(Storage):
    def reserve_identity(self, device_id):
        with self.connection:
            self.connection.execute(
                "INSERT INTO state(key,value) VALUES('last_sequence','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        return 1, f"{device_id}-fixed000-1"


def test_cycle_persists_before_upload_and_ack_keeps_history(tmp_path):
    config = AppConfig(
        device_id="node",
        database_path=tmp_path / "garden.db",
        cycle=CycleConfig(network_timeout_seconds=0.1, time_sync_timeout_seconds=0.1),
        telemetry=TelemetryConfig(True, "https://example.test/readings", "token"),
    )
    storage = DeterministicStorage(config.database_path)
    transport = PersistCheckingTransport(storage)
    result = run_cycle(
        config, storage, FakeSensors(), FakeNetwork(), FakeClock(), transport
    )
    assert result.uploaded == 1
    assert result.queued == 0
    assert transport.calls == 1
    assert storage.observation_count() == 1
    storage.close()


def test_offline_estimated_cycle_is_queued_without_transport(tmp_path):
    config = AppConfig(
        device_id="node",
        database_path=tmp_path / "garden.db",
        cycle=CycleConfig(network_timeout_seconds=0.1, time_sync_timeout_seconds=0.1),
        telemetry=TelemetryConfig(True, "https://example.test/readings", ""),
    )
    storage = Storage(config.database_path)
    result = run_cycle(
        config,
        storage,
        FakeSensors(),
        FakeNetwork(online=False),
        FakeClock(estimated=True),
        transport=None,
    )
    assert result.status_bits & ERR_WIFI
    assert result.status_bits & WARN_TIME_ESTIMATED
    assert result.queued == 1
    storage.close()
