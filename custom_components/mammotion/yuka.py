"""Yuka model helpers and app-facing option mappings."""

from __future__ import annotations

from typing import Any

from pymammotion.utility.device_type import DeviceType

YUKA_ML_PREFIX = "Yuka-ML"

LAP_OPTIONS = ["none", "one", "two", "three"]
OBSTACLE_OPTIONS = ["off", "standard", "sensitive"]
BLADE_SPEED_OPTIONS = ["low", "high"]
ROUTE_TO_DOCK_OPTIONS = ["direct", "follow_perimeter"]
VOICE_VOLUME_LEVEL_OPTIONS = ["one", "two", "three", "four", "five"]
PATTERN_FAMILY_OPTIONS = ["perimeter_only", "stripes", "grid"]
STRIPES_PATTERN_OPTIONS = ["efficient", "random", "custom"]
GRID_PATTERN_OPTIONS = ["default", "random", "custom"]
WILDLIFE_SAFETY_OPTIONS = ["low_speed_mowing", "stop_mowing"]

PATTERN_FAMILY_VALUES = {"perimeter_only": 3, "stripes": 0, "grid": 1}
STRIPES_PATTERN_VALUE = PATTERN_FAMILY_VALUES["stripes"]
GRID_PATTERN_VALUE = PATTERN_FAMILY_VALUES["grid"]
LAP_VALUES = {"none": 0, "one": 1, "two": 2, "three": 3}
OBSTACLE_VALUES = {"off": 0, "standard": 10, "sensitive": 11}
BLADE_SPEED_VALUES = {"low": 1, "high": 2}
ROUTE_TO_DOCK_VALUES = {"direct": 0, "follow_perimeter": 1}
VOICE_VOLUME_VALUES = {"one": 20, "two": 40, "three": 60, "four": 80, "five": 100}
WILDLIFE_SAFETY_VALUES = {"low_speed_mowing": 2, "stop_mowing": 1}


def is_yuka_2(device_name: str) -> bool:
    """Return True for Yuka 2 / Yuka ML devices."""
    is_yuka_ml = getattr(DeviceType, "is_yuka_ml", None)
    return device_name.startswith(YUKA_ML_PREFIX) or (
        callable(is_yuka_ml) and is_yuka_ml(device_name)
    )


def is_yuka_mini_or_ml(device_name: str) -> bool:
    """Return True for Yuka models that do not expose legacy Yuka sweeper features."""
    return is_yuka_2(device_name) or DeviceType.is_yuka_mini(device_name)


def yuka_value_option(options: list[str], value: Any, values: dict[str, int]) -> str:
    """Map a numeric device value back to an exposed option."""
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return options[0]

    for option, option_value in values.items():
        if option_value == numeric_value and option in options:
            return option
    return options[0]


def voice_volume_option(volume: Any) -> str:
    """Map a 0-100 voice volume to one of the five app levels."""
    try:
        numeric_volume = int(volume)
    except (TypeError, ValueError):
        numeric_volume = 0
    if numeric_volume <= 20:
        return "one"
    if numeric_volume <= 40:
        return "two"
    if numeric_volume <= 60:
        return "three"
    if numeric_volume <= 80:
        return "four"
    return "five"
