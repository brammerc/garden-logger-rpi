"""One observation cycle in the exact order defined by the porting contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import AppConfig
from .constants import (
    ERR_TIME,
    ERR_WIFI,
    FIRMWARE_VERSION,
    WARN_TIME_ESTIMATED,
)
from .models import Reading
from .transport import drain_queue


class Sensors(Protocol):
    def read(self): ...


class Clock(Protocol):
    def wait_for_synchronization(self, timeout_seconds: float) -> bool: ...
    def choose(self, synchronized, anchor, interval_seconds): ...


class Network(Protocol):
    def wait_online(self, timeout_seconds: float) -> bool: ...
    def wifi_rssi(self) -> int | None: ...


@dataclass(frozen=True)
class CycleResult:
    event_id: str
    sequence_number: int
    status_bits: int
    queued: int
    uploaded: int


def run_cycle(config: AppConfig, storage, sensors: Sensors, network: Network, clock: Clock, transport=None) -> CycleResult:
    """Measure, commit locally, then attempt delivery; never retry without a bound."""
    # Storage, buses, and sensors are initialized by the composition root before
    # this function. From here onward the ordering mirrors PORTING.md line 7.
    network_online = network.wait_online(config.cycle.network_timeout_seconds)
    synchronized = network_online and clock.wait_for_synchronization(
        config.cycle.time_sync_timeout_seconds
    )
    time_choice = clock.choose(
        synchronized, storage.time_anchor(), config.cycle.interval_seconds
    )

    snapshot = sensors.read()
    sequence, event_id = storage.reserve_identity(config.device_id)
    status_bits = snapshot.status_bits
    if not network_online:
        status_bits |= ERR_WIFI
    if time_choice.epoch is None:
        status_bits |= ERR_TIME
    elif time_choice.estimated:
        status_bits |= WARN_TIME_ESTIMATED

    reading = Reading(
        event_id=event_id,
        device_id=config.device_id,
        sequence_number=sequence,
        observed_epoch=time_choice.epoch,
        sensors=snapshot,
        wifi_rssi=network.wifi_rssi() if network_online else None,
        status_bits=status_bits,
        firmware_version=FIRMWARE_VERSION,
    )
    payload = reading.payload()

    # One FULL-synchronous SQLite transaction appends immutable history and the
    # exact payload to the outbox before any network transmission is attempted.
    storage.persist(
        reading,
        payload,
        queue_for_upload=config.telemetry.enabled,
        boot_id=time_choice.boot_id,
        monotonic_seconds=time_choice.monotonic_seconds,
    )

    uploaded = 0
    if (
        config.telemetry.enabled
        and network_online
        and time_choice.epoch is not None
        and transport is not None
    ):
        uploaded = drain_queue(storage, transport, config.cycle.max_uploads)

    return CycleResult(
        event_id=event_id,
        sequence_number=sequence,
        status_bits=status_bits,
        queued=storage.pending_count(),
        uploaded=uploaded,
    )
