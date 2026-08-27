"""Command-line composition root for systemd and maintenance."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sqlite3
import sys

from .config import load_config
from .cycle import run_cycle
from .network import NetworkProbe
from .sensors import PiSensors, SimulatedSensors
from .storage import Storage
from .timekeeper import SystemClock
from .transport import HttpsTransport


LOG = logging.getLogger("garden_logger")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Raspberry Pi garden telemetry logger")
    parser.add_argument(
        "--config", default="/etc/garden-logger/config.toml", help="TOML configuration path"
    )
    parser.add_argument("--verbose", action="store_true")
    subcommands = parser.add_subparsers(dest="command", required=True)
    cycle = subcommands.add_parser("cycle", help="perform exactly one observation cycle")
    cycle.add_argument(
        "--simulate", action="store_true", help="use deterministic fake sensors explicitly"
    )
    subcommands.add_parser("status", help="show database and outbox status")
    export = subcommands.add_parser("export-csv", help="export immutable history as CSV")
    export.add_argument("--output", default="-", help="output file, or - for stdout")
    subcommands.add_parser("validate-config", help="load and validate configuration")
    return parser


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.command == "validate-config":
            print(_json({"valid": True, "device_id": config.device_id}))
            return 0

        storage = Storage(config.database_path)
        try:
            if args.command == "status":
                print(_json(storage.status()))
                return 0
            if args.command == "export-csv":
                if args.output == "-":
                    storage.export_csv(sys.stdout)
                else:
                    output_path = Path(args.output)
                    with output_path.open("w", encoding="utf-8", newline="") as output:
                        storage.export_csv(output)
                return 0

            sensors = SimulatedSensors() if args.simulate else PiSensors(
                config.hardware, config.calibration
            )
            try:
                network = NetworkProbe(config.network.interface)
                clock = SystemClock()
                transport = None
                if config.telemetry.enabled:
                    transport = HttpsTransport(
                        config.telemetry.api_url,
                        config.telemetry.bearer_token,
                        config.cycle.http_timeout_seconds,
                    )
                result = run_cycle(
                    config, storage, sensors, network, clock, transport=transport
                )
                print(_json(result.__dict__))
                return 0
            finally:
                sensors.close()
        finally:
            storage.close()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        LOG.error("cycle failed safely: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
