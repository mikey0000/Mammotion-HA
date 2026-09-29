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

import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings, create_path_order

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_LUBA_PRO = "Luba-VS10001"
_LUBA1 = "Luba-AAAA"

#: A real running job's reserved, as the device echoes it: every byte +10.
#: Decodes to border 0, obstacle_laps 1, plan enabled 0, progress 0, toward_mode 0,
#: device_tactics 8, collect_freq 10 — the last two being exactly the
#: constants create_path_order writes, which is what confirms the offset.
_ECHOED_RESERVED = "\n\x0b\n\n\n\x12\x14("

_METHODS = (
    "_is_route_job_running",
    "_seed_operation_settings_from_running_job",
    "_running_job_refusal",
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
    device.work.auto_change_direction = True
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


def _as_echoed(sent: str) -> str:
    """Return what the device reports back for a buffer it was sent: every byte +10."""
    return bytes(b + 10 for b in sent.encode("latin-1")).decode("latin-1")


def test_the_resent_route_carries_the_running_job_s_buffer() -> None:
    """The decisive sample: with the echo undone once, 8/10 fall out as the defaults.

    Undoing it twice would zero obstacle_laps and the Luba Pro's 8/10 constants
    would no longer line up with what create_path_order writes.
    """
    coordinator = _coordinator()

    coordinator._seed_operation_settings_from_running_job()
    resent = create_path_order(coordinator._operation_settings, _LUBA_PRO)

    assert list(resent.encode("latin-1")) == [0, 1, 0, 0, 0, 8, 10, 0]


def test_a_mid_job_progress_is_seeded_where_the_mower_is() -> None:
    """Progress 25 echoes as 35; seeding must read 25, neither 35 nor 15."""
    coordinator = _coordinator(reserved=_as_echoed("\x00\x01\x00\x19\x00\x08\x0a\x00"))

    coordinator._seed_operation_settings_from_running_job()

    assert coordinator._operation_settings.start_progress == 25


def test_repeated_mid_job_changes_do_not_compound() -> None:
    """Seed, send, receive the echo, seed again: the buffer must not drift."""
    coordinator = _coordinator(reserved=_as_echoed("\x00\x01\x00\x19\x00\x08\x0a\x00"))
    coordinator._seed_operation_settings_from_running_job()
    first = create_path_order(coordinator._operation_settings, _LUBA_PRO)

    coordinator.data.work.reserved = _as_echoed(first)
    coordinator._seed_operation_settings_from_running_job()
    second = create_path_order(coordinator._operation_settings, _LUBA_PRO)

    assert second == first
    assert second.encode("latin-1")[3] == 25


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


@pytest.mark.parametrize(
    ("reported", "stored", "expected"),
    [(True, 0, 1), (False, 1, 0), (None, 1, 1)],
    ids=["on", "off", "not-reported-keeps-the-setting"],
)
def test_the_running_jobs_auto_change_direction_is_seeded_from_field_21(
    reported: bool | None, stored: int, expected: int
) -> None:
    """Field 20 was read as the setting, but it always echoes 1; field 21 carries it."""
    coordinator = _coordinator()
    coordinator.data.work.task_settings_mode = 1
    coordinator.data.work.auto_change_direction = reported
    coordinator._operation_settings.auto_change_direction = stored

    coordinator._seed_operation_settings_from_running_job()

    assert coordinator._operation_settings.auto_change_direction == expected


async def test_it_changes_several_settings_at_once() -> None:
    """The point of the service: one re-issue, not one per field."""
    coordinator = _coordinator()

    await coordinator.async_modify_running_job(blade_height=45, speed=0.8)

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


async def test_an_idle_mower_is_refused_rather_than_ignored() -> None:
    """An idle mower has no route to re-issue; the caller must be told so."""
    coordinator = _coordinator(running=False)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_modify_running_job(speed=0.8)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "no_running_job"
    assert err.value.translation_placeholders == {"device_name": _LUBA_PRO}
    coordinator.async_modify_plan_route.assert_not_awaited()


@pytest.mark.parametrize("running", [True, False], ids=["running", "idle"])
async def test_a_luba_1_is_refused_rather_than_ignored(running: bool) -> None:
    """Its in-job editor only offers blade height, sent as a direct command."""
    coordinator = _coordinator(device_name=_LUBA1, running=running)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_modify_running_job(speed=0.8)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "running_job_modify_unsupported"
    assert err.value.translation_placeholders == {"device_name": _LUBA1}
    coordinator.async_modify_plan_route.assert_not_awaited()
    coordinator.async_blade_height.assert_not_awaited()


@pytest.mark.parametrize(
    ("device_name", "running"),
    [(_LUBA_PRO, False), (_LUBA1, True), (_LUBA1, False)],
    ids=["luba-pro-idle", "luba-1-running", "luba-1-idle"],
)
async def test_the_entity_helpers_still_no_op_when_it_would_refuse(
    device_name: str, running: bool
) -> None:
    """A slider moved while idle only stores the setting; it must not raise."""
    coordinator = _coordinator(device_name=device_name, running=running)

    await coordinator.async_change_progress_if_working()

    coordinator.async_modify_plan_route.assert_not_awaited()


@pytest.mark.parametrize("key", ["no_running_job", "running_job_modify_unsupported"])
def test_every_locale_has_the_refusal_messages(key: str) -> None:
    """A translation_key with no message renders as the bare key."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    english = json.loads((_ROOT / "strings.json").read_text())["exceptions"][key]
    assert "{device_name}" in english["message"]
    for path in files:
        message = json.loads(path.read_text())["exceptions"][key]["message"]
        assert "{device_name}" in message, path
        if path.stem not in ("strings", "en"):
            assert message != english["message"], path


async def test_a_job_with_no_reserved_yet_still_works() -> None:
    """Before the first report the buffer is empty; that must not raise."""
    coordinator = _coordinator(reserved="")

    await coordinator.async_modify_running_job(speed=0.8)

    coordinator.async_modify_plan_route.assert_awaited_once()


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
