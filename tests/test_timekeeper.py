from garden_logger.timekeeper import SystemClock, TimeAnchor


def test_synchronized_utc_is_preferred():
    clock = SystemClock(
        wall_time=lambda: 1_800_000_000.0,
        monotonic=lambda: 500.0,
        boot_id=lambda: "boot-a",
    )
    choice = clock.choose(True, TimeAnchor("boot-a", 1_700_000_000, 100), 60)
    assert choice.epoch == 1_800_000_000.0
    assert choice.estimated is False


def test_same_boot_estimate_advances_by_at_least_interval():
    clock = SystemClock(monotonic=lambda: 110.0, boot_id=lambda: "boot-a")
    choice = clock.choose(False, TimeAnchor("boot-a", 1_800_000_000, 100), 60)
    assert choice.epoch == 1_800_000_060
    assert choice.estimated is True


def test_same_boot_estimate_uses_longer_monotonic_elapsed_time():
    clock = SystemClock(monotonic=lambda: 250.0, boot_id=lambda: "boot-a")
    choice = clock.choose(False, TimeAnchor("boot-a", 1_800_000_000, 100), 60)
    assert choice.epoch == 1_800_000_150


def test_estimate_is_rejected_after_reboot():
    clock = SystemClock(monotonic=lambda: 10.0, boot_id=lambda: "boot-b")
    choice = clock.choose(False, TimeAnchor("boot-a", 1_800_000_000, 100), 60)
    assert choice.epoch is None
    assert choice.estimated is False
