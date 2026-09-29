"""The app's "Continue last job", as a button.

The app offers it only on mower screens, and its home start flow refuses unless
the mower is on standby (MODE_READY), has a map (``bolHash > 1``) and holds at
least 30 % charge; those three are local state, so they drive availability.
Which job to resume, and whether it may be resumed at all, the app reads from
the newest record of the cloud's work-report page (``workId``,
``continueWork``).  That is fetched on press, never polled, and a press with
nothing resumable is refused rather than sent.
"""

import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.report_info import LocationData
from pymammotion.messaging.command_queue import Priority
from pymammotion.utility.constant import WorkMode
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion.button import BUTTON_CONTINUE_LAST_JOB, BUTTON_SENSORS
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_KEY = "continue_last_job"


def _description():
    return next(entity for entity in BUTTON_CONTINUE_LAST_JOB if entity.key == _KEY)


def _availability_coordinator(
    *,
    sys_status: int = WorkMode.MODE_READY,
    bol_hash: int = 12345,
    battery: int = 80,
) -> MagicMock:
    coordinator = MagicMock()
    device = MowingDevice()
    device.report_data.dev.sys_status = sys_status
    device.report_data.dev.battery_val = battery
    device.report_data.locations = [LocationData(bol_hash=bol_hash)]
    coordinator.data = device
    return coordinator


def _press_coordinator(record: object | None) -> SimpleNamespace:
    """Bind the real press handler to a fake self whose cloud returns *record*."""
    coordinator = SimpleNamespace(
        device_name="Luba-VS10001",
        manager=SimpleNamespace(get_latest_work_report=AsyncMock(return_value=record)),
        send_command_and_update=AsyncMock(),
    )
    coordinator.async_continue_last_job = MethodType(
        MammotionBaseUpdateCoordinator.async_continue_last_job, coordinator
    )
    return coordinator


@pytest.mark.parametrize(
    "device_name",
    ["Luba-VS10001", "Luba-AAAA", "Luba-VA123456", "Yuka-MNTXVHBE"],
)
def test_mowers_are_offered_it(device_name: str) -> None:
    """The gate the entity is created behind."""
    assert DeviceType.supports_continue_last_job(device_name)


@pytest.mark.parametrize("device_name", ["RTK12345", "Spino-SP123456"])
def test_rtk_and_pool_devices_never_get_it(device_name: str) -> None:
    """The app shows it only on mower screens."""
    assert not DeviceType.supports_continue_last_job(device_name)


def test_it_is_not_in_the_unconditional_button_set() -> None:
    """Were it there, the gate would be bypassed."""
    assert _KEY not in {entity.key for entity in BUTTON_SENSORS}


def test_it_is_available_when_standby_mapped_and_charged() -> None:
    """All three of the app's local preconditions hold."""
    assert _description().available_fn(_availability_coordinator()) is True


@pytest.mark.parametrize(
    "mode",
    [
        WorkMode.MODE_WORKING,
        WorkMode.MODE_PAUSE,
        WorkMode.MODE_RETURNING,
        WorkMode.MODE_CHARGING,
        WorkMode.MODE_CORRIDOR_DRAW,
    ],
    ids=["working", "paused", "returning", "charging", "corridor-draw"],
)
def test_it_is_unavailable_off_standby(mode: int) -> None:
    """The app refuses with "must be on standby" outside MODE_READY."""
    assert (
        _description().available_fn(_availability_coordinator(sys_status=mode)) is False
    )


@pytest.mark.parametrize("bol_hash", [0, 1], ids=["no-map", "placeholder-hash"])
def test_it_is_unavailable_without_a_map(bol_hash: int) -> None:
    """The app's home screen treats ``bolHash <= 1`` as no map."""
    coordinator = _availability_coordinator(bol_hash=bol_hash)
    assert _description().available_fn(coordinator) is False


def test_it_is_unavailable_before_any_location_is_reported() -> None:
    """No location frame means no ``bol_hash``, so a map is unproven."""
    coordinator = _availability_coordinator()
    coordinator.data.report_data.locations = []
    assert _description().available_fn(coordinator) is False


@pytest.mark.parametrize(
    ("battery", "available"), [(29, False), (30, True)], ids=["29%", "30%"]
)
def test_it_needs_thirty_percent_battery(battery: int, available: bool) -> None:
    """The app's threshold is inclusive: ``< 30`` is refused."""
    coordinator = _availability_coordinator(battery=battery)
    assert _description().available_fn(coordinator) is available


def test_it_is_unavailable_before_any_state_arrives() -> None:
    """Nothing is known yet, so standby is unproven."""
    coordinator = _availability_coordinator()
    coordinator.data = None
    assert _description().available_fn(coordinator) is False


async def test_pressing_it_resumes_the_cloud_s_latest_job() -> None:
    """The work_id is the report's, sent as a user command so it is not queued."""
    coordinator = _press_coordinator(
        SimpleNamespace(work_id="987654321", can_resume=True)
    )

    await coordinator.async_continue_last_job()

    coordinator.manager.get_latest_work_report.assert_awaited_once_with("Luba-VS10001")
    coordinator.send_command_and_update.assert_awaited_once_with(
        "continue_last_job", priority=Priority.USER, work_id=987654321
    )


async def test_an_empty_work_id_is_sent_as_zero() -> None:
    """The app's RN caller falls back to 0 when the record carries no workId."""
    coordinator = _press_coordinator(SimpleNamespace(work_id="", can_resume=True))

    await coordinator.async_continue_last_job()

    assert coordinator.send_command_and_update.await_args.kwargs["work_id"] == 0


@pytest.mark.parametrize(
    "record",
    [None, SimpleNamespace(work_id="1", can_resume=False)],
    ids=["no-record", "not-resumable"],
)
async def test_nothing_resumable_is_refused_not_sent(record: object | None) -> None:
    """The app hides the action here; sending would ask the mower to resume nothing."""
    coordinator = _press_coordinator(record)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_continue_last_job()

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "no_job_to_continue"
    coordinator.send_command_and_update.assert_not_awaited()


async def test_the_button_press_reaches_the_coordinator() -> None:
    """The entity only delegates; the refusal logic lives on the coordinator."""
    coordinator = MagicMock()
    coordinator.async_continue_last_job = AsyncMock()

    await _description().press_fn(coordinator)

    coordinator.async_continue_last_job.assert_awaited_once()


def _locale_files() -> list[Path]:
    return [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]


def test_every_locale_names_the_button_and_the_refusal() -> None:
    """A key missing from a locale falls back to English mid-interface."""
    for path in _locale_files():
        data = json.loads(path.read_text())
        assert data["entity"]["button"][_KEY]["name"].strip(), path.name
        assert data["exceptions"]["no_job_to_continue"]["message"].strip(), path.name

    icons = json.loads((_ROOT / "icons.json").read_text())
    assert icons["entity"]["button"][_KEY]["default"]


def test_no_locale_copied_the_english_wording() -> None:
    """A locale that just copied the English string was never translated."""
    english = json.loads((_ROOT / "strings.json").read_text())
    name = english["entity"]["button"][_KEY]["name"]
    message = english["exceptions"]["no_job_to_continue"]["message"]

    copied = [
        path.stem
        for path in sorted((_ROOT / "translations").glob("*.json"))
        if path.stem != "en"
        and (
            json.loads(path.read_text())["entity"]["button"][_KEY]["name"] == name
            or json.loads(path.read_text())["exceptions"]["no_job_to_continue"][
                "message"
            ]
            == message
        )
    ]

    assert copied == []
