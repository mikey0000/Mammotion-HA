"""Which stored task is running, and its dynamic sensor.

From a real run: starting plan ``179054453265003205210`` made the mower report
``bidire_reqconver_path.job_id = 17905445326500320`` — the plan id cut to 17
digits to fit an int64.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import Plan
from pymammotion.utility.constant import WorkMode

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.sensor import (
    RUNNING_TASK_STATES,
    async_sync_running_task_entity,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_PLAN_ID = "179054453265003205210"
_JOB_ID = 17905445326500320
_OTHER_PLAN = "179000000000012345678"


def _coordinator(
    hass: HomeAssistant,
    *,
    job_id: int = _JOB_ID,
    sys_status: int = WorkMode.MODE_WORKING.value,
    name: str = "Top half of bottom lawn",
) -> MammotionReportUpdateCoordinator:
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    coordinator.hass = hass
    coordinator.unique_name = "Luba-VS563L6H"
    coordinator.device_name = "Luba-VS563L6H"
    device = MowingDevice()
    device.map.plan = {
        _PLAN_ID: Plan(plan_id=_PLAN_ID, task_name=name),
        _OTHER_PLAN: Plan(plan_id=_OTHER_PLAN, task_name="Front"),
    }
    device.work.job_id = job_id
    device.report_data.dev.sys_status = sys_status
    coordinator.data = device
    return coordinator


@pytest.mark.parametrize(
    "sys_status",
    [WorkMode.MODE_WORKING, WorkMode.MODE_PAUSE, WorkMode.MODE_RETURNING],
)
def test_the_running_plan_is_the_one_the_job_id_prefixes(
    hass: HomeAssistant, sys_status: WorkMode
) -> None:
    """Throughout the job — mowing, paused or heading home — it names the plan."""
    coordinator = _coordinator(hass, sys_status=sys_status.value)

    plan = coordinator.running_plan

    assert plan is not None
    assert plan.plan_id == _PLAN_ID


@pytest.mark.parametrize(
    ("job_id", "sys_status"),
    [
        pytest.param(_JOB_ID, WorkMode.MODE_READY.value, id="job over"),
        pytest.param(0, WorkMode.MODE_WORKING.value, id="ad-hoc job"),
        pytest.param(
            17990000000000001, WorkMode.MODE_WORKING.value, id="task not stored"
        ),
    ],
)
def test_no_plan_is_running(hass: HomeAssistant, job_id: int, sys_status: int) -> None:
    """Outside a job, or for a job no stored task started, nothing is running."""
    assert _coordinator(hass, job_id=job_id, sys_status=sys_status).running_plan is None


async def test_the_running_task_gets_a_sensor_that_goes_when_the_job_ends(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Like the zone sensors: added for the running task, removed afterwards."""
    coordinator = _coordinator(hass)
    entities: dict[str, Any] = {}
    add = MagicMock()

    async_sync_running_task_entity(coordinator, entities, add)
    async_sync_running_task_entity(coordinator, entities, add)

    add.assert_called_once()
    (entity,) = add.call_args.args[0]
    assert entity.translation_placeholders == {"name": "Top half of bottom lawn"}
    assert entity.native_value == "mowing"
    coordinator.data.report_data.dev.sys_status = WorkMode.MODE_PAUSE.value
    assert entity.native_value == "paused"

    entity_registry.async_get_or_create(
        "sensor",
        "mammotion",
        f"Luba-VS563L6H_{_PLAN_ID}_running_task",
    )
    coordinator.data.report_data.dev.sys_status = WorkMode.MODE_CHARGING.value
    async_sync_running_task_entity(coordinator, entities, add)

    assert entities == {}
    assert (
        entity_registry.async_get_entity_id(
            "sensor", "mammotion", f"Luba-VS563L6H_{_PLAN_ID}_running_task"
        )
        is None
    )


async def test_the_running_task_sensor_follows_a_rename(hass: HomeAssistant) -> None:
    """A task renamed mid-job renames its sensor instead of adding another."""
    coordinator = _coordinator(hass, name="Bottom lawn")
    entities: dict[str, Any] = {}
    add = MagicMock()
    async_sync_running_task_entity(coordinator, entities, add)

    coordinator.data.map.plan[_PLAN_ID] = Plan(
        plan_id=_PLAN_ID, task_name="Top half of bottom lawn"
    )
    async_sync_running_task_entity(coordinator, entities, add)

    add.assert_called_once()
    assert entities[_PLAN_ID].translation_placeholders == {
        "name": "Top half of bottom lawn"
    }


@pytest.mark.parametrize(
    "path",
    [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))],
    ids=lambda path: path.name,
)
def test_every_locale_translates_the_running_task_sensor(path: Path) -> None:
    """The name carries the task's name; every phase is translated."""
    entry = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"][
        "running_task"
    ]
    assert "{name}" in entry["name"]
    assert set(entry["state"]) == set(RUNNING_TASK_STATES.values())


def test_the_task_button_says_whether_it_is_running(hass: HomeAssistant) -> None:
    """Each task button carries ``running``; only the running task's is true."""
    from custom_components.mammotion.button import (  # noqa: PLC0415
        MammotionTaskButtonSensorEntity,
        MammotionTaskButtonSensorEntityDescription,
    )

    coordinator = _coordinator(hass)

    def _button(plan_id: str) -> MammotionTaskButtonSensorEntity:
        return MammotionTaskButtonSensorEntity(
            coordinator,
            MammotionTaskButtonSensorEntityDescription(
                key=plan_id, plan_id=plan_id, press_fn=MagicMock()
            ),
        )

    assert _button(_PLAN_ID).extra_state_attributes["running"] is True
    assert _button(_OTHER_PLAN).extra_state_attributes["running"] is False


def _syncing_coordinator(hass: HomeAssistant, **kwargs: Any) -> Any:
    coordinator = _coordinator(hass, **kwargs)
    coordinator.config_entry = MagicMock()
    coordinator.async_sync_tasks = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.manager.mower.return_value.has_queued_commands.return_value = False
    return coordinator


def test_an_unknown_running_job_syncs_the_tasks_once(hass: HomeAssistant) -> None:
    """A task made in the app since the last fetch: fetch once for that job, not per report."""
    coordinator = _syncing_coordinator(hass, job_id=17990000000000001)

    coordinator._async_sync_tasks_for_unknown_job()
    # pymammotion records the job the completed fetch ran during.
    coordinator.data.map.plans_fetched_job_id = 17990000000000001
    coordinator._async_sync_tasks_for_unknown_job()

    create = coordinator.config_entry.async_create_background_task
    create.assert_called_once()
    assert create.call_args.args[1] is coordinator.async_sync_tasks.return_value


def test_a_new_unknown_job_syncs_again(hass: HomeAssistant) -> None:
    """Each unknown job id gets its own fetch."""
    coordinator = _syncing_coordinator(hass, job_id=17990000000000001)
    coordinator._async_sync_tasks_for_unknown_job()
    coordinator.data.map.plans_fetched_job_id = 17990000000000001

    coordinator.data.work.job_id = 17990000000000002
    coordinator._async_sync_tasks_for_unknown_job()

    assert coordinator.config_entry.async_create_background_task.call_count == 2


@pytest.mark.parametrize(
    ("job_id", "sys_status"),
    [
        pytest.param(_JOB_ID, WorkMode.MODE_WORKING.value, id="task known"),
        pytest.param(17990000000000001, WorkMode.MODE_READY.value, id="not in a job"),
        pytest.param(0, WorkMode.MODE_WORKING.value, id="ad-hoc job"),
    ],
)
def test_no_task_sync_is_needed(
    hass: HomeAssistant, job_id: int, sys_status: int
) -> None:
    """Only a running job no stored task accounts for needs a fetch."""
    coordinator = _syncing_coordinator(hass, job_id=job_id, sys_status=sys_status)

    coordinator._async_sync_tasks_for_unknown_job()

    coordinator.config_entry.async_create_background_task.assert_not_called()


async def test_the_sync_button_keeps_its_entity_after_the_rename(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The "sync schedules" button became "sync tasks"; its registry entry moves."""
    from custom_components.mammotion.button import (  # noqa: PLC0415
        _async_migrate_task_sync_unique_id,
    )

    coordinator = _coordinator(hass)
    old = entity_registry.async_get_or_create(
        "button", "mammotion", "Luba-VS563L6H_start_schedule_sync"
    )

    _async_migrate_task_sync_unique_id(hass, coordinator)

    moved = entity_registry.async_get(old.entity_id)
    assert moved is not None
    assert moved.unique_id == "Luba-VS563L6H_start_task_sync"


async def test_the_task_sensor_appears_once_the_fetched_plan_lands(
    hass: HomeAssistant,
) -> None:
    """The unknown job's plan arriving from the task sync brings its sensor up."""
    coordinator = _coordinator(hass)
    coordinator.data.map.plan.pop(_PLAN_ID)
    entities: dict[str, Any] = {}
    add = MagicMock()
    async_sync_running_task_entity(coordinator, entities, add)
    add.assert_not_called()

    coordinator.data.map.plan[_PLAN_ID] = Plan(
        plan_id=_PLAN_ID, task_name="Top half of bottom lawn"
    )
    async_sync_running_task_entity(coordinator, entities, add)

    add.assert_called_once()
    assert _PLAN_ID in entities


def _querying_coordinator(hass: HomeAssistant, **kwargs: Any) -> Any:
    from unittest.mock import AsyncMock  # noqa: PLC0415

    from homeassistant.helpers.debounce import Debouncer  # noqa: PLC0415

    from custom_components.mammotion.const import LOGGER  # noqa: PLC0415

    coordinator = _syncing_coordinator(hass, **kwargs)
    coordinator.async_send_command = AsyncMock()
    coordinator._job_id_query_debouncer = Debouncer(
        hass,
        LOGGER,
        cooldown=60,
        immediate=True,
        function=coordinator._async_query_job_id,
        background=True,
    )
    # The report of a mow in progress: a route, as in the real run.
    coordinator.data.report_data.work.ub_path_hash = 2925952933437268886
    return coordinator


async def test_a_job_without_route_settings_is_queried(hass: HomeAssistant) -> None:
    """After a restart mid-job ``work`` is empty; ask for the route, at most once a minute."""
    coordinator = _querying_coordinator(hass, job_id=0)

    coordinator._async_request_missing_job_id()
    coordinator._async_request_missing_job_id()
    await hass.async_block_till_done()

    coordinator.async_send_command.assert_awaited_once_with(
        "query_generate_route_information"
    )
    coordinator._job_id_query_debouncer.async_shutdown()


async def test_the_reply_filling_work_ends_the_query(hass: HomeAssistant) -> None:
    """The data is the state: once the route settings arrive nothing is asked."""
    coordinator = _querying_coordinator(hass, job_id=0)
    coordinator.data.work.job_id = _JOB_ID

    coordinator._async_request_missing_job_id()
    await hass.async_block_till_done()

    coordinator.async_send_command.assert_not_awaited()


@pytest.mark.parametrize(
    ("job_id", "sys_status", "ub_path_hash", "zones"),
    [
        pytest.param(_JOB_ID, WorkMode.MODE_WORKING.value, 1, [], id="job id known"),
        pytest.param(0, WorkMode.MODE_READY.value, 1, [], id="no job"),
        pytest.param(0, WorkMode.MODE_RETURNING.value, 0, [], id="returning, no route"),
        pytest.param(0, WorkMode.MODE_WORKING.value, 1, [905459], id="zones known"),
    ],
)
async def test_no_job_id_query_is_needed(
    hass: HomeAssistant,
    job_id: int,
    sys_status: int,
    ub_path_hash: int,
    zones: list[int],
) -> None:
    """Only an empty ``work`` during a mow the report confirms is queried."""
    coordinator = _querying_coordinator(hass, job_id=job_id, sys_status=sys_status)
    coordinator.data.report_data.work.ub_path_hash = ub_path_hash
    coordinator.data.report_data.work.path_hash = 0
    coordinator.data.work.zone_hashs = zones

    coordinator._async_request_missing_job_id()
    await hass.async_block_till_done()

    coordinator.async_send_command.assert_not_awaited()


@pytest.mark.parametrize(
    ("fetched", "stale", "syncs"),
    [(False, False, True), (True, True, True), (True, False, False)],
    ids=["never fetched", "stale", "fetched"],
)
async def test_start_up_fetches_tasks_only_when_needed(
    hass: HomeAssistant, fetched: bool, stale: bool, syncs: bool
) -> None:
    """A restored empty task list is refilled at start-up; a fetched one is left alone."""
    from unittest.mock import AsyncMock  # noqa: PLC0415

    coordinator = _coordinator(hass)
    coordinator.data.map.plans_fetched = fetched
    coordinator.data.map.plans_stale = stale
    coordinator.async_sync_tasks = AsyncMock()

    await coordinator.async_sync_tasks_if_unfetched()

    assert coordinator.async_sync_tasks.await_count == (1 if syncs else 0)


def test_the_unknown_job_sync_waits_for_a_running_saga(hass: HomeAssistant) -> None:
    """With the start-up fetch still running, look again later instead of fetching twice."""
    coordinator = _syncing_coordinator(hass, job_id=17990000000000001)
    queued = coordinator.manager.mower.return_value.has_queued_commands
    queued.return_value = True

    coordinator._async_sync_tasks_for_unknown_job()
    coordinator.config_entry.async_create_background_task.assert_not_called()

    queued.return_value = False
    coordinator._async_sync_tasks_for_unknown_job()
    coordinator.config_entry.async_create_background_task.assert_called_once()
