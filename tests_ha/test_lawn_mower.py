"""The lawn mower entity: failure keys, the retired job field and resuming a job.

A failed action says which action it was. The coordinator maps every send
failure to a HomeAssistantError, so the entity names its action to the
coordinator, which keeps the one mapping.

The job services still accept the retired ``rain_tactics`` field: ``start_mow``
and ``modify_running_job`` drop it before it reaches the route settings or the
coordinator, and raise a repair naming the action.

Resuming a paused job waits for the mower to report WORKING before the report
snapshot that refreshes the mode sensor; taken while the mower is still in
PAUSE, it pinned PAUSE until the next report.
"""

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from job_service_support import make_mower_entity, start_mow
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.messaging.command_queue import Priority
from pymammotion.transport.base import CommandTimeoutError, TransportRateLimitedError
from pymammotion.utility.constant.device_constant import WorkMode
from user_command_support import make_coordinator

from custom_components.mammotion import lawn_mower as lawn_mower_platform
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.lawn_mower import (
    MODIFY_RUNNING_JOB_SCHEMA,
    START_MOW_SCHEMA,
    MammotionLawnMowerEntity,
)

_MOWER = "Luba-VS123456"


def _entity(
    mode: WorkMode, exc: Exception, *, charge_state: int = 0
) -> MammotionLawnMowerEntity:
    """Return the lawn mower over a mower in *mode* whose every send fails with *exc*."""
    device = MowingDevice()
    device.report_data.dev.sys_status = mode
    device.report_data.dev.charge_state = charge_state
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )
    coordinator.unique_name = _MOWER
    coordinator._operation_settings = OperationSettings()  # noqa: SLF001
    coordinator.manager.send_command_with_args.side_effect = exc
    coordinator.manager.send_command_and_wait.side_effect = exc
    return MammotionLawnMowerEntity(coordinator)


def _offline() -> DeviceOfflineException:
    return DeviceOfflineException(29003, "iot-1")


async def _dock(entity: MammotionLawnMowerEntity) -> None:
    await entity.async_dock()


async def _pause(entity: MammotionLawnMowerEntity) -> None:
    await entity.async_pause()


async def _start(entity: MammotionLawnMowerEntity) -> None:
    await entity.async_start_mowing()


def _record_snapshot_modes(entity: MammotionLawnMowerEntity) -> list[WorkMode]:
    """Return the modes the mower held each time a report snapshot was requested."""
    modes: list[WorkMode] = []

    async def snapshot() -> None:
        modes.append(entity.coordinator.data.report_data.dev.sys_status)

    entity.coordinator.async_request_report_snapshot.side_effect = snapshot
    return modes


@pytest.mark.regression
@pytest.mark.parametrize(
    ("action", "mode", "key"),
    [
        (_dock, WorkMode.MODE_READY, "dock_failed"),
        (_dock, WorkMode.MODE_RETURNING, "dock_cancel_failed"),
        (_dock, WorkMode.MODE_WORKING, "pause_failed"),
        (_pause, WorkMode.MODE_WORKING, "pause_failed"),
        (_pause, WorkMode.MODE_RETURNING, "dock_cancel_failed"),
        (_start, WorkMode.MODE_READY, "start_failed"),
        (_start, WorkMode.MODE_RETURNING, "dock_cancel_failed"),
    ],
    ids=[
        "dock",
        "dock while returning",
        "dock while mowing",
        "pause",
        "pause while returning",
        "start",
        "start while returning",
    ],
)
async def test_a_failed_action_names_the_action(
    action: Any, mode: WorkMode, key: str
) -> None:
    """Every failure surfaced as ``command_failed`` once the coordinator mapped it first."""
    entity = _entity(mode, _offline())

    with pytest.raises(HomeAssistantError) as raised:
        await action(entity)

    assert raised.value.translation_key == key


@pytest.mark.parametrize(
    ("exc", "key"),
    [
        (TransportRateLimitedError("banned for 12 h"), "api_limit_exceeded"),
        (CommandTimeoutError("todev_taskctrl_ack", 3), "command_unconfirmed"),
    ],
    ids=["rate_limited", "unconfirmed"],
)
async def test_the_rate_limit_and_unconfirmed_messages_are_kept(
    exc: Exception, key: str
) -> None:
    """Those say something the action name would hide, so they are not replaced."""
    entity = _entity(WorkMode.MODE_RETURNING, exc)

    with pytest.raises(HomeAssistantError) as raised:
        await _start(entity)

    assert raised.value.translation_key == key


@pytest.mark.regression
async def test_start_mow_still_accepts_rain_tactics_and_ignores_it(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Dropping the field from the schema broke every automation still sending it.

    The call must plan the job with the other fields and raise a repair naming
    the action.
    """
    entity = make_mower_entity(OperationSettings(), hass=hass)

    planned = await start_mow(entity, rain_tactics="1", speed=0.6)

    assert planned.speed == 0.6
    issue = issue_registry.async_get_issue(DOMAIN, "deprecated_rain_tactics_start_mow")
    assert issue is not None
    assert issue.translation_key == "deprecated_rain_tactics"
    assert issue.translation_placeholders == {
        "service": "mammotion.start_mow",
        "field": "rain_tactics",
    }


@pytest.mark.regression
async def test_modify_running_job_still_accepts_rain_tactics_and_ignores_it(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """The same break on the in-job editor; the coordinator gets only the live fields."""
    entity = make_mower_entity(OperationSettings(), hass=hass)

    await entity.async_modify_running_job(
        **vol.Schema(MODIFY_RUNNING_JOB_SCHEMA)({"rain_tactics": 0, "speed": 0.8})
    )

    entity.coordinator.async_modify_running_job.assert_awaited_once_with(
        priority=Priority.USER, speed=0.8
    )
    assert issue_registry.async_get_issue(
        DOMAIN, "deprecated_rain_tactics_modify_running_job"
    )


async def test_start_mow_without_rain_tactics_raises_no_repair(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Only callers still sending the field are asked to change."""
    await start_mow(make_mower_entity(OperationSettings(), hass=hass), speed=0.6)

    assert issue_registry.issues == {}


async def test_modify_running_job_without_rain_tactics_raises_no_repair(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Only callers still sending the field are asked to change."""
    entity = make_mower_entity(OperationSettings(), hass=hass)

    await entity.async_modify_running_job(speed=0.8)

    assert issue_registry.issues == {}


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(START_MOW_SCHEMA, id="start"),
        pytest.param(MODIFY_RUNNING_JOB_SCHEMA, id="modify"),
    ],
)
@pytest.mark.parametrize("value", [2, "x"])
def test_the_retired_field_still_only_takes_0_or_1(
    schema: dict[Any, Any], value: object
) -> None:
    """Accepting it again must not widen what the old schema allowed."""
    with pytest.raises(vol.Invalid, match="rain_tactics"):
        vol.Schema(schema)({"rain_tactics": value})


@pytest.mark.regression
async def test_resume_requests_the_snapshot_once_the_mower_is_working() -> None:
    """The snapshot went out right after resume_execute_task, still in PAUSE.

    The mode sensor then showed MODE_PAUSE until the next report although the
    mower was mowing again.
    """
    entity = make_mower_entity(
        OperationSettings(),
        mode=WorkMode.MODE_PAUSE,
        bp_info=1,
        responses={"resume_execute_task": WorkMode.MODE_WORKING},
    )
    snapshot_modes = _record_snapshot_modes(entity)

    await entity.async_start_mowing()

    assert snapshot_modes == [WorkMode.MODE_WORKING]


async def test_a_resume_the_mower_never_reports_fails_with_resume_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mower that never reports WORKING fails the action; the snapshot still goes out."""
    monkeypatch.setattr(lawn_mower_platform, "_MODE_WAIT_TIMEOUT", 0)
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)
    snapshot_modes = _record_snapshot_modes(entity)

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_start_mowing()

    assert raised.value.translation_key == "resume_failed"
    assert entity.coordinator.sent == [
        "resume_execute_task",
        "query_generate_route_information",
    ]
    assert snapshot_modes == [WorkMode.MODE_PAUSE]
