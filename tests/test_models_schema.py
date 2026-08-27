import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from garden_logger.models import Reading, SensorSnapshot


def validator():
    schema = json.loads(Path("schema/telemetry-v1.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_included_example_validates():
    document = json.loads(Path("examples/reading.example.json").read_text(encoding="utf-8"))
    validator().validate(document)


def test_generated_payload_validates_and_has_no_nan():
    reading = Reading(
        event_id="garden-node-01-abcd1234-1",
        device_id="garden-node-01",
        sequence_number=1,
        observed_epoch=None,
        sensors=SensorSnapshot(air_temp_f=float("nan"), status_bits=1),
        wifi_rssi=None,
        status_bits=0xA1,
        firmware_version="3.0.0-rpi",
    )
    payload = reading.payload()
    assert "NaN" not in payload
    validator().validate(json.loads(payload))
