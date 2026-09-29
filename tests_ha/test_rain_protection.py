"""Rain protection: a mode select (Off / Smart / Sensor) and a Sensor-mode delay select.

Offered only on X5 mowers whose device proved the feature (a RAINPRO reply or
self-check 34, ``RainProtectionSettings.supported``): the user's Luba 2 has no such
setting, while his Luba 3 (``Luba-VAME9R5S``) reported self-check 34.  The state is
known only from a query reply or our own write, so an unread setting shows as
unknown rather than as the app's defaults.  The platform runs over a real
``MowingDevice`` with spec'd coordinators; the coordinator tests use the shipped
class around a spec'd client.
"""

import copy
import logging

import pytest
from battery_support import make_mower, make_report_coordinator, platform_entities
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_info import RainProtectionSettings
from pymammotion.data.model.mowing_modes import RainProtectionMode
from pymammotion.http.model.rain_protection import WeatherServerSync
from pymammotion.state.device_state import DeviceStateMachine
from pymammotion.transport.base import CommandRejectedError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from user_command_support import make_coordinator

from custom_components.mammotion import select as select_platform
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.models import MammotionDevices, MammotionMowerData

_LUBA_3 = "Luba-VAME9R5S"
_LUBA_3_FIRMWARE = "2.3.30.39"
_KEYS = {"rain_protection_mode", "rain_protection_delay"}


def _mower(name: str = _LUBA_3, **settings: object) -> MammotionMowerData:
    mower = make_mower(name, _LUBA_3_FIRMWARE)
    mower.reporting_coordinator.data.mower_state.rain_protection = (
        RainProtectionSettings(**settings)
    )
    return mower


async def _rain_entities(mower: MammotionMowerData) -> dict:
    entities = await platform_entities(select_platform, mower)
    return {key: entities[key] for key in _KEYS if key in entities}


@pytest.mark.parametrize(
    ("name", "supported", "expected"),
    [
        (_LUBA_3, True, True),
        (_LUBA_3, None, False),
        (_LUBA_3, False, False),
        # The user's Luba 2: no smart rain settings in the app.
        ("Luba-VS563L6H", True, False),
        ("Luba-QXABCDEF", True, False),
        ("Yuka-116ABCD", True, False),
        ("Yuka-MN6ABCDE", True, False),
    ],
    ids=[
        "luba3",
        "luba3-unprobed",
        "luba3-refused",
        "luba2",
        "luba1",
        "yuka",
        "yuka-mini",
    ],
)
async def test_the_selects_exist_only_on_x5_mowers_that_proved_support(
    name: str, supported: bool | None, expected: bool
) -> None:
    """The Luba 3 gets them once proved; the Luba 2, Luba 1 and Yukas never do."""
    entities = await _rain_entities(_mower(name, supported=supported))

    assert set(entities) == (_KEYS if expected else set())


async def test_the_selects_appear_once_the_device_proves_support(
    hass: HomeAssistant,
) -> None:
    """Support is only known at runtime, after the platform set up."""
    mower = _mower(supported=None)
    listeners = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), config_entry=None, name=_LUBA_3
    )
    coordinator = mower.reporting_coordinator
    coordinator.async_add_listener = listeners.async_add_listener
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = MammotionDevices(mowers=[mower], RTK=[], spino=[])
    added: list[str] = []
    await select_platform.async_setup_entry(
        hass, entry, lambda new: added.extend(e.entity_description.key for e in new)
    )
    assert not set(added) & _KEYS

    for _ in range(2):
        device = copy.deepcopy(coordinator.data)
        device.mower_state.rain_protection = RainProtectionSettings(supported=True)
        coordinator.data = device
        listeners.async_set_updated_data(device)

    assert sorted(k for k in added if k in _KEYS) == sorted(_KEYS), added


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (None, None),
        (RainProtectionMode.off, "off"),
        (RainProtectionMode.smart, "smart"),
        (RainProtectionMode.sensor, "sensor"),
        (7, None),
    ],
    ids=["unread", "off", "smart", "sensor", "unknown-firmware-value"],
)
async def test_the_mode_shows_what_the_device_reported(
    mode: int | None, expected: str | None
) -> None:
    """An unread mode, or one this version does not know, shows as unknown."""
    entity = (await _rain_entities(_mower(supported=True, mode=mode)))[
        "rain_protection_mode"
    ]

    assert entity.options == ["off", "smart", "sensor"]
    assert entity.current_option == expected


async def test_the_delay_is_offered_only_in_sensor_mode() -> None:
    """The options are the app's delay list, in hours."""
    entity = (
        await _rain_entities(
            _mower(supported=True, mode=RainProtectionMode.sensor, delay_hours=6)
        )
    )["rain_protection_delay"]

    assert entity.available is True
    assert entity.current_option == "6"
    assert entity.options == [str(h) for h in (*range(13), 24, 48)]


@pytest.mark.parametrize(
    "mode",
    [None, RainProtectionMode.off, RainProtectionMode.smart],
    ids=["unread", "off", "smart"],
)
async def test_the_delay_is_unavailable_outside_sensor_mode(mode: int | None) -> None:
    """The app hides the row; the remembered delay is not the device's current one."""
    entity = (await _rain_entities(_mower(supported=True, mode=mode, delay_hours=6)))[
        "rain_protection_delay"
    ]

    assert entity.available is False
    assert entity.current_option is None


@pytest.mark.parametrize("option", ["off", "smart", "sensor"])
async def test_picking_a_mode_sets_it(option: str) -> None:
    """The option maps to the library mode it names."""
    entity = (await _rain_entities(_mower(supported=True, mode=0)))[
        "rain_protection_mode"
    ]

    await entity.async_select_option(option)

    entity.coordinator.async_set_rain_protection_mode.assert_awaited_once_with(
        RainProtectionMode[option]
    )


async def test_picking_a_delay_sets_sensor_mode_with_it() -> None:
    """The app's delay picker implies Sensor mode."""
    entity = (
        await _rain_entities(_mower(supported=True, mode=RainProtectionMode.sensor))
    )["rain_protection_delay"]

    await entity.async_select_option("48")

    entity.coordinator.async_set_rain_protection_delay.assert_awaited_once_with(48)


def _write_coordinator(
    device: MowingDevice | None = None,
) -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator, device or MowingDevice(), device_name=_LUBA_3
    )


async def test_a_mode_write_leaves_the_delay_to_the_library() -> None:
    """``None`` makes pymammotion resend the remembered Sensor delay (24 until one is read)."""
    coordinator = _write_coordinator()
    coordinator.manager.set_rain_protection.return_value = WeatherServerSync.SAVED

    await coordinator.async_set_rain_protection_mode(RainProtectionMode.sensor)

    coordinator.manager.set_rain_protection.assert_awaited_once_with(
        _LUBA_3, RainProtectionMode.sensor, None
    )


async def test_a_delay_write_is_a_sensor_mode_write() -> None:
    """The delay only exists in Sensor mode, so writing it selects that mode."""
    coordinator = _write_coordinator()
    coordinator.manager.set_rain_protection.return_value = WeatherServerSync.SAVED

    await coordinator.async_set_rain_protection_delay(12)

    coordinator.manager.set_rain_protection.assert_awaited_once_with(
        _LUBA_3, RainProtectionMode.sensor, 12
    )


async def test_a_refused_write_is_reported_to_the_user() -> None:
    """A device refusal must not look like a successful selection."""
    coordinator = _write_coordinator()
    coordinator.manager.set_rain_protection.side_effect = CommandRejectedError(
        "refused"
    )

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_set_rain_protection_mode(RainProtectionMode.smart)

    assert raised.value.translation_key == "command_failed"


async def test_a_weather_server_failure_is_a_warning_not_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The mower already changed; failing the action would read as nothing happened."""
    coordinator = _write_coordinator()
    coordinator.manager.set_rain_protection.return_value = WeatherServerSync.FAILED

    with caplog.at_level(logging.WARNING, logger="custom_components.mammotion"):
        await coordinator.async_set_rain_protection_mode(RainProtectionMode.smart)

    assert any("weather server" in r.getMessage() for r in caplog.records)


async def test_the_read_goes_through_the_library() -> None:
    """The library serializes batch-config exchanges, so HA never sends the query itself."""
    coordinator = _write_coordinator()

    await coordinator.async_read_rain_protection()

    coordinator.manager.read_rain_protection.assert_awaited_once_with(_LUBA_3)


def _read_count(coordinator: MammotionReportUpdateCoordinator) -> int:
    return coordinator.manager.read_rain_protection.await_count


@pytest.mark.parametrize(
    ("name", "expected"),
    [(_LUBA_3, 1), ("Luba-VS563L6H", 0), ("Yuka-116ABCD", 0)],
    ids=["luba3", "luba2", "yuka"],
)
async def test_the_startup_reads_probe_only_x5_mowers(
    hass: HomeAssistant, name: str, expected: int
) -> None:
    """The probe is what proves support, so it cannot wait for ``supported``."""
    coordinator = await make_report_coordinator(hass, name, _LUBA_3_FIRMWARE)

    await coordinator._async_ensure_startup_reads()  # noqa: SLF001

    assert _read_count(coordinator) == expected


async def _push(
    hass: HomeAssistant,
    coordinator: MammotionReportUpdateCoordinator,
    settings: RainProtectionSettings,
) -> None:
    device = MowingDevice()
    device.online = True
    device.device_firmwares.device_version = _LUBA_3_FIRMWARE
    device.mower_state.rain_protection = settings
    coordinator.manager.get_device_by_name.return_value = device
    await coordinator._on_state_changed(DeviceStateMachine("dev-1", device).current)  # noqa: SLF001
    await hass.async_block_till_done()


async def test_self_check_34_after_an_unanswered_probe_reads_the_settings_once(
    hass: HomeAssistant,
) -> None:
    """Support proved without values (self-check 34) must not leave the selects unknown all session."""
    coordinator = await make_report_coordinator(hass, _LUBA_3, _LUBA_3_FIRMWARE)
    await coordinator._async_ensure_startup_reads()  # noqa: SLF001
    assert _read_count(coordinator) == 1

    await _push(hass, coordinator, RainProtectionSettings(supported=True))
    await _push(hass, coordinator, RainProtectionSettings(supported=True))

    assert _read_count(coordinator) == 2


async def test_an_answered_probe_is_not_read_again(hass: HomeAssistant) -> None:
    """A probe that returned the values leaves nothing for the late-gate read to do."""
    coordinator = await make_report_coordinator(hass, _LUBA_3, _LUBA_3_FIRMWARE)
    await coordinator._async_ensure_startup_reads()  # noqa: SLF001

    await _push(
        hass,
        coordinator,
        RainProtectionSettings(supported=True, mode=RainProtectionMode.smart),
    )

    assert _read_count(coordinator) == 1
