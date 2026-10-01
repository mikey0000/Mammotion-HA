"""The per-zone "Task area" sensors of the running job.

From a real Luba 3 (Luba-VAME9R5S): idle on the dock (``sys_status`` 11, no
breakpoint) it sent the task-area buffer ``[3, 0, 1, 1, 2]`` — zone hash ``1``,
the device's "no zone" placeholder — and a "Task area path" sensor appeared.
"""

import json
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from area_rename_support import (
    AREA_HASH,
    DEVICE_AREA_NAME,
    ECHOED_NAME,
    AreaRenameRig,
    make_area_rename_rig,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.enums import TaskAreaStatus
from pymammotion.data.model.hash_list import AreaHashNameList, FrameList
from pymammotion.utility.constant import WorkMode
from pytest_homeassistant_custom_component.common import MockEntityPlatform

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.sensor import (
    async_add_task_area_entities,
    async_remove_orphaned_task_area_entities,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_DEVICE = "Luba-VAME9R5S"
_ZONE = 8377881458226819852
_PATH = 950717014274746940


def _coordinator(
    hass: HomeAssistant,
    *,
    sys_status: WorkMode = WorkMode.MODE_WORKING,
    bp_info: int = 0,
    zones: dict[int, TaskAreaStatus] | None = None,
) -> MammotionReportUpdateCoordinator:
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    coordinator.hass = hass
    coordinator.unique_name = _DEVICE
    coordinator.device_name = _DEVICE
    device = MowingDevice()
    device.map.area = {_ZONE: FrameList()}
    device.map.area_name = [AreaHashNameList(name="Framsida 1", hash=_ZONE)]
    device.map.path = {_PATH: FrameList()}
    device.report_data.dev.sys_status = sys_status.value
    device.report_data.work.bp_info = bp_info
    zones = {_ZONE: TaskAreaStatus.MOWING} if zones is None else zones
    device.events.work_tasks_event.hash_area_map = dict(zones)
    device.events.work_tasks_event.ids = list(zones)
    coordinator.data = device
    return coordinator


def _sync(
    coordinator: MammotionReportUpdateCoordinator, state: dict[str, Any]
) -> MagicMock:
    add = MagicMock()
    async_add_task_area_entities(coordinator, state["added"], state["entities"], add)
    return add


def _state() -> dict[str, Any]:
    return {"added": set(), "entities": {}}


def _added(add: MagicMock) -> list[Any]:
    return [entity for call in add.call_args_list for entity in call.args[0]]


class TestAreaName:
    """A hash names its area, else a mow-path segment, else nothing known."""

    async def test_an_area_hash_is_named(self, hass: HomeAssistant) -> None:
        """An area's name is the one the app shows."""
        assert _coordinator(hass).get_area_entity_name(_ZONE) == "Framsida 1"

    async def test_a_path_hash_is_the_path(self, hass: HomeAssistant) -> None:
        """A hash in the map's paths is a mow-path segment, as the work zone can be."""
        assert _coordinator(hass).get_area_entity_name(_PATH) == "path"

    async def test_an_unmatched_hash_is_unknown(self, hass: HomeAssistant) -> None:
        """Any hash that was not an area used to be called "path", matched or not."""
        assert _coordinator(hass).get_area_entity_name(1234) == "unknown"

    async def test_an_area_known_only_by_name_is_named(
        self, hass: HomeAssistant
    ) -> None:
        """Over MQTT the map fetch can stop at the area names, leaving no area frames."""
        coordinator = _coordinator(hass)
        coordinator.data.map.area = {}

        assert coordinator.get_area_entity_name(_ZONE) == "Framsida 1"


class TestActiveJob:
    """Zone sensors exist only while a job is running, paused or resumable."""

    @pytest.mark.parametrize(
        ("sys_status", "bp_info"),
        [
            (WorkMode.MODE_WORKING, 0),
            (WorkMode.MODE_PAUSE, 1),
            (WorkMode.MODE_RETURNING, 0),
            (WorkMode.MODE_CHARGING_PAUSE, 8),
            (WorkMode.MODE_READY, 1),
        ],
    )
    async def test_a_job_shows_its_zones(
        self, hass: HomeAssistant, sys_status: WorkMode, bp_info: int
    ) -> None:
        """Mowing, paused, heading home, charging mid-job or ready to resume."""
        add = _sync(
            _coordinator(hass, sys_status=sys_status, bp_info=bp_info), _state()
        )

        (entity,) = _added(add)
        assert entity.translation_placeholders == {"name": "Framsida 1"}
        assert entity.native_value == "MOWING"

    @pytest.mark.parametrize("bp_info", [0, -1])
    async def test_an_idle_mower_shows_no_zones(
        self, hass: HomeAssistant, bp_info: int
    ) -> None:
        """Idle on the dock the mower still reports its last task's zones."""
        add = _sync(
            _coordinator(hass, sys_status=WorkMode.MODE_READY, bp_info=bp_info),
            _state(),
        )

        add.assert_not_called()

    async def test_the_zones_go_when_the_job_ends(
        self, hass: HomeAssistant, entity_registry: er.EntityRegistry
    ) -> None:
        """Out of a job the sensors are removed, though the zones are still reported."""
        coordinator = _coordinator(hass)
        state = _state()
        _sync(coordinator, state)
        entity_registry.async_get_or_create(
            "sensor", "mammotion", f"{_DEVICE}_{_ZONE}_task_area"
        )

        coordinator.data.report_data.dev.sys_status = WorkMode.MODE_READY.value
        _sync(coordinator, state)

        assert state == _state()
        assert (
            entity_registry.async_get_entity_id(
                "sensor", "mammotion", f"{_DEVICE}_{_ZONE}_task_area"
            )
            is None
        )


class TestUnknownZone:
    """A zone the map does not name, or a status the library does not model."""

    async def test_an_unmatched_zone_is_named_unknown(
        self, hass: HomeAssistant
    ) -> None:
        """It was named "path", and kept that name once the map had loaded."""
        coordinator = _coordinator(hass)
        coordinator.data.map.area = {}
        coordinator.data.map.area_name = []
        state = _state()

        (entity,) = _added(_sync(coordinator, state))
        assert entity.translation_placeholders == {"name": "unknown"}

        coordinator.data.map.area = {_ZONE: FrameList()}
        coordinator.data.map.area_name = [
            AreaHashNameList(name="Framsida 1", hash=_ZONE)
        ]
        _sync(coordinator, state)

        assert entity.translation_placeholders == {"name": "Framsida 1"}

    async def test_an_unmodelled_status_is_unknown(self, hass: HomeAssistant) -> None:
        """A status newer firmware sends is shown as an option, not an invalid state."""
        coordinator = _coordinator(hass, zones={_ZONE: TaskAreaStatus(7)})

        (entity,) = _added(_sync(coordinator, _state()))

        assert entity.native_value == "UNKNOWN"
        assert entity.native_value in entity.options


async def test_a_previous_sessions_zone_sensor_is_removed_at_setup(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The phantom zone-1 sensor outlived restarts: only a live sync removed sensors."""
    coordinator = _coordinator(hass)
    for unique_id in (
        f"{_DEVICE}_1_task_area",
        f"{_DEVICE}_{_ZONE}_task_area",
        "Luba-OTHER_1_task_area",
    ):
        entity_registry.async_get_or_create("sensor", "mammotion", unique_id)

    async_remove_orphaned_task_area_entities(coordinator)

    def _exists(unique_id: str) -> bool:
        return (
            entity_registry.async_get_entity_id("sensor", "mammotion", unique_id)
            is not None
        )

    assert not _exists(f"{_DEVICE}_1_task_area")
    assert _exists(f"{_DEVICE}_{_ZONE}_task_area")
    assert _exists("Luba-OTHER_1_task_area")


@pytest.fixture
async def renamed_zone_rig(hass: HomeAssistant) -> AsyncIterator[AreaRenameRig]:
    """Build a mower mowing the zone the user renamed; shut its coordinator down after."""
    device = MowingDevice()
    device.map.area = {AREA_HASH: FrameList()}
    device.map.area_name = [AreaHashNameList(name=DEVICE_AREA_NAME, hash=AREA_HASH)]
    device.report_data.dev.sys_status = WorkMode.MODE_WORKING.value
    device.events.work_tasks_event.hash_area_map = {AREA_HASH: TaskAreaStatus.MOWING}
    device.events.work_tasks_event.ids = [AREA_HASH]
    rig = await make_area_rename_rig(hass, device)
    yield rig
    await rig.coordinator.async_shutdown()


@pytest.mark.regression
async def test_a_zone_sensor_follows_its_area_when_the_mower_renames_it(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    renamed_zone_rig: AreaRenameRig,
) -> None:
    """The sensor's placeholders followed the rename but its cached name did not.

    ``update_name`` wrote ``_attr_translation_placeholders``, which clears only the
    cached placeholders; Home Assistant kept rendering the cached ``Entity.name``,
    so the registry's ``original_name`` stayed on the area's old name.
    """
    coordinator = renamed_zone_rig.coordinator
    platform = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
    platform.config_entry = renamed_zone_rig.entry
    await platform.platform_data.async_load_translations()
    added: list[Entity] = []
    sync = partial(async_add_task_area_entities, coordinator, set(), {}, added.extend)
    sync()
    await platform.async_add_entities(added)
    coordinator.async_add_listener(sync)
    (entity,) = added
    assert entity_registry.async_get(entity.entity_id).original_name == (
        f"Task area {DEVICE_AREA_NAME}"
    ), "premise: the sensor starts on the area's old name"

    await renamed_zone_rig.receive_echo()
    await hass.async_block_till_done()

    assert entity_registry.async_get(entity.entity_id).original_name == (
        f"Task area {ECHOED_NAME}"
    )


@pytest.mark.parametrize(
    "path",
    [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))],
    ids=lambda path: path.name,
)
def test_every_locale_translates_every_zone_status(path: Path) -> None:
    """The name carries the zone's name; every status is translated."""
    entry = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"][
        "task_area_status"
    ]
    assert "{name}" in entry["name"]
    assert set(entry["state"]) == {status.name for status in TaskAreaStatus}


def test_every_zone_status_has_an_icon() -> None:
    """Every status the sensor can show has its own icon."""
    icons = json.loads((_ROOT / "icons.json").read_text(encoding="utf-8"))
    entry = icons["entity"]["sensor"]["task_area_status"]
    assert set(entry["state"]) == {status.name for status in TaskAreaStatus}
