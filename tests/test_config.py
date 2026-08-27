from pathlib import Path

import pytest

from garden_logger.config import load_config


def test_example_config_loads(monkeypatch):
    monkeypatch.setenv("GARDEN_LOGGER_API_BEARER_TOKEN", "secret-from-env")
    config = load_config(Path("config/garden-logger.example.toml"))
    assert config.device_id == "garden-node-01"
    assert config.hardware.i2c_address_ads1115 == 0x48
    assert config.hardware.i2c_address_bme == (0x77, 0x76)
    assert config.telemetry.bearer_token == "secret-from-env"


def test_enabled_telemetry_requires_https(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text(
        '[device]\nid="node"\n[telemetry]\nenabled=true\napi_url="http://example.test"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="HTTPS"):
        load_config(path)
