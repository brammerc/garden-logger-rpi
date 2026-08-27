"""Trustworthy system-clock selection with a retained same-boot estimate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Callable


MIN_VALID_EPOCH = 1_704_067_200  # 2024-01-01T00:00:00Z


@dataclass(frozen=True)
class TimeAnchor:
    boot_id: str
    observed_epoch: float
    monotonic_seconds: float


@dataclass(frozen=True)
class TimeChoice:
    epoch: float | None
    estimated: bool
    boot_id: str
    monotonic_seconds: float


def linux_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return "unknown-boot"


class SystemClock:
    def __init__(
        self,
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        boot_id: Callable[[], str] = linux_boot_id,
    ):
        self.wall_time = wall_time
        self.monotonic = monotonic
        self.boot_id = boot_id

    @staticmethod
    def _ntp_synchronized(timeout_seconds: float) -> bool:
        try:
            result = subprocess.run(
                ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                capture_output=True,
                text=True,
                timeout=max(0.1, timeout_seconds),
                check=False,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "yes"
        except (OSError, subprocess.TimeoutExpired):
            return False

    def wait_for_synchronization(self, timeout_seconds: float) -> bool:
        deadline = self.monotonic() + timeout_seconds
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return False
            if self._ntp_synchronized(min(remaining, 2.0)):
                return self.wall_time() >= MIN_VALID_EPOCH
            time.sleep(min(0.25, max(0.0, remaining)))

    def choose(
        self,
        synchronized: bool,
        anchor: TimeAnchor | None,
        interval_seconds: int,
    ) -> TimeChoice:
        now_mono = self.monotonic()
        current_boot = self.boot_id()
        if synchronized:
            now = self.wall_time()
            if now >= MIN_VALID_EPOCH:
                return TimeChoice(now, False, current_boot, now_mono)
        if (
            anchor is not None
            and anchor.boot_id == current_boot
            and anchor.observed_epoch >= MIN_VALID_EPOCH
            and now_mono >= anchor.monotonic_seconds
        ):
            elapsed = now_mono - anchor.monotonic_seconds
            estimate = anchor.observed_epoch + max(float(interval_seconds), elapsed)
            return TimeChoice(estimate, True, current_boot, now_mono)
        return TimeChoice(None, False, current_boot, now_mono)
