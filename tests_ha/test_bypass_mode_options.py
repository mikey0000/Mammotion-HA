"""Obstacle-detection options follow the app's value and label scheme (issue #887).

The app's off position is value 1 on the new Off/Standard/Sensitive list and
value 0 on the older touch lists, and it labels a value by which list the
device has.  The select therefore builds its options from label keys rather
than from the protocol member names.

The stubbed suite could only confirm that ``option_key`` appeared in the
source.  Here the select platform really runs, so the options a mower is
offered and the value each one writes are the real ones.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.data.model.mowing_modes import DetectionStrategy

from custom_components.mammotion import select as select_platform

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_LUBA_1 = ("Luba-123456", "")
_OLD_FIRMWARE = ("Luba-VS123456", "1.11.0")
_NEW_FIRMWARE = ("Luba-VS123456", "1.12.0")

# The list that still carries value 0, which is what makes 1 "Slow touch" rather
# than the off position.
_LEGACY_OPTIONS = DetectionStrategy.for_device(*_OLD_FIRMWARE)


def _mower(name: str, firmware: str = "") -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setup walks."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    mower = MagicMock()
    mower.name = name
    mower.device.device_name = name
    mower.device.product_key = ""
    mower.api.get_device_by_name.return_value = None
    coordinator = mower.reporting_coordinator
    coordinator.data = device
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings = OperationSettings()
    return mower


async def _bypass_entities(*mowers: MagicMock) -> list[Any]:
    """Run the select platform's own setup and return its bypass entities, in order."""
    entry = MagicMock()
    entry.runtime_data.mowers = list(mowers)
    entry.runtime_data.spino = []
    add_entities = MagicMock()
    await select_platform.async_setup_entry(MagicMock(), entry, add_entities)
    return [
        entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
        if entity.entity_description.key == "bypass_mode"
    ]


def _presentable_option_keys() -> set[str]:
    """Every label key any device's list can present."""
    keys: set[str] = set()
    for name, firmware in (_LUBA_1, _OLD_FIRMWARE, _NEW_FIRMWARE):
        options = DetectionStrategy.for_device(name, firmware)
        keys.update(strategy.option_key(options) for strategy in options)
    return keys


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        (_LUBA_1, ["off", "slow_touch", "less_touch"]),
        (_OLD_FIRMWARE, ["off", "slow_touch", "less_touch", "standard"]),
        (_NEW_FIRMWARE, ["off", "standard", "sensitive"]),
    ],
)
async def test_options_are_label_keys_not_member_names(
    device: tuple[str, str], expected: list[str]
) -> None:
    """``s.name`` would show "Slow touch" for the new list's off position."""
    entity = (await _bypass_entities(_mower(*device)))[0]
    assert entity.entity_description.options == expected


@pytest.mark.parametrize(
    ("device", "key", "ultra_wave"),
    [
        (_LUBA_1, "off", 0),
        (_LUBA_1, "slow_touch", 1),
        (_LUBA_1, "less_touch", 2),
        (_NEW_FIRMWARE, "off", 1),
        (_NEW_FIRMWARE, "standard", 10),
        (_NEW_FIRMWARE, "sensitive", 11),
    ],
)
async def test_the_chosen_label_writes_the_value_that_list_uses(
    device: tuple[str, str], key: str, ultra_wave: int
) -> None:
    """The off position is 0 on the old lists and 1 on the new one."""
    entity = (await _bypass_entities(_mower(*device)))[0]
    entity.entity_description.set_fn(entity.coordinator, key)
    assert entity.coordinator.operation_settings.ultra_wave == ultra_wave


async def test_the_device_list_is_bound_per_description() -> None:
    """The description outlives the loop; a closure would read the last mower's list."""
    luba_1, new = await _bypass_entities(_mower(*_LUBA_1), _mower(*_NEW_FIRMWARE))

    assert luba_1.entity_description.options == ["off", "slow_touch", "less_touch"]
    luba_1.entity_description.set_fn(luba_1.coordinator, "off")
    assert luba_1.coordinator.operation_settings.ultra_wave == 0
    new.entity_description.set_fn(new.coordinator, "off")
    assert new.coordinator.operation_settings.ultra_wave == 1


def test_every_translation_names_the_keys_the_lists_present() -> None:
    """direct_touch/no_touch are gone; the keys follow the app's own strings."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        state = json.loads(path.read_text())["entity"]["select"]["bypass_mode"]["state"]
        assert set(state) == _presentable_option_keys(), path
        assert all(state.values()), path


@pytest.mark.parametrize("raw", ["0", "1", "2", "10", "11"])
def test_the_service_selector_matches_the_entity_wording(raw: str) -> None:
    """start_mow labels raw values, but must not disagree with the entity."""
    key = DetectionStrategy(int(raw)).option_key(_LEGACY_OPTIONS)
    for path in [
        _ROOT / "strings.json",
        *sorted((_ROOT / "translations").glob("*.json")),
    ]:
        data = json.loads(path.read_text())
        options = data["selector"]["ultra_wave"]["options"]
        assert set(options) == {"0", "1", "2", "10", "11"}, path
        assert options[raw] == data["entity"]["select"]["bypass_mode"]["state"][key], (
            path
        )


def test_only_english_keeps_the_english_wording() -> None:
    """The APK's own per-locale strings, not English placeholders."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    english_state = english["entity"]["select"]["bypass_mode"]["state"]
    for path in sorted((_ROOT / "translations").glob("*.json")):
        if path.stem in ("en", "da", "sv"):
            # da/sv legitimately share "Standard" with English.
            continue
        state = json.loads(path.read_text())["entity"]["select"]["bypass_mode"]["state"]
        assert state["off"] != english_state["off"], path
        assert state["slow_touch"] != english_state["slow_touch"], path
