"""Battery charge limit is offered only where the app offers it (issue #857).

The stubbed suite could only confirm the gate expressions appeared in the
source.  These run the real platform setups over a real ``MowingDevice`` and
read back the entities they actually create, so a gate that is wired to the
wrong firmware string fails here rather than passing on a token match.
"""

import json
from pathlib import Path

import pytest
from battery_support import (
    make_mower,
    make_report_coordinator,
    platform_entities,
    sent_commands,
)
from homeassistant.components.number import NumberMode
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice
from pymammotion.messaging.command_queue import Priority
from user_command_support import make_coordinator

from custom_components.mammotion import number as number_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_GATE_CASES = [
    ("Luba-VS123456", "2.1.1.5", True),
    ("Luba-VS123456", "2.3.28.1", True),
    ("Yuka-123456", "2.1.1.5", True),
    ("Luba-123456", "2.1.1.5", True),
    ("Luba-VS123456", "2.1.1.4", False),
    ("Luba-VS123456", "", False),
]


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_slider_exists_only_where_the_app_shows_the_page(
    name: str, firmware: str, expected: bool
) -> None:
    """The app hides Battery management below 2.1.1.5, and on an unread version."""
    entities = await platform_entities(number_platform, make_mower(name, firmware))
    assert ("charge_limit" in entities) is expected


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_smart_charging_switch_follows_the_same_gate(
    name: str, firmware: str, expected: bool
) -> None:
    """Both controls belong to the one page, so one gate decides both."""
    entities = await platform_entities(switch_platform, make_mower(name, firmware))
    assert ("smart_charge" in entities) is expected


async def test_the_gate_reads_the_firmware_the_device_reported() -> None:
    """``mower.device`` is the Aliyun binding record and carries no firmware."""
    mower = make_mower("Luba-VS123456", "")
    assert not hasattr(mower.device, "device_firmwares")
    assert "charge_limit" not in await platform_entities(number_platform, mower)


async def test_the_slider_matches_the_app() -> None:
    """The app's charge-limit slider runs 80-100 in steps of 5."""
    entity = (
        await platform_entities(number_platform, make_mower("Luba-VS123456", "2.1.1.5"))
    )["charge_limit"]
    assert entity.native_min_value == 80
    assert entity.native_max_value == 100
    assert entity.native_step == 5
    assert entity.entity_description.mode is NumberMode.SLIDER


async def test_moving_the_slider_sets_a_whole_percent() -> None:
    """The protocol field is an int; HA hands the setter a float."""
    entity = (
        await platform_entities(number_platform, make_mower("Luba-VS123456", "2.1.1.5"))
    )["charge_limit"]
    await entity.entity_description.set_async_fn(entity.coordinator, 85.0)
    entity.coordinator.async_set_charge_limit.assert_awaited_once_with(85)


@pytest.mark.parametrize(("reported", "expected"), [(0, None), (85, 85)])
async def test_an_unreported_limit_shows_as_unknown(
    reported: int, expected: int | None
) -> None:
    """0 is the proto default, not a limit the user could ever have set."""
    mower = make_mower("Luba-VS123456", "2.1.1.5")
    mower.reporting_coordinator.data.mower_state.charge_settings.charge_limit = reported
    entity = (await platform_entities(number_platform, mower))["charge_limit"]
    assert entity.native_value == expected


@pytest.mark.parametrize(
    "command", ["query_battery_info", "read_recharge_level", "read_resume_level"]
)
@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_startup_read_is_gated_the_same_way(
    hass: HomeAssistant, command: str, name: str, firmware: str, expected: bool
) -> None:
    """The probes are not sent to devices the app would not show the page for."""
    coordinator = await make_report_coordinator(hass, name, firmware)

    await coordinator._async_ensure_startup_reads()  # noqa: SLF001

    assert (command in sent_commands(coordinator)) is expected


async def test_setting_the_limit_resends_the_off_peak_window() -> None:
    """bms_ctrl_info_msg carries every setting, so the window must be echoed back."""
    device = MowingDevice()
    settings = device.mower_state.charge_settings
    settings.charge_limit = 100
    settings.peak_valley_charge = True
    settings.valley_charge_start_time = 23
    settings.valley_charge_end_time = 6
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name="Luba-VS123456"
    )

    await coordinator.async_set_charge_limit(90)

    coordinator.manager.send_command_and_wait.assert_awaited_once_with(
        "Luba-VS123456",
        "set_battery_info",
        "bms_ctrl_info_msg",
        prefer_ble=False,
        priority=Priority.USER,
        smart_charge=False,
        charge_limit=90,
        peak_valley_charge=True,
        valley_charge_start_time=23,
        valley_charge_end_time=6,
    )


_BATTERY_ENTITY_KEYS = [
    ("number", "charge_limit"),
    ("switch", "smart_charge"),
    ("number", "recharge_level"),
    ("number", "resume_level"),
    ("switch", "smart_recharge_level"),
    ("switch", "smart_resume_level"),
]


@pytest.mark.parametrize(("platform", "key"), _BATTERY_ENTITY_KEYS)
def test_every_translation_names_the_new_entities(platform: str, key: str) -> None:
    """Every locale and icons.json know the new number and switch keys."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        data = json.loads(path.read_text())
        assert data["entity"][platform][key]["name"], path
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert icons[platform][key]["default"].startswith("mdi:")


def test_every_locale_explains_an_unread_battery_write() -> None:
    """The refusal is raised as a translated HomeAssistantError."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    for path in files:
        data = json.loads(path.read_text())
        assert data["exceptions"]["battery_settings_unread"]["message"], path


@pytest.mark.regression
async def test_the_smart_charging_switch_is_unknown_until_the_device_reports() -> None:
    """An unread ChargeSettings showed "off" although the mower was in smart mode."""
    entity = (
        await platform_entities(switch_platform, make_mower("Luba-VS123456", "2.1.1.5"))
    )["smart_charge"]
    assert entity.is_on is None


@pytest.mark.parametrize(
    ("platform", "key"),
    [("number", k) for k in ("recharge_level", "resume_level")]
    + [("switch", k) for k in ("smart_recharge_level", "smart_resume_level")],
)
@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_charge_levels_share_the_battery_page_gate(
    platform: str, key: str, name: str, firmware: str, expected: bool
) -> None:
    """The app's Battery page shows both levels with no gate of their own."""
    module = number_platform if platform == "number" else switch_platform
    entities = await platform_entities(module, make_mower(name, firmware))
    assert (key in entities) is expected


@pytest.mark.parametrize(
    ("key", "low", "high"), [("recharge_level", 15, 30), ("resume_level", 40, 100)]
)
async def test_the_level_sliders_match_the_app(key: str, low: int, high: int) -> None:
    """RN battery page: min_progress/max_progress 15-30 and 40-100, default step 1."""
    entity = (
        await platform_entities(number_platform, make_mower("Luba-VS123456", "2.1.1.5"))
    )[key]
    assert (entity.native_min_value, entity.native_max_value) == (low, high)
    assert entity.native_step == 1
    assert entity.native_unit_of_measurement == "%"


@pytest.mark.parametrize(
    ("field", "key", "reported", "expected"),
    [
        ("recharge_level", "recharge_level", 0, None),
        ("recharge_level", "recharge_level", 20, 20),
        ("recharge_level", "recharge_level", -1, 30),
        ("resume_level", "resume_level", 0, None),
        ("resume_level", "resume_level", 60, 60),
        ("resume_level", "resume_level", -1, 100),
    ],
)
async def test_a_level_shows_what_the_app_shows(
    field: str, key: str, reported: int, expected: int | None
) -> None:
    """Unread is unknown; smart (-1) shows the slider's top, as the app does."""
    mower = make_mower("Luba-VS123456", "2.1.1.5")
    setattr(mower.reporting_coordinator.data.mower_state, field, reported)
    entity = (await platform_entities(number_platform, mower))[key]
    assert entity.native_value == expected


@pytest.mark.parametrize(
    ("field", "key", "reported", "expected"),
    [
        ("recharge_level", "smart_recharge_level", 0, None),
        ("recharge_level", "smart_recharge_level", -1, True),
        ("recharge_level", "smart_recharge_level", 20, False),
        ("resume_level", "smart_resume_level", 0, None),
        ("resume_level", "smart_resume_level", -1, True),
        ("resume_level", "smart_resume_level", 60, False),
    ],
)
async def test_a_smart_level_switch_follows_the_reported_level(
    field: str, key: str, reported: int, expected: bool | None
) -> None:
    """Unread is unknown, not "off"; only -1 is smart."""
    mower = make_mower("Luba-VS123456", "2.1.1.5")
    setattr(mower.reporting_coordinator.data.mower_state, field, reported)
    entity = (await platform_entities(switch_platform, mower))[key]
    assert entity.is_on is expected


@pytest.mark.parametrize(
    ("key", "setter", "value", "sent"),
    [
        ("smart_recharge_level", "async_set_recharge_level", True, -1),
        ("smart_recharge_level", "async_set_recharge_level", False, 30),
        ("smart_resume_level", "async_set_resume_level", True, -1),
        ("smart_resume_level", "async_set_resume_level", False, 100),
    ],
)
async def test_the_smart_level_switch_writes_what_the_app_writes(
    key: str, setter: str, value: bool, sent: int
) -> None:
    """Leaving smart, the app writes the slider's top (``e ? 30 : -1``, ``e ? 100 : -1``)."""
    entity = (
        await platform_entities(switch_platform, make_mower("Luba-VS123456", "2.1.1.5"))
    )[key]
    await entity.entity_description.set_fn(entity.coordinator, value)
    getattr(entity.coordinator, setter).assert_awaited_once_with(sent)


@pytest.mark.parametrize(
    ("key", "setter"),
    [
        ("recharge_level", "async_set_recharge_level"),
        ("resume_level", "async_set_resume_level"),
    ],
)
async def test_moving_a_level_slider_sets_a_whole_percent(
    key: str, setter: str
) -> None:
    """A slider write is a custom level, which is how the app leaves smart too."""
    entity = (
        await platform_entities(number_platform, make_mower("Luba-VS123456", "2.1.1.5"))
    )[key]
    await entity.entity_description.set_async_fn(entity.coordinator, 25.0)
    getattr(entity.coordinator, setter).assert_awaited_once_with(25)
