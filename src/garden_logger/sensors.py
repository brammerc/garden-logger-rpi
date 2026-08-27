"""Raspberry Pi I2C sensor adapter and calibration rules."""

from __future__ import annotations

import math
import time
from typing import Callable

from .config import CalibrationConfig, HardwareConfig
from .constants import ERR_ADS1115, ERR_BATTERY, ERR_BME, ERR_LIGHT, ERR_SOIL
from .models import SensorSnapshot


def _in_range(value: float, minimum: float, maximum: float) -> bool:
    return math.isfinite(value) and minimum <= value <= maximum


def light_percent(voltage: float, calibration: CalibrationConfig) -> float:
    corrected = voltage * calibration.light_voltage_scale
    return min(100.0, max(0.0, corrected / calibration.sensor_supply_v * 100.0))


def soil_temperature_f(voltage: float, calibration: CalibrationConfig) -> float:
    corrected = voltage * calibration.thermistor_voltage_scale
    supply = calibration.sensor_supply_v
    if not _in_range(corrected, 0.001, supply - 0.001):
        raise ValueError("thermistor voltage is outside its valid divider range")
    resistance = calibration.thermistor_series_ohms * corrected / (supply - corrected)
    inverse_kelvin = (
        math.log(resistance / calibration.thermistor_nominal_ohms)
        / calibration.thermistor_beta
        + 1.0 / (calibration.thermistor_nominal_c + 273.15)
    )
    temp_c = 1.0 / inverse_kelvin - 273.15
    if not _in_range(temp_c, -40.0, 85.0):
        raise ValueError("thermistor temperature is outside the supported range")
    return temp_c * 9.0 / 5.0 + 32.0


def battery_voltage(voltage: float, calibration: CalibrationConfig) -> float:
    corrected = voltage * calibration.battery_voltage_scale
    divider_ratio = (
        calibration.battery_divider_top_ohms
        + calibration.battery_divider_bottom_ohms
    ) / calibration.battery_divider_bottom_ohms
    result = corrected * divider_ratio
    if not _in_range(result, 0.0, 5.0):
        raise ValueError("battery voltage is outside the telemetry range")
    return result


class PiSensors:
    """Owns one I2C bus and reads one bounded, coherent sensor snapshot."""

    def __init__(self, hardware: HardwareConfig, calibration: CalibrationConfig):
        self.hardware = hardware
        self.calibration = calibration
        self._i2c = None
        self._bme = None
        self._ads = None
        self._analog_in_type = None
        self._ads_pins: dict[int, object] = {}
        self._initialize()

    def _initialize(self) -> None:
        try:
            import board

            self._i2c = board.I2C()
        except Exception:
            return

        addresses: set[int] = set()
        locked = False
        deadline = time.monotonic() + 1.0
        try:
            while time.monotonic() < deadline:
                if self._i2c.try_lock():
                    locked = True
                    addresses = set(self._i2c.scan())
                    break
                time.sleep(0.01)
        except Exception:
            addresses = set()
        finally:
            if locked:
                self._i2c.unlock()

        try:
            import adafruit_bme280.basic as adafruit_bme280

            for address in self.hardware.i2c_address_bme:
                if address not in addresses:
                    continue
                try:
                    self._bme = adafruit_bme280.Adafruit_BME280_I2C(
                        self._i2c, address=address
                    )
                    self._bme.mode = adafruit_bme280.MODE_FORCE
                    break
                except Exception:
                    self._bme = None
        except Exception:
            self._bme = None

        try:
            import adafruit_ads1x15.ads1115 as ads_module
            from adafruit_ads1x15.analog_in import AnalogIn

            if self.hardware.i2c_address_ads1115 not in addresses:
                raise RuntimeError("ADS1115 address was not present on I2C")
            self._ads = ads_module.ADS1115(
                self._i2c,
                address=self.hardware.i2c_address_ads1115,
                gain=1,
                data_rate=128,
            )
            self._analog_in_type = AnalogIn
            self._ads_pins = {
                0: ads_module.P0,
                1: ads_module.P1,
                2: ads_module.P2,
                3: ads_module.P3,
            }
        except Exception:
            self._ads = None
            self._analog_in_type = None
            self._ads_pins = {}

    def close(self) -> None:
        if self._i2c is not None and hasattr(self._i2c, "deinit"):
            self._i2c.deinit()

    def _read_bme(self) -> tuple[float | None, float | None, float | None, int]:
        if self._bme is None:
            return None, None, None, ERR_BME
        try:
            # In forced mode the first property access triggers a conversion. The
            # driver caches its fine-temperature compensation for the following
            # humidity and pressure property reads.
            temp_c = float(self._bme.temperature)
            humidity = float(self._bme.relative_humidity)
            pressure = float(self._bme.pressure)
            if not (
                _in_range(temp_c, -40.0, 85.0)
                and _in_range(humidity, 0.0, 100.0)
                and _in_range(pressure, 300.0, 1100.0)
            ):
                raise ValueError("BME280 value outside validation bounds")
            return temp_c * 9.0 / 5.0 + 32.0, humidity, pressure, 0
        except Exception:
            return None, None, None, ERR_BME

    def _average_voltage(self, channel: int) -> float:
        if self._ads is None or self._analog_in_type is None:
            raise RuntimeError("ADS1115 is unavailable")
        analog = self._analog_in_type(self._ads, self._ads_pins[channel])
        samples: list[float] = []
        for index in range(self.hardware.analog_samples):
            value = float(analog.voltage)
            if not math.isfinite(value) or value < 0:
                raise ValueError("invalid ADS1115 sample")
            samples.append(value)
            if index + 1 < self.hardware.analog_samples:
                time.sleep(self.hardware.analog_sample_delay_seconds)
        return sum(samples) / len(samples)

    def _analog_value(
        self,
        channel: int,
        status_bit: int,
        conversion: Callable[[float, CalibrationConfig], float],
    ) -> tuple[float | None, int]:
        try:
            voltage = self._average_voltage(channel)
            if voltage > self.calibration.sensor_supply_v + 0.05:
                raise ValueError("ADS1115 input exceeded its 3.3 V supply")
            return conversion(voltage, self.calibration), 0
        except Exception:
            return None, status_bit

    def read(self) -> SensorSnapshot:
        air, humidity, pressure, status = self._read_bme()
        if self._ads is None:
            status |= ERR_ADS1115
        light, light_status = self._analog_value(
            self.hardware.light_channel, ERR_LIGHT, light_percent
        )
        soil, soil_status = self._analog_value(
            self.hardware.thermistor_channel, ERR_SOIL, soil_temperature_f
        )
        battery, battery_status = self._analog_value(
            self.hardware.battery_channel, ERR_BATTERY, battery_voltage
        )
        return SensorSnapshot(
            air_temp_f=air,
            humidity_pct=humidity,
            pressure_hpa=pressure,
            soil_temp_f=soil,
            light_pct=light,
            battery_v=battery,
            status_bits=status | light_status | soil_status | battery_status,
        )


class SimulatedSensors:
    """Explicit dry-run source; never selected silently on hardware failure."""

    def read(self) -> SensorSnapshot:
        return SensorSnapshot(
            air_temp_f=72.5,
            humidity_pct=55.0,
            pressure_hpa=1013.25,
            soil_temp_f=68.0,
            light_pct=42.0,
            battery_v=4.1,
        )

    def close(self) -> None:
        return None
