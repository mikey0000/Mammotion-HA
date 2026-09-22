"""Changing settings on a job the mower is already running.

The app's in-job editor seeds from the active route and re-sends the whole
parameter set with only the edited fields changed.  Two things make that
delicate here:

* ``async_modify_plan_route`` forces the job's identity and geometry back from
  the device, so those fields cannot be changed this way and must not be
  offered as though they could.
* the reserved buffer is rebuilt from ``operation_settings`` on every send, and
  the device echoes it back with every byte raised by ten — the quirk behind
  Mammotion-HA #891.  Seeding it without undoing the echo re-creates that bug
  on the running job, with ``start_progress`` climbing ten each time.
"""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings, create_path_order

from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    _reserved_without_echo,
)

_LUBA_PRO = "Luba-VS10001"
_LUBA1 = "Luba-AAAA"

#: A real running job's reserved, as the device echoes it: every byte +10.
#: Decodes to border 0, obstacle_laps 1, rain 0, progress 0, toward_mode 0,
#: device_tactics 8, collect_freq 10 — the last two being exactly the
#: constants create_path_order writes, which is what confirms the offset.
_ECHOED_RESERVED = "\n\x0b\n\n\n\x12\x14("

_METHODS = (
    "_is_route_job_running",
    "_seed_operation_settings_from_running_job",
    "async_modify_running_job",
    "_apply_route_field_if_working",
    "async_change_progress_if_working",
    "async_change_blade_height_if_working",
)


def _coordinator(
    *,
    device_name: str = _LUBA_PRO,
    running: bool = True,
    reserved: str = _ECHOED_RESERVED,
) -> SimpleNamespace:
    """Bind the real coordinator methods to a fake self over a real device."""
    device = MowingDevice()
    device.work.zone_hashs = [123] if running else []
    device.work.speed = 0.4
    device.work.channel_width = 30
    device.work.ultra_wave = 2
    device.work.channel_mode = 1
    device.work.knife_height = 60
    device.work.auto_change_direction = 1
    device.work.job_id = 99
    device.work.reserved = reserved
    device.report_data.work.bp_hash = "123"
    device.report_data.work.area = 5

    settings = OperationSettings()
    # Stale planning values, deliberately different from the running job.
    settings.start_progress = 55
    settings.obstacle_laps = 4
    settings.border_mode = 3

    coordinator = SimpleNamespace(
        device_name=device_name,
        data=device,
        operation_settings=settings,
        _operation_settings=settings,
        async_modify_plan_route=AsyncMock(),
        async_blade_height=AsyncMock(),
    )
    for name in _METHODS:
        setattr(
            coordinator,
            name,
            MethodType(getattr(MammotionBaseUpdateCoordinator, name), coordinator),
        )
    return coordinator


def test_the_echo_is_undone() -> None:
    """The decisive sample: +10 on bytes 0-6, and 8/10 fall out as the defaults.

    Byte 7 is left alone: create_path_order never writes it and its meaning is
    unknown, so subtracting from it would be invention rather than correction.
    """
    raw = _reserved_without_echo(_ECHOED_RESERVED).encode("latin-1")

    assert list(raw[:7]) == [0, 1, 0, 0, 0, 8, 10]
    assert raw[7] == 40, "byte 7 is passed through untouched"


def test_an_untouched_reserved_byte_survives_a_round_trip() -> None:
    """Seed, re-encode, and progress must come back where it started — not +10.

    This is the #891 failure mode: without undoing the echo, every mid-job
    change would push the running job's progress ten further along.
    """
    coordinator = _coordinator()

    coordinator._seed_operation_settings_from_running_job()
    resent = create_path_order(coordinator._operation_settings, _LUBA_PRO)

    assert resent.encode("latin-1")[3] == 0, "progress must not climb by ten"
    assert coordinator._operation_settings.start_progress == 0


def test_the_running_job_wins_over_the_planning_values() -> None:
    """The slider holds 55 and the mower is at 0; the mower is right."""
    coordinator = _coordinator()

    coordinator._seed_operation_settings_from_running_job()

    assert coordinator._operation_settings.start_progress == 0
    assert coordinator._operation_settings.obstacle_laps == 1


async def test_it_changes_several_settings_at_once() -> None:
    """The point of the service: one re-issue, not one per field."""
    coordinator = _coordinator()

    assert await coordinator.async_modify_running_job(blade_height=45, speed=0.8)

    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert (sent.blade_height, sent.speed) == (45, 0.8)
    coordinator.async_modify_plan_route.assert_awaited_once()


async def test_unspecified_settings_keep_the_running_job_s_values() -> None:
    """Changing one thing must not reset the rest to planning defaults."""
    coordinator = _coordinator()

    await coordinator.async_modify_running_job(blade_height=45)

    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.speed == 0.4
    assert sent.channel_width == 30
    assert sent.ultra_wave == 2
    assert sent.job_id == 99


async def test_none_is_not_treated_as_a_change() -> None:
    """The service passes only what the caller supplied; absent stays absent."""
    coordinator = _coordinator()

    await coordinator.async_modify_running_job(blade_height=None, speed=0.8)

    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.blade_height == 60, "the running job's height, not None"
    assert sent.speed == 0.8


async def test_progress_can_be_moved() -> None:
    """What this was all for."""
    coordinator = _coordinator()

    await coordinator.async_modify_running_job(start_progress=25)

    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.start_progress == 25
    assert create_path_order(sent, _LUBA_PRO).encode("latin-1")[3] == 25


async def test_the_progress_entity_routes_through_it() -> None:
    """The number entity writes operation_settings, then this sends it."""
    coordinator = _coordinator()
    coordinator._operation_settings.start_progress = 30

    await coordinator.async_change_progress_if_working()

    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.start_progress == 30


async def test_nothing_is_sent_when_no_job_is_running() -> None:
    """An idle mower has no route to re-issue."""
    coordinator = _coordinator(running=False)

    assert await coordinator.async_modify_running_job(speed=0.8) is False
    coordinator.async_modify_plan_route.assert_not_awaited()


async def test_nothing_is_sent_on_a_luba_1() -> None:
    """Its in-job editor only offers blade height, sent as a direct command."""
    coordinator = _coordinator(device_name=_LUBA1)

    assert await coordinator.async_modify_running_job(speed=0.8) is False
    coordinator.async_modify_plan_route.assert_not_awaited()


async def test_a_job_with_no_reserved_yet_still_works() -> None:
    """Before the first report the buffer is empty; that must not raise."""
    coordinator = _coordinator(reserved="")

    assert await coordinator.async_modify_running_job(speed=0.8)


@pytest.mark.parametrize(
    "field", ["speed", "channel_width", "ultra_wave", "channel_mode", "blade_height"]
)
async def test_each_offered_field_actually_reaches_the_route(field: str) -> None:
    """A field the service offers but modify_plan_route reseeds would be a lie."""
    coordinator = _coordinator()
    before = getattr(coordinator._operation_settings, field)

    await coordinator.async_modify_running_job(**{field: before})

    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert getattr(sent, field) == before
