"""Positioning sensors follow the app's model gates: vision devices get vision rows, LiDAR devices a LiDAR row.

The app shows "Visual Positioning" and camera brightness only where ``isSupportVision()``
holds and "LiDAR Positioning" only where ``isSupportRadar()`` does.  The Luba 3 is a LiDAR
device whose vision block is uninitialised memory, so a vision sensor on it can only ever
read nonsense.  The frames below are the user's real ``vslam_status`` / ``vio_state`` values.
"""

import json
from pathlib import Path

import pytest
from battery_support import make_mower, platform_entities
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion import sensor as sensor_platform
from custom_components.mammotion.models import MammotionMowerData
from custom_components.mammotion.sensor import (
    FUSED_LOCALIZATION_TYPES,
    LIDAR_POSITIONING_TYPES,
    VISION_POSITIONING_TYPES,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_LOCALES = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]

LUBA_3 = "Luba-VAME9R5S"
LUBA_2 = "Luba-VS563L6H"
YUKA = "Yuka-MNTXVHBE"
YUKA_MINI_2_VISION = "Yuka-MV6ABCDE"  # pure-visual X5: the app shows neither row
LUBA_1 = "Luba-VFPEV2AJ"

_VISION_KEYS = {"visual_positioning_status", "camera_brightness"}
_LIDAR_KEYS = {"lidar_positioning_status"}
_FUSED_KEYS = {"fused_localization_status", "vision_survival"}
_POSITIONING_KEYS = _VISION_KEYS | _LIDAR_KEYS | _FUSED_KEYS

_DESCRIPTIONS = {
    d.key: d
    for d in (
        *VISION_POSITIONING_TYPES,
        *LIDAR_POSITIONING_TYPES,
        *FUSED_LOCALIZATION_TYPES,
    )
}


def _device(
    vslam_status: int = 0, vio_state: int = 0, brightness: int = 0
) -> MowingDevice:
    device = MowingDevice()
    device.report_data.dev.vslam_status = vslam_status
    device.report_data.vision_info.vio_state = vio_state
    device.report_data.vision_info.brightness = brightness
    return device


def _luba_3_frame() -> MowingDevice:
    """Return the Luba 3 on 2.3.30.39: fuse byte 1 (RTK fixed) beside an uninitialised vision block."""
    return _device(vslam_status=257, vio_state=232, brightness=240)


def _luba_2_extended_frame() -> MowingDevice:
    """Return the Luba 2 on 1.30.29.26 while vision extended its fix: fuse 2, survival 100 %."""
    return _device(vslam_status=0x640201, vio_state=2, brightness=1)


def _sensor_mower(hass: HomeAssistant, name: str) -> MammotionMowerData:
    """``make_mower`` plus what the sensor platform's registry pass and error sensors read."""
    mower = make_mower(name)
    mower.error_coordinator.unique_name = name
    mower.reporting_coordinator.hass = hass
    return mower


async def _positioning_keys(hass: HomeAssistant, name: str) -> set[str]:
    entities = await platform_entities(sensor_platform, _sensor_mower(hass, name), hass)
    return set(entities) & _POSITIONING_KEYS


@pytest.mark.regression
async def test_the_luba_3_gets_a_lidar_sensor_and_no_vision_sensors(
    hass: HomeAssistant,
) -> None:
    """It was given the vision pair (gated on ``is_luba_pro``), which read "Unknown" forever."""
    assert await _positioning_keys(hass, LUBA_3) == _LIDAR_KEYS | _FUSED_KEYS


@pytest.mark.parametrize("name", [LUBA_2, YUKA])
async def test_a_vision_device_gets_the_vision_sensors_and_no_lidar_sensor(
    hass: HomeAssistant, name: str
) -> None:
    """Luba 2 and Yuka are in the app's vision set, not its radar set."""
    assert await _positioning_keys(hass, name) == _VISION_KEYS | _FUSED_KEYS


async def test_a_device_in_neither_app_set_gets_only_the_fused_diagnostics(
    hass: HomeAssistant,
) -> None:
    """The pure-visual X5 models show neither row in the app, but still report ``vslam_status``."""
    assert await _positioning_keys(hass, YUKA_MINI_2_VISION) == _FUSED_KEYS


async def test_the_luba_1_gets_no_positioning_sensor(hass: HomeAssistant) -> None:
    """The app decodes ``vslam_status`` only for Luba 2 and later."""
    assert await _positioning_keys(hass, LUBA_1) == set()


async def test_the_gate_resolves_through_the_product_key(hass: HomeAssistant) -> None:
    """The model comes from the bound device record, which carries the product key from setup."""
    mower = _sensor_mower(hass, "Mower-ABC123")
    mower.device.product_key = "a1iMygIwxFC"  # a Luba 2

    entities = await platform_entities(sensor_platform, mower, hass)

    assert set(entities) & _POSITIONING_KEYS == _VISION_KEYS | _FUSED_KEYS


def test_the_luba_3_s_real_frame_reads_as_good_lidar_positioning() -> None:
    """Its fuse byte says RTK fixed, the app's "LiDAR Positioning: Good"."""
    lidar = _DESCRIPTIONS["lidar_positioning_status"]

    assert lidar.value_fn(_luba_3_frame()) == "good"


@pytest.mark.parametrize("vslam_status", [0, 0x0201, 0x0401, 0x0501])
def test_lidar_positioning_is_none_unless_the_fix_is_rtk_fixed(
    vslam_status: int,
) -> None:
    """``refreshRadarStatusUI``: fuse 1 is Good, anything else None (extended, failed, unnamed)."""
    lidar = _DESCRIPTIONS["lidar_positioning_status"]

    assert lidar.value_fn(_device(vslam_status=vslam_status)) == "none"


def test_an_extended_fix_shows_its_state_and_survival() -> None:
    """The Luba 2 frame: fuse 2 (kRTkExtended) with a full survival bar."""
    assert (
        _DESCRIPTIONS["fused_localization_status"].value_fn(_luba_2_extended_frame())
        == "rtk_extended_vision"
    )
    assert _DESCRIPTIONS["vision_survival"].value_fn(_luba_2_extended_frame()) == 100


def test_vision_survival_is_unknown_outside_a_vision_extension() -> None:
    """The app shows the survival bar only for fuse 2 and 3; the Luba 3 frame is fuse 1."""
    assert _DESCRIPTIONS["vision_survival"].value_fn(_luba_3_frame()) is None


def test_a_vision_device_s_real_frame_reads_good() -> None:
    """The Luba 2's valid vision block: vio_state 2, brightness 1."""
    assert (
        _DESCRIPTIONS["visual_positioning_status"].value_fn(_luba_2_extended_frame())
        == "SIGNAL_GOOD"
    )
    assert (
        _DESCRIPTIONS["camera_brightness"].value_fn(_luba_2_extended_frame()) == "good"
    )


@pytest.mark.regression
@pytest.mark.parametrize(
    ("raw", "state"), [(0, "dark"), (1, "good"), (2, "intense"), (240, "unknown")]
)
def test_camera_brightness_follows_the_app_s_mapping(raw: int, state: str) -> None:
    """2 read "Dark" and 1 read "Light"; the app shows 0 Dark, 1 Good, 2 Intense, else "--"."""
    brightness = _DESCRIPTIONS["camera_brightness"]

    assert brightness.value_fn(_device(brightness=raw)) == state


@pytest.mark.parametrize(
    "key", sorted(key for key, d in _DESCRIPTIONS.items() if d.options is not None)
)
def test_every_value_the_sensor_can_report_is_an_option(key: str) -> None:
    """HA rejects an enum value missing from ``options``; walk every byte each field can carry."""
    description = _DESCRIPTIONS[key]
    assert description.options is not None
    produced = {
        description.value_fn(
            _device(vslam_status=raw << 8, vio_state=raw, brightness=raw)
        )
        for raw in range(256)
    }

    assert produced <= set(description.options)


def test_the_enum_options_are_the_translated_states() -> None:
    """These are the state keys the translations carry; the visual ones stay upper-case for saved history."""
    assert _DESCRIPTIONS["visual_positioning_status"].options == [
        "SIGNAL_UNKNOWN",
        "SIGNAL_NONE",
        "SIGNAL_INIT",
        "SIGNAL_GOOD",
        "SIGNAL_BAD",
    ]
    assert _DESCRIPTIONS["camera_brightness"].options == [
        "unknown",
        "dark",
        "good",
        "intense",
    ]
    assert _DESCRIPTIONS["fused_localization_status"].options == [
        "unknown",
        "no_pose",
        "rtk_fixed",
        "rtk_extended_vision",
        "vision_extended",
        "vision_extended_failed",
    ]
    assert _DESCRIPTIONS["lidar_positioning_status"].options == ["good", "none"]


@pytest.mark.parametrize("key", sorted(_FUSED_KEYS))
def test_the_fused_diagnostics_are_disabled_by_default(key: str) -> None:
    """They are raw diagnostics; the positioning row a model's app shows is the enabled one."""
    assert _DESCRIPTIONS[key].entity_registry_enabled_default is False


@pytest.mark.parametrize("path", _LOCALES, ids=lambda path: path.name)
def test_every_locale_names_each_sensor_and_every_state(path: Path) -> None:
    """A missing state renders as its raw key; a missing name as the entity id."""
    sensors = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
    missing = [
        f"{key}.{state}"
        for key, description in _DESCRIPTIONS.items()
        for state in ["name", *(description.options or [])]
        if not (sensors[key] if state == "name" else sensors[key]["state"]).get(state)
    ]

    assert missing == []
