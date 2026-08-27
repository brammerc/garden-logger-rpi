# Analog calibration log

Measure each ADS1115 input and its source with a calibrated meter at several points.
The correction factor is `meter voltage / reported ADS voltage`. Use the fitted factor
in `config.toml`; keep the physical divider equations unchanged.

Device ID: ____________________  Meter asset/calibration: ____________________

| Date | Channel | Meter V | ADS V | Correction factor | Conditions/operator |
| --- | --- | ---: | ---: | ---: | --- |
| | A0 light | | | | |
| | A1 thermistor | | | | |
| | A2 battery | | | | |

Applied configuration:

```toml
[calibration]
light_voltage_scale = 1.0
thermistor_voltage_scale = 1.0
battery_voltage_scale = 1.0
```
