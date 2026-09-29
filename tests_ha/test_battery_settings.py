"""Battery settings are written from what the device reported, never from defaults.

``bms_ctrl_info_msg`` carries every battery setting at once, so a write has to echo
the off-peak window it read.  The app always reads before it renders the page;
these pin that HA does too, that an unread setting shows as unknown rather than
"off", and that the reads happen once the firmware gate passes even when the
firmware arrives after setup.  The coordinator is the shipped class around a
spec'd client.
"""

from typing import Any

import pytest
from battery_support import make_report_coordinator
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_info import ChargeSettings
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.state.device_state import DeviceStateMachine
from user_command_support import make_coordinator

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_MOWER = "Luba-VS123456"
_FIRMWARE = "2.3.30.39"
#: The window the Luba 3 reported in every frame of the field log (22:00-06:00).
_WINDOW = {"valley_charge_start_time": 1320, "valley_charge_end_time": 360}


def _reported(**overrides: Any) -> ChargeSettings:
    return ChargeSettings(
        **{
            "smart_charge": True,
            "charge_limit": 100,
            "peak_valley_charge": True,
            **_WINDOW,
            **overrides,
        }
    )


def _sent(
    coordinator: MammotionReportUpdateCoordinator,
) -> list[tuple[str, dict[str, Any]]]:
    """Return (command, kwargs) for every command the coordinator handed the client."""
    return [
        (call.args[1], call.kwargs)
        for call in coordinator.manager.send_command_and_wait.await_args_list
    ]


def _write_coordinator(device: MowingDevice) -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )


def _device_answers_the_read_with(
    coordinator: MammotionReportUpdateCoordinator, settings: ChargeSettings
) -> None:
    """Make the query reply land in the library's state only.

    ``coordinator.data`` trails a reply by the push debounce, so the library's
    device is replaced and ``data`` left as it was.
    """

    async def _answer(
        device_name: str, command: str, *args: Any, **kwargs: Any
    ) -> None:
        if command == "query_battery_info":
            replied = MowingDevice()
            replied.mower_state.charge_settings = settings
            coordinator.manager.get_device_by_name.return_value = replied

    coordinator.manager.send_command_and_wait.side_effect = _answer


@pytest.mark.regression
async def test_a_write_before_the_first_read_keeps_the_devices_off_peak_window() -> (
    None
):
    """Unread settings were resent as defaults, resetting the window to 00:00-00:00."""
    coordinator = _write_coordinator(MowingDevice())
    _device_answers_the_read_with(coordinator, _reported())

    await coordinator.async_set_charge_limit(90)

    assert _sent(coordinator)[0][0] == "query_battery_info"
    command, kwargs = _sent(coordinator)[-1]
    assert command == "set_battery_info"
    assert kwargs["smart_charge"] is False
    assert kwargs["charge_limit"] == 90
    assert kwargs["peak_valley_charge"] is True
    assert {k: kwargs[k] for k in _WINDOW} == _WINDOW


@pytest.mark.regression
async def test_a_write_is_refused_when_the_settings_cannot_be_read() -> None:
    """Sending anyway would overwrite the device's real window with zeros."""
    coordinator = _write_coordinator(MowingDevice())

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_set_smart_charge(True)

    assert raised.value.translation_key == "battery_settings_unread"
    assert [command for command, _ in _sent(coordinator)] == ["query_battery_info"]


async def test_a_write_after_a_read_does_not_read_again() -> None:
    """Reading first is for the unread case only; a routine write is one send."""
    device = MowingDevice()
    device.mower_state.charge_settings = _reported(smart_charge=False, charge_limit=85)
    coordinator = _write_coordinator(device)

    await coordinator.async_set_smart_charge(True)

    assert [command for command, _ in _sent(coordinator)] == ["set_battery_info"]


async def _push_firmware(
    hass: HomeAssistant, coordinator: MammotionReportUpdateCoordinator, firmware: str
) -> None:
    device = MowingDevice()
    device.online = True
    device.device_firmwares.device_version = firmware
    coordinator.manager.get_device_by_name.return_value = device
    await coordinator._on_state_changed(DeviceStateMachine("dev-1", device).current)  # noqa: SLF001
    await hass.async_block_till_done()


_GATED = ("query_battery_info", "read_recharge_level", "read_resume_level")


@pytest.mark.regression
async def test_the_battery_settings_are_read_once_the_firmware_is_known(
    hass: HomeAssistant,
) -> None:
    """Firmware unknown at setup skipped the read, and nothing issued it later.

    The entities appeared once the firmware arrived, but showed defaults all
    session and every write hit the unread-settings path.
    """
    coordinator = await make_report_coordinator(hass, _MOWER, "")
    await coordinator._async_ensure_startup_reads()  # noqa: SLF001
    assert not {c for c, _ in _sent(coordinator)} & set(_GATED)

    await _push_firmware(hass, coordinator, _FIRMWARE)
    await _push_firmware(hass, coordinator, _FIRMWARE)

    sent = [c for c, _ in _sent(coordinator) if c in _GATED]
    assert sent == list(_GATED), f"expected each gated read exactly once, got {sent}"


async def test_reads_issued_at_setup_are_not_repeated_when_a_push_arrives(
    hass: HomeAssistant,
) -> None:
    """The late-gate path must not duplicate what setup already read."""
    coordinator = await make_report_coordinator(hass, _MOWER, _FIRMWARE)
    await coordinator._async_ensure_startup_reads()  # noqa: SLF001

    await _push_firmware(hass, coordinator, _FIRMWARE)

    sent = [c for c, _ in _sent(coordinator) if c in _GATED]
    assert sent == list(_GATED)


async def test_a_firmware_push_before_the_startup_reads_leaves_them_to_it(
    hass: HomeAssistant,
) -> None:
    """With updates off nothing was read yet; the startup reads pick the gate up."""
    coordinator = await make_report_coordinator(hass, _MOWER, "")

    await _push_firmware(hass, coordinator, _FIRMWARE)

    assert not {c for c, _ in _sent(coordinator)} & set(_GATED)


def _builds(command: str, kwargs: dict[str, Any]) -> bytes:
    """Build *command* the way the library will: a renamed method or kwarg fails here."""
    payload = {k: v for k, v in kwargs.items() if k not in ("prefer_ble", "priority")}
    return getattr(MammotionCommand(_MOWER, 1), command)(**payload)


async def test_the_charge_levels_are_read_one_after_the_other() -> None:
    """Both replies are ``nav_sys_param_cmd``, so the reads cannot overlap."""
    coordinator = _write_coordinator(MowingDevice())

    await coordinator.async_read_charge_levels()

    calls = coordinator.manager.send_command_and_wait.await_args_list
    assert [call.args[1:] for call in calls] == [
        ("read_recharge_level", "nav_sys_param_cmd"),
        ("read_resume_level", "nav_sys_param_cmd"),
    ]
    for command, kwargs in _sent(coordinator):
        _builds(command, kwargs)


@pytest.mark.parametrize(
    ("setter", "command"),
    [
        ("async_set_recharge_level", "set_recharge_level"),
        ("async_set_resume_level", "set_resume_level"),
    ],
)
async def test_setting_a_level_sends_only_that_level(setter: str, command: str) -> None:
    """Ids 14 and 15 are separate messages: no unread default rides along."""
    coordinator = _write_coordinator(MowingDevice())

    await getattr(coordinator, setter)(-1)

    assert [c for c, _ in _sent(coordinator)] == [command]
    sent_command, kwargs = _sent(coordinator)[0]
    assert kwargs["level"] == -1
    _builds(sent_command, kwargs)


async def test_the_battery_write_matches_the_library_signature() -> None:
    """``set_battery_info`` takes the off-peak window as required keywords."""
    device = MowingDevice()
    device.mower_state.charge_settings = _reported()
    coordinator = _write_coordinator(device)

    await coordinator.async_set_charge_limit(85)

    _builds(*_sent(coordinator)[0])
