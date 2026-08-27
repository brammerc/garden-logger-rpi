"""TOML and environment configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlparse


DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class CycleConfig:
    interval_seconds: int = 60
    network_timeout_seconds: float = 12.0
    time_sync_timeout_seconds: float = 10.0
    http_timeout_seconds: float = 12.0
    max_uploads: int = 10


@dataclass(frozen=True)
class HardwareConfig:
    i2c_address_bme: tuple[int, ...] = (0x77, 0x76)
    i2c_address_ads1115: int = 0x48
    light_channel: int = 0
    thermistor_channel: int = 1
    battery_channel: int = 2
    analog_samples: int = 10
    analog_sample_delay_seconds: float = 0.005


@dataclass(frozen=True)
class CalibrationConfig:
    sensor_supply_v: float = 3.3
    thermistor_nominal_ohms: float = 10_000.0
    thermistor_nominal_c: float = 25.0
    thermistor_beta: float = 3950.0
    thermistor_series_ohms: float = 10_000.0
    battery_divider_top_ohms: float = 100_000.0
    battery_divider_bottom_ohms: float = 100_000.0
    light_voltage_scale: float = 1.0
    thermistor_voltage_scale: float = 1.0
    battery_voltage_scale: float = 1.0


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool = False
    api_url: str = ""
    bearer_token: str = ""


@dataclass(frozen=True)
class NetworkConfig:
    interface: str = "wlan0"


@dataclass(frozen=True)
class AppConfig:
    device_id: str
    database_path: Path
    cycle: CycleConfig = field(default_factory=CycleConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)


def _section(document: dict, name: str) -> dict:
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def _addresses(value: object) -> tuple[int, ...]:
    raw = value if isinstance(value, list) else [value]
    addresses = tuple(int(item, 0) if isinstance(item, str) else int(item) for item in raw)
    if not addresses or any(item < 0x03 or item > 0x77 for item in addresses):
        raise ValueError("hardware.bme_addresses must contain valid 7-bit I2C addresses")
    return addresses


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        document = tomllib.load(handle)

    device = _section(document, "device")
    storage = _section(document, "storage")
    cycle = _section(document, "cycle")
    hardware = _section(document, "hardware")
    calibration = _section(document, "calibration")
    telemetry = _section(document, "telemetry")
    network = _section(document, "network")

    device_id = str(device.get("id", ""))
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise ValueError("device.id must match ^[A-Za-z0-9._-]{1,64}$")

    database_path = Path(str(storage.get("database_path", "/var/lib/garden-logger/garden.db")))
    bearer_token = os.environ.get(
        "GARDEN_LOGGER_API_BEARER_TOKEN", str(telemetry.get("bearer_token", ""))
    )

    result = AppConfig(
        device_id=device_id,
        database_path=database_path,
        cycle=CycleConfig(
            interval_seconds=int(cycle.get("interval_seconds", 60)),
            network_timeout_seconds=float(cycle.get("network_timeout_seconds", 12)),
            time_sync_timeout_seconds=float(cycle.get("time_sync_timeout_seconds", 10)),
            http_timeout_seconds=float(cycle.get("http_timeout_seconds", 12)),
            max_uploads=int(cycle.get("max_uploads", 10)),
        ),
        hardware=HardwareConfig(
            i2c_address_bme=_addresses(hardware.get("bme_addresses", ["0x77", "0x76"])),
            i2c_address_ads1115=int(str(hardware.get("ads1115_address", "0x48")), 0),
            light_channel=int(hardware.get("light_channel", 0)),
            thermistor_channel=int(hardware.get("thermistor_channel", 1)),
            battery_channel=int(hardware.get("battery_channel", 2)),
            analog_samples=int(hardware.get("analog_samples", 10)),
            analog_sample_delay_seconds=float(
                hardware.get("analog_sample_delay_seconds", 0.005)
            ),
        ),
        calibration=CalibrationConfig(
            sensor_supply_v=float(calibration.get("sensor_supply_v", 3.3)),
            thermistor_nominal_ohms=float(
                calibration.get("thermistor_nominal_ohms", 10_000)
            ),
            thermistor_nominal_c=float(calibration.get("thermistor_nominal_c", 25)),
            thermistor_beta=float(calibration.get("thermistor_beta", 3950)),
            thermistor_series_ohms=float(
                calibration.get("thermistor_series_ohms", 10_000)
            ),
            battery_divider_top_ohms=float(
                calibration.get("battery_divider_top_ohms", 100_000)
            ),
            battery_divider_bottom_ohms=float(
                calibration.get("battery_divider_bottom_ohms", 100_000)
            ),
            light_voltage_scale=float(calibration.get("light_voltage_scale", 1)),
            thermistor_voltage_scale=float(
                calibration.get("thermistor_voltage_scale", 1)
            ),
            battery_voltage_scale=float(calibration.get("battery_voltage_scale", 1)),
        ),
        telemetry=TelemetryConfig(
            enabled=bool(telemetry.get("enabled", False)),
            api_url=str(telemetry.get("api_url", "")),
            bearer_token=bearer_token,
        ),
        network=NetworkConfig(interface=str(network.get("interface", "wlan0"))),
    )
    _validate(result)
    return result


def _validate(config: AppConfig) -> None:
    if config.cycle.interval_seconds <= 0:
        raise ValueError("cycle.interval_seconds must be greater than zero")
    if min(
        config.cycle.network_timeout_seconds,
        config.cycle.time_sync_timeout_seconds,
        config.cycle.http_timeout_seconds,
    ) <= 0:
        raise ValueError("cycle timeouts must be greater than zero")
    if config.cycle.max_uploads <= 0:
        raise ValueError("cycle.max_uploads must be greater than zero")
    channels = (
        config.hardware.light_channel,
        config.hardware.thermistor_channel,
        config.hardware.battery_channel,
    )
    if any(channel not in range(4) for channel in channels) or len(set(channels)) != 3:
        raise ValueError("ADS1115 channels must be distinct values from 0 through 3")
    if config.hardware.analog_samples <= 0:
        raise ValueError("hardware.analog_samples must be greater than zero")
    if config.hardware.i2c_address_ads1115 not in range(0x03, 0x78):
        raise ValueError("hardware.ads1115_address is not a valid 7-bit I2C address")
    if config.calibration.sensor_supply_v <= 0:
        raise ValueError("calibration.sensor_supply_v must be greater than zero")
    if min(
        config.calibration.thermistor_nominal_ohms,
        config.calibration.thermistor_beta,
        config.calibration.thermistor_series_ohms,
        config.calibration.battery_divider_top_ohms,
        config.calibration.battery_divider_bottom_ohms,
        config.calibration.light_voltage_scale,
        config.calibration.thermistor_voltage_scale,
        config.calibration.battery_voltage_scale,
    ) <= 0:
        raise ValueError("calibration resistances, beta, and scales must be positive")
    if config.telemetry.enabled:
        parsed = urlparse(config.telemetry.api_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("telemetry.api_url must be an HTTPS URL when enabled")
