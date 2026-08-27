# Field acceptance record

Device ID: ____________________  Pi model: ____________________

Date: ____________________  Operator: ____________________

## Bench checks

- [ ] `cat /proc/device-tree/model` confirms the intended board.
- [ ] The enclosure fits without stressing USB-C, micro-HDMI, or GPIO connections.
- [ ] The fan rating/wiring was checked and its intake/exhaust remain clear.
- [ ] The Pi is protected by a weather-rated outer cabinet, glands, and drip loops.
- [ ] `i2cdetect -y 1` sees ADS1115 `0x48` and BME280 `0x76` or `0x77`.
- [ ] Every generated example payload validates against `schema/telemetry-v1.schema.json`.
- [ ] A killed process leaves the current reading in history/outbox after restart.
- [ ] Abrupt power removal after persistence leaves an integrity-clean SQLite database.
- [ ] Repeated delivery of one `event_id` creates one backend observation.
- [ ] Network, DNS, TLS, sensor, and storage failures finish within the configured/systemd deadline.

## Offline soak

- [ ] Disable the network for at least 24 hours.
- [ ] Observation count increases at the configured interval.
- [ ] Pending count equals the number of upload-enabled offline readings.
- [ ] Reconnect and confirm the oldest-first backlog drains.
- [ ] Exported history has no missing persisted event IDs.

Start: ____________________  End: ____________________

Observations before: ______  after: ______  pending after drain: ______

Notes:

________________________________________________________________________________
