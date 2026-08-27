# Platform handoff

This document separates product behavior from the ESP8266 implementation so the
logger can be moved to a Raspberry Pi 4, ESP32, or another runtime without
changing stored data or the backend API.

## Behavioral contract

Implement one observation cycle in this order:

1. Initialize buses, sensors, persistent state, and local storage.
2. Attempt network connection and UTC synchronization with finite deadlines.
3. Choose an observation time. Prefer synchronized UTC; otherwise use a retained
   estimate and mark missing time when neither is trustworthy.
4. Read one coherent sensor snapshot and apply the validation/calibration rules
   below.
5. Append the snapshot to permanent local history.
6. Append the exact JSON payload to a durable pending queue.
7. Attempt a bounded number of oldest queued payloads. Remove a payload only
   after an HTTP 2xx response.
8. Release resources and sleep or wait until the next interval.

Local persistence precedes transmission. Network failure must never discard a
reading or create an unbounded retry loop. Delivery is at least once, so receivers
must use `event_id` as an idempotency key.

## Platform boundary

Keep these operations behind functions or classes when porting:

| Capability | Input/output contract | ESP8266 implementation | Raspberry Pi 4 candidate |
| --- | --- | --- | --- |
| Clock | UTC epoch plus trustworthy flag | NTP plus RTC user-memory estimate | systemd-timesyncd/chrony; optional DS3231 |
| Air sensor | temp F, RH %, pressure hPa or nulls | Adafruit BME280 forced mode | `adafruit-circuitpython-bme280` |
| Analog input | channel voltage or failure | ADS1115 at `0x48` | `adafruit-circuitpython-ads1x15` |
| History | durable append of one CSV row | FAT microSD `/datalog.csv` | local filesystem, SQLite, or external drive |
| Queue | durable FIFO of exact JSON payloads | JSONL plus two-phase rewrite | SQLite table with unique `event_id` |
| Transport | HTTPS POST; 2xx acknowledges | BearSSL + ESP8266 HTTPClient | `httpx` or `requests` with CA validation |
| Scheduler | one cycle per interval | timed deep sleep/reset | systemd timer or long-running service |
| Configuration | device/network/API/calibration | compile-time header | environment variables or TOML file |

On Linux, SQLite is preferable to rewriting JSONL: store history and an upload
state in one transaction, put a unique index on `event_id`, and update a row to
sent only after a 2xx response. Preserve JSON generated at observation time so a
later software release cannot silently reinterpret queued measurements.

## Canonical reading model

The in-memory platform-neutral model is:

```text
event_id            string, unique and immutable
device_id           string
sequence_number     non-negative integer, monotonic within retained device state
observed_at         UTC RFC 3339 string or null
air_temp_f          finite number or null
humidity_pct        finite number or null
pressure_hpa        finite number or null
soil_temp_f         finite number or null
light_pct           finite number or null
battery_v           finite number or null
wifi_rssi           integer dBm or null
status_bits         non-negative integer bitmask
firmware_version    string
```

The JSON envelope and field limits are normative in
`telemetry-v1.schema.json`. The server supplies `received_at`; devices must not.

## Calibration and validation

Preserve these equations unless the physical circuit changes:

```text
light_pct = clamp(light_voltage / 3.3 * 100, 0, 100)

thermistor_ohms = 10000 * node_voltage / (3.3 - node_voltage)
1/T_kelvin = ln(thermistor_ohms / 10000) / 3950 + 1 / 298.15
soil_temp_f = (T_kelvin - 273.15) * 9/5 + 32

battery_v = divider_voltage * (100000 + 100000) / 100000
```

Sensor validity bounds are BME280 temperature -40..85 C, humidity 0..100%,
pressure 300..1100 hPa, soil temperature -40..85 C, light 0..100%, and battery
0..5 V. A failed field is JSON `null`, CSV `NaN`, and its status bit is set.

The ADS1115 is configured for `GAIN_ONE` (+/-4.096 V, 0.125 mV/count), but its
physical input must remain between ground and its 3.3 V supply. Average ten
single-ended samples per analog observation.

## Time semantics

All device timestamps are UTC and end in `Z`. Presentation layers own timezone
conversion. On the ESP8266, an NTP-derived epoch is retained across deep-sleep
resets and advanced by the configured interval when a later wake has no network.
That estimate is lost on true power loss. Add a DS3231 when accurate offline
timestamps after power failure are required. Set status bit `0x0100` whenever a
retained estimate is used; `0x0020` means no usable timestamp exists at all.

On a Raspberry Pi, reject unsynchronized wall-clock time rather than assuming the
presence of an OS clock makes it correct. A DS3231 should be read as UTC, and NTP
should correct it when connectivity returns.

## Queue and API semantics

`POST API_URL` sends one `garden.telemetry.v1` JSON document with
`Content-Type: application/json`. An optional bearer token is sent in
`Authorization`. TLS peer verification is required.

The receiver should:

- validate against `telemetry-v1.schema.json`;
- upsert on `event_id` and return 2xx for an already accepted duplicate;
- assign `received_at` in UTC;
- preserve the original payload for auditability;
- return non-2xx for records it did not durably accept.

Ordering cannot be assumed across devices. `sequence_number` helps diagnose gaps
but is not globally unique and can restart after loss of retained state.

## Raspberry Pi 4 acceptance checklist

- Use the same ADS1115 channel assignments and equations, or document a hardware
  revision with new calibration metadata.
- Validate generated example payloads against the included JSON Schema.
- Prove a reading remains queued across process termination and power loss.
- Prove repeated delivery of one `event_id` creates one backend observation.
- Prove network, DNS, TLS, sensor, and storage failures all terminate within a
  configured deadline.
- Run at least 24 hours with the network disabled, then reconnect and drain the
  backlog without losing the append-only history.
- Compare each analog channel with a calibrated meter and record correction
  factors before field deployment.

## Deliberately deferred features

The contract leaves room for a DS3231, digital lux, soil moisture, rainfall, OTA,
and irrigation control. Add new measurement fields through a new schema version
or as explicitly optional fields; do not repurpose existing fields or status bits.
Control outputs require a separate fail-safe design and are not part of telemetry
v1.
