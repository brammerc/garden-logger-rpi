import math

import pytest

from garden_logger.config import CalibrationConfig
from garden_logger.constants import ERR_ADS1115, ERR_BATTERY, ERR_BME, ERR_LIGHT, ERR_SOIL
from garden_logger.sensors import PiSensors, battery_voltage, light_percent, soil_temperature_f


def test_light_percentage_preserves_original_equation_and_clamps():
    calibration = CalibrationConfig()
    assert light_percent(1.65, calibration) == pytest.approx(50.0)
    assert light_percent(4.0, calibration) == 100.0


def test_thermistor_midpoint_is_25_celsius():
    assert soil_temperature_f(1.65, CalibrationConfig()) == pytest.approx(77.0)


def test_battery_divider_preserves_two_to_one_ratio():
    assert battery_voltage(2.0, CalibrationConfig()) == pytest.approx(4.0)


def test_battery_range_is_validated():
    with pytest.raises(ValueError):
        battery_voltage(3.0, CalibrationConfig())


def test_meter_scale_is_applied_before_equation():
    calibration = CalibrationConfig(light_voltage_scale=1.01)
    assert light_percent(1.0, calibration) == pytest.approx(1.01 / 3.3 * 100)


def test_unavailable_hardware_returns_nulls_and_all_sensor_bits():
    sensors = PiSensors.__new__(PiSensors)
    sensors.hardware = type(
        "Hardware", (), {"light_channel": 0, "thermistor_channel": 1, "battery_channel": 2}
    )()
    sensors.calibration = CalibrationConfig()
    sensors._bme = None
    sensors._ads = None
    sensors._analog_in_type = None
    sensors._ads_pins = {}
    snapshot = sensors.read()
    assert snapshot.air_temp_f is None
    assert snapshot.light_pct is None
    assert snapshot.status_bits == ERR_BME | ERR_ADS1115 | ERR_LIGHT | ERR_SOIL | ERR_BATTERY
