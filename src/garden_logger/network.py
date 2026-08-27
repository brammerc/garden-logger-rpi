"""Bounded connectivity checks and Wi-Fi signal reporting."""

from __future__ import annotations

from pathlib import Path
import time


class NetworkProbe:
    def __init__(self, interface: str):
        self.interface = interface

    def _interface_up(self) -> bool:
        try:
            state = Path(f"/sys/class/net/{self.interface}/operstate").read_text(
                encoding="ascii"
            )
            return state.strip() in {"up", "unknown"}
        except OSError:
            return False

    def wait_online(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            # Association/carrier is the Raspberry Pi equivalent of the ESP8266
            # station-connection check. DNS and API reachability belong to the
            # independently bounded HTTPS transport step.
            if self._interface_up():
                return True
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    def wifi_rssi(self) -> int | None:
        try:
            lines = Path("/proc/net/wireless").read_text(encoding="ascii").splitlines()[2:]
        except OSError:
            return None
        for line in lines:
            name, separator, fields = line.partition(":")
            if separator and name.strip() == self.interface:
                columns = fields.split()
                try:
                    # /proc/net/wireless reports level in the third numeric column.
                    return max(-127, min(0, int(float(columns[2].rstrip(".")))))
                except (IndexError, ValueError):
                    return None
        return None
