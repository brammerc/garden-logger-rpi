"""Platform-neutral reading and wire-format models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any

from .constants import SCHEMA_NAME


def finite_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value


def iso_utc(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SensorSnapshot:
    air_temp_f: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    soil_temp_f: float | None = None
    light_pct: float | None = None
    battery_v: float | None = None
    status_bits: int = 0


@dataclass(frozen=True)
class Reading:
    event_id: str
    device_id: str
    sequence_number: int
    observed_epoch: float | None
    sensors: SensorSnapshot
    wifi_rssi: int | None
    status_bits: int
    firmware_version: str

    @property
    def observed_at(self) -> str | None:
        return iso_utc(self.observed_epoch)

    def document(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_NAME,
            "event_id": self.event_id,
            "device_id": self.device_id,
            "sequence_number": self.sequence_number,
            "observed_at": self.observed_at,
            "measurements": {
                "air_temp_f": finite_or_none(self.sensors.air_temp_f),
                "humidity_pct": finite_or_none(self.sensors.humidity_pct),
                "pressure_hpa": finite_or_none(self.sensors.pressure_hpa),
                "soil_temp_f": finite_or_none(self.sensors.soil_temp_f),
                "light_pct": finite_or_none(self.sensors.light_pct),
                "battery_v": finite_or_none(self.sensors.battery_v),
            },
            "wifi_rssi": self.wifi_rssi,
            "status_bits": self.status_bits,
            "firmware_version": self.firmware_version,
        }

    def payload(self) -> str:
        return json.dumps(
            self.document(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
