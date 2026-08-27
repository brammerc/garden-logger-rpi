# Garden Logger — Raspberry Pi edition

This repository ports the ESP8266 garden logger to Raspberry Pi OS while preserving
the `garden.telemetry.v1` payload and the included
[observation-cycle contract](docs/PORTING.md).

Each systemd timer activation runs exactly one bounded cycle:

1. Open I2C sensors and SQLite state.
2. Check network reachability and wait a finite time for OS clock synchronization.
3. Select synchronized UTC, a same-boot monotonic estimate, or no timestamp.
4. Read one BME280/ADS1115 snapshot and apply the original validation equations.
5. Append immutable history and the exact JSON payload to SQLite in one transaction.
6. Attempt up to ten oldest payloads, deleting from the outbox only after HTTP 2xx.
7. Close resources; systemd schedules the next cycle.

The database uses WAL mode and `synchronous=FULL`. A power loss may cause a sequence
gap, and a loss immediately after the server accepts a payload may cause a duplicate
upload. It cannot silently drop an accepted local reading. The receiver must therefore
upsert on `event_id`.

## Target and enclosure check

The software targets a Raspberry Pi 4 Model B running 64-bit Raspberry Pi OS Bookworm.
The Pi 4 Model B was introduced in 2019, not 2018. A board marked or purchased as a
2018 Model B is likely a Raspberry Pi 3 Model B+. Confirm before installation:

```sh
tr -d '\0' </proc/device-tree/model; echo
```

The commonly sold older Eleduino acrylic/aluminum fan enclosures were made for the
Pi 2/3 B connector layout. Their 40-pin header is usable, but the HDMI and power-port
openings do not match a Pi 4. Do not force a Pi 4 into an incompatible metal case.
Use a Pi 4-compatible Eleduino enclosure or keep the existing enclosure with the Pi
model it was designed for. This logger also runs on a Pi 3 B+ because it uses the same
Linux/Blinka I²C APIs.

The Eleduino case is not weatherproof. Keep it indoors or inside a ventilated,
weather-rated outer cabinet. Add cable glands and drip loops for garden sensor leads;
do not seal the fan intake.

## Wiring

Enable I²C with `sudo raspi-config nonint do_i2c 0`, then power down before wiring.
All sensor logic remains 3.3 V.

| Raspberry Pi physical pin | Signal | Peripheral |
| ---: | --- | --- |
| 1 | 3.3 V | BME280 VIN and ADS1115 VDD |
| 3 | GPIO2 / SDA1 | BME280 SDA and ADS1115 SDA |
| 5 | GPIO3 / SCL1 | BME280 SCL and ADS1115 SCL |
| 9 | Ground | All sensor grounds |
| ADS1115 A0 | Analog input | Light output |
| ADS1115 A1 | Analog input | Thermistor-divider midpoint |
| ADS1115 A2 | Analog input | Battery-divider midpoint |

The ADS1115 remains at `0x48`, gain 1 (±4.096 V, 0.125 mV/count), 128 samples/s.
Although the ADC range is ±4.096 V, no input may exceed its 3.3 V supply. Ten
single-ended samples are averaged per channel.

Keep the case fan on its manufacturer-specified power pins, electrically separate
from the sensor rail. A two-wire fan needs no logger GPIO. Verify its label before
powering it: some older Eleduino kits included a 12 V-rated 25 mm fan that was run
more slowly from 5 V. Do not connect a fan to the 3.3 V sensor supply.

The analog circuits are unchanged:

- Light: `light_pct = clamp(A0_voltage / 3.3 * 100, 0, 100)`.
- Thermistor: `3.3 V -> 10K fixed -> A1 -> 10K NTC -> GND`.
- Battery: `BAT -> 100K -> A2 -> 100K -> GND`.

The battery field retains the telemetry-v1 0–5 V meaning. A nominal 5.1 V Pi supply
can fall outside that contract; monitor the original ≤5 V battery source, not the Pi
USB-C rail, unless a future schema explicitly changes the range.

## Installation

On the Pi, clone/copy this repository and run:

```sh
sudo raspi-config nonint do_i2c 0
sudo ./scripts/install.sh
sudoedit /etc/garden-logger/config.toml
sudoedit /etc/garden-logger/secrets.env
sudo systemctl enable --now garden-logger.timer
```

The installer creates an isolated virtual environment under `/opt/garden-logger`, a
locked-down `garden-logger` service account, configuration under
`/etc/garden-logger`, and persistent data under `/var/lib/garden-logger`.

Set `telemetry.enabled = true` and an HTTPS `api_url` only after local readings work.
Put the bearer token in `secrets.env`, not TOML or Git. Python uses the OS CA trust
store and has no insecure TLS fallback.

Useful checks:

```sh
i2cdetect -y 1                         # expect 48 and 76 or 77
sudo systemctl start garden-logger.service
journalctl -u garden-logger.service -n 50 --no-pager
sudo -u garden-logger /opt/garden-logger/.venv/bin/garden-logger \
  --config /etc/garden-logger/config.toml status
```

For a no-hardware smoke test, use `cycle --simulate`. Simulation is never selected
automatically when a real sensor fails.

Export permanent history without changing it:

```sh
sudo -u garden-logger /opt/garden-logger/.venv/bin/garden-logger \
  --config /etc/garden-logger/config.toml export-csv --output /tmp/datalog.csv
```

## Time behavior

`timedatectl ... NTPSynchronized` must report `yes` before wall time is trusted. If
NTP is later unavailable in the same Linux boot, the logger advances the last trusted
timestamp using the kernel monotonic clock and sets `0x0100`. After reboot it refuses
that estimate and sets `0x0020` until NTP returns. Add a UTC-configured DS3231 through
the operating system when power-loss-resilient offline timestamps are required.

## Tests and field acceptance

Developer tests do not require Pi hardware:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

Before field deployment, complete [docs/field-acceptance.md](docs/field-acceptance.md)
and record meter measurements in [docs/calibration-log.md](docs/calibration-log.md).
The automated suite proves schema validity, calibration math, immutable history,
queue survival across reopen, exact-payload retention, retry behavior, and local
persistence before transmission. Hardware power-loss and 24-hour offline tests must
still be performed on the actual Pi/storage/power supply.
