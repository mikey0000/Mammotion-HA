"""Sweep, dump and dump-point controls are offered where the app offers them.

The stubbed suite could only confirm that the capability check and the command
names appeared in the source.  Here the switch, button and lawn_mower
platforms really run — the dump-point actions are registered on a real
``hass`` — and the commands are read off the calls the coordinator makes.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.messaging.command_queue import Priority

from custom_components.mammotion import button as button_platform
from custom_components.mammotion import lawn_mower as lawn_mower_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator
from custom_components.mammotion.entity import supports_grass_collection

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_SERVICE_KEYS = (
    "start_dump_point_setup",
    "add_dump_point",
    "undo_dump_point",
    "finish_dump_point_setup",
    "finish_outside_dump_point",
)
_SWITCH_KEYS = ("manual_grass_collection", "manual_grass_dump")

_YUKA = "Yuka-123456"
_YUKA_VP = "Yuka-VP1234"
_LUBA = "Luba-VS123456"


def _mower(name: str, collector: int = 1) -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setups walk."""
    device = MowingDevice()
    device.report_data.dev.collector_status.collector_installation_status = collector
    mower = MagicMock()
    mower.name = name
    mower.device.device_name = name
    mower.device.product_key = ""
    mower.device.iot_id = "iot-id"
    mower.api.get_device_by_name.return_value = None
    coordinator = mower.reporting_coordinator
    coordinator.data = device
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings = OperationSettings()
    coordinator.grass_collector_installed = device.report_data.dev.collector_installed
    return mower


async def _switches(mower: MagicMock) -> dict[str, Any]:
    """Run the switch platform's own setup and return what it created."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    add_entities = MagicMock()
    await switch_platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


async def _lawn_mower(hass: HomeAssistant, mower: MagicMock) -> Any:
    """Run the lawn_mower platform's own setup, registering its actions on hass."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    add_entities = MagicMock()
    await lawn_mower_platform.async_setup_entry(hass, entry, add_entities)
    return add_entities.call_args[0][0][0]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (_YUKA, True),
        (_YUKA_VP, True),
        (_LUBA, False),
        ("Yuka-MN1234", False),
        ("Luba-123456", False),
    ],
)
def test_capability_helper_matches_the_app(name: str, expected: bool) -> None:
    """isSupportGrassCutting() is the original Yuka and the Yuka VP, nothing else."""
    assert supports_grass_collection(name) is expected


@pytest.mark.parametrize(("name", "expected"), [(_YUKA, True), (_LUBA, False)])
@pytest.mark.parametrize("key", _SWITCH_KEYS)
async def test_switch_adds_sweep_and_dump_only_behind_the_gate(
    name: str, expected: bool, key: str
) -> None:
    """A mower that takes no collector must not grow the manual toggles."""
    assert (key in await _switches(_mower(name))) is expected


@pytest.mark.parametrize("key", _SWITCH_KEYS)
@pytest.mark.parametrize(("collector", "expected"), [(0, False), (1, True)])
async def test_manual_toggles_need_a_collector_fitted(
    key: str, collector: int, expected: bool
) -> None:
    """The app hides both controls on collector_installation_status == 0."""
    switches = await _switches(_mower(_YUKA, collector=collector))
    assert switches[key].available is expected


async def test_dump_points_are_lawn_mower_actions_not_buttons(
    hass: HomeAssistant,
) -> None:
    """Each point is taken at the current position, so these are sequenced actions."""
    await _lawn_mower(hass, _mower(_YUKA))
    for key in _SERVICE_KEYS:
        assert hass.services.has_service(DOMAIN, key), key

    entry = MagicMock()
    entry.runtime_data.mowers = [_mower(_YUKA)]
    entry.runtime_data.spino = []
    entry.runtime_data.RTK = []
    add_entities = MagicMock()
    await button_platform.async_setup_entry(MagicMock(), entry, add_entities)
    buttons = [
        entity.entity_description.key
        for call in add_entities.call_args_list
        for entity in call[0][0]
    ]
    assert not [key for key in buttons if "dump" in key]


@pytest.mark.parametrize("key", _SERVICE_KEYS)
def test_the_actions_target_lawn_mower_entities(key: str) -> None:
    """services.yaml drives the target picker, which HA does not derive from the code."""
    services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    assert services[key]["target"]["entity"]["domain"] == "lawn_mower"


@pytest.mark.parametrize(
    ("method", "command"),
    [
        ("async_start_dump_point_setup", "async_enter_dump_point_setup"),
        ("async_add_dump_point", "async_add_dump_point"),
        ("async_undo_dump_point", "async_revoke_dump_point"),
        ("async_finish_dump_point_setup", "async_exit_dump_point_setup"),
        ("async_finish_outside_dump_point", "async_finish_outside_dump_point"),
    ],
)
async def test_dump_point_actions_reject_mowers_without_a_collector(
    hass: HomeAssistant, method: str, command: str
) -> None:
    """Every handler runs the capability check before touching the coordinator."""
    entity = await _lawn_mower(hass, _mower(_LUBA))
    setattr(entity.coordinator, command, AsyncMock())

    with pytest.raises(HomeAssistantError) as raised:
        await getattr(entity, method)()

    assert raised.value.translation_key == "grass_collection_unsupported"
    assert getattr(entity.coordinator, command).await_count == 0


@pytest.mark.parametrize(
    ("method", "command"),
    [
        ("async_start_dump_point_setup", "async_enter_dump_point_setup"),
        ("async_add_dump_point", "async_add_dump_point"),
        ("async_undo_dump_point", "async_revoke_dump_point"),
        ("async_finish_dump_point_setup", "async_exit_dump_point_setup"),
        ("async_finish_outside_dump_point", "async_finish_outside_dump_point"),
    ],
)
async def test_dump_point_actions_reach_the_coordinator_on_a_yuka(
    hass: HomeAssistant, method: str, command: str
) -> None:
    """Undo maps onto revoke, and finishing outside the map onto its own command."""
    entity = await _lawn_mower(hass, _mower(_YUKA))
    setattr(entity.coordinator, command, AsyncMock())

    await getattr(entity, method)()

    getattr(entity.coordinator, command).assert_awaited_once_with()


@pytest.mark.parametrize(
    ("method", "args", "command", "payload"),
    [
        (
            "async_set_grass_collection",
            (True,),
            "manual_grass_collection",
            {"collect_ctrl": 1},
        ),
        (
            "async_set_grass_collection",
            (False,),
            "manual_grass_collection",
            {"collect_ctrl": 0},
        ),
        ("async_set_grass_dump", (True,), "manual_pour_grass", {"unload_ctrl": 1}),
        ("async_enter_dump_point_setup", (), "enter_dumping_status", {}),
        ("async_add_dump_point", (), "add_dump_point", {}),
        ("async_revoke_dump_point", (), "revoke_dump_point", {}),
        ("async_exit_dump_point_setup", (), "exit_dumping_status", {}),
        ("async_finish_outside_dump_point", (), "out_drop_dumping_add", {}),
    ],
)
async def test_coordinator_sends_the_commands_the_app_sends(
    method: str, args: tuple[Any, ...], command: str, payload: dict[str, int]
) -> None:
    """Each control maps onto the pymammotion command the APK's helper builds."""
    coordinator = AsyncMock()

    await getattr(MammotionBaseUpdateCoordinator, method)(coordinator, *args)

    coordinator.async_send_command.assert_awaited_once_with(
        command, priority=Priority.USER, **payload
    )


@pytest.mark.parametrize(
    ("prop", "accessor"),
    [
        ("grass_collection_state", "collector_state"),
        ("grass_dump_state", "dump_state"),
        ("grass_collector_installed", "collector_installed"),
    ],
)
def test_state_comes_from_pymammotion_not_a_local_bit_decode(
    prop: str, accessor: str
) -> None:
    """The sensor_status split lives in the library, next to its sibling accessors."""
    device = MowingDevice()
    device.report_data.dev.collector_status.collector_installation_status = 1
    device.report_data.dev.sensor_status = (1 << 24) | (3 << 27)
    coordinator = MagicMock()
    coordinator.data = device

    value = getattr(MammotionBaseUpdateCoordinator, prop).fget(coordinator)

    assert value == getattr(device.report_data.dev, accessor)


def test_every_translation_names_the_new_entities_and_services() -> None:
    """Every locale, icons.json and the exception key are in sync."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        data = json.loads(path.read_text())
        for key in _SWITCH_KEYS:
            assert data["entity"]["switch"][key]["name"], (path, key)
        for key in _SERVICE_KEYS:
            assert data["services"][key]["name"], (path, key)
            assert data["services"][key]["description"], (path, key)
        assert data["exceptions"]["grass_collection_unsupported"]["message"], path
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    for key in _SWITCH_KEYS:
        assert icons["switch"][key]["default"].startswith("mdi:")


def test_translations_are_not_english_placeholders() -> None:
    """Non-English locales translate the names rather than copying English."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    for path in sorted((_ROOT / "translations").glob("*.json")):
        if path.stem == "en":
            continue
        data = json.loads(path.read_text())
        for key in _SWITCH_KEYS:
            assert (
                data["entity"]["switch"][key]["name"]
                != english["entity"]["switch"][key]["name"]
            ), (path, key)
        for key in _SERVICE_KEYS:
            assert (
                data["services"][key]["description"]
                != english["services"][key]["description"]
            ), (path, key)
