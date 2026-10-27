"""X5 autonomous drive speed: offered, ranged and sent the way the app's driving page does it."""

import pytest
from battery_support import (
    make_mower,
    make_report_coordinator,
    platform_entities,
    sent_commands,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_limits import DeviceLimits, RangeLimit
from pymammotion.messaging.command_queue import Priority
from user_command_support import make_coordinator, without_state_machine

from custom_components.mammotion import number as number_platform
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.number import (
    AUTONOMOUS_DRIVE_SPEED_NUMBER_ENTITY,
    MammotionAutonomousDriveSpeedNumberEntity,
)

X5 = "Luba-VA123456"
X5_FIRMWARE = "2.3.28"
# A model whose config runs working speed 0.2-1.2 m/s.
WIDE_RANGE_PRODUCT_KEY = "a1nf9kRBWoH"

_GATE_CASES = [
    pytest.param(X5, "2.3.28", True, id="x5-at-gate"),
    pytest.param(X5, "2.3.30.1", True, id="x5-above-gate"),
    pytest.param("Yuka-MV123456", "2.3.28", True, id="yuka-mv"),
    pytest.param(X5, "2.3.27.9", False, id="x5-below-gate"),
    pytest.param(X5, "", False, id="x5-unknown-firmware"),
    pytest.param("Luba-VS123456", "2.3.28", False, id="x3-luba-2"),
    pytest.param("Luba-123456", "2.3.28", False, id="luba-1"),
]


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_slider_exists_only_where_the_app_shows_it(
    name: str, firmware: str, expected: bool
) -> None:
    """CarDrivingActivity shows the slider on X5 only (Yuka MV is X5), from firmware 2.3.28."""
    entities = await platform_entities(number_platform, make_mower(name, firmware))
    assert ("autonomous_drive_speed" in entities) is expected


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_startup_read_is_gated_the_same_way(
    hass: HomeAssistant, name: str, firmware: str, expected: bool
) -> None:
    """The speed is only read from devices the app would show it for."""
    coordinator = await make_report_coordinator(hass, name, firmware)

    await coordinator._async_ensure_startup_reads()  # noqa: SLF001

    assert ("read_autonomous_drive_speed" in sent_commands(coordinator)) is expected


async def test_the_range_falls_back_to_the_app_default_without_model_limits() -> None:
    """With no model config the app's working-speed range is 0.2-0.6 m/s in 0.1 steps."""
    entity = (await platform_entities(number_platform, make_mower(X5, X5_FIRMWARE)))[
        "autonomous_drive_speed"
    ]
    assert (entity.native_min_value, entity.native_max_value) == (0.2, 0.6)
    assert entity.native_step == 0.1


async def test_the_range_follows_the_model_working_speed() -> None:
    """The app bounds the slider with getWorkingSpeedMin/Max, the model's working-speed config."""
    mower = make_mower(X5, X5_FIRMWARE)
    device = MowingDevice()
    device.mower_state.product_key = WIDE_RANGE_PRODUCT_KEY
    mower.api.get_device_by_name.return_value = device

    entity = (await platform_entities(number_platform, mower))["autonomous_drive_speed"]

    assert (entity.native_min_value, entity.native_max_value) == (0.2, 1.2)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        pytest.param(None, None, id="unread"),
        pytest.param(0.4, 0.4, id="in-range"),
        pytest.param(0.1, 0.2, id="below-range"),
        pytest.param(0.9, 0.6, id="above-range"),
    ],
)
async def test_the_reported_speed_is_clamped_into_the_range(
    reported: float | None, expected: float | None
) -> None:
    """The app clamps the device's value into the slider's range before showing it."""
    mower = make_mower(X5, X5_FIRMWARE)
    mower.reporting_coordinator.data.mower_state.autonomous_drive_speed = reported

    entity = (await platform_entities(number_platform, mower))["autonomous_drive_speed"]

    assert entity.native_value == (
        pytest.approx(expected) if expected is not None else None
    )


def _reported_coordinator(speed: float) -> MammotionReportUpdateCoordinator:
    device = MowingDevice()
    device.mower_state.autonomous_drive_speed = speed
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=X5
    )
    coordinator.unique_name = X5
    return coordinator


async def test_a_degenerate_model_range_is_widened_by_0_4() -> None:
    """CarDrivingActivity widens a range whose max is not above its min by 0.4 m/s."""
    limits = DeviceLimits(working_speed=RangeLimit(min=0.3, max=0.3))

    entity = MammotionAutonomousDriveSpeedNumberEntity(
        _reported_coordinator(0.3), AUTONOMOUS_DRIVE_SPEED_NUMBER_ENTITY, limits
    )

    assert entity.native_min_value == pytest.approx(0.3)
    assert entity.native_max_value == pytest.approx(0.7)


async def test_moving_the_slider_sends_the_speed(hass: HomeAssistant) -> None:
    """The entity's set action reaches the mower as a USER command on nav."""
    coordinator = _reported_coordinator(0.4)
    entity = without_state_machine(
        MammotionAutonomousDriveSpeedNumberEntity(
            coordinator, AUTONOMOUS_DRIVE_SPEED_NUMBER_ENTITY, None
        ),
        hass,
    )

    await entity.async_set_native_value(0.5)

    assert entity.native_value == 0.5
    coordinator.manager.send_command_and_wait.assert_awaited_once_with(
        X5,
        "set_autonomous_drive_speed",
        "nav_sys_param_cmd",
        prefer_ble=False,
        priority=Priority.USER,
        speed=0.5,
    )


async def test_a_speed_the_mower_never_got_is_reported_and_reverted(
    hass: HomeAssistant,
) -> None:
    """A rejected write raises a translated error and the slider returns to the reported speed."""
    coordinator = _reported_coordinator(0.4)
    coordinator.manager.send_command_and_wait.side_effect = DeviceOfflineException(
        29003, "iot-1"
    )
    entity = without_state_machine(
        MammotionAutonomousDriveSpeedNumberEntity(
            coordinator, AUTONOMOUS_DRIVE_SPEED_NUMBER_ENTITY, None
        ),
        hass,
    )

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_native_value(0.5)

    assert raised.value.translation_key == "command_failed"
    assert entity.native_value == pytest.approx(0.4)


async def test_setting_the_speed_sends_a_user_command_on_nav() -> None:
    """A slider move is a user action, answered by nav_sys_param_cmd."""
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=X5
    )

    await coordinator.async_set_autonomous_drive_speed(0.5)

    coordinator.manager.send_command_and_wait.assert_awaited_once_with(
        X5,
        "set_autonomous_drive_speed",
        "nav_sys_param_cmd",
        prefer_ble=False,
        priority=Priority.USER,
        speed=0.5,
    )
