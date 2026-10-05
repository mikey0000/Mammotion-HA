"""Resuming a paused job waits for the mower to report it is working again.

The report snapshot requested after the action is what refreshes the mode
sensor; taken before the mower leaves PAUSE, it pins PAUSE until the next report.
"""

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion import lawn_mower as lawn_mower_platform
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.lawn_mower import MammotionLawnMowerEntity
from tests_ha.job_service_support import make_mower_entity


def _record_snapshot_modes(entity: MammotionLawnMowerEntity) -> list[WorkMode]:
    """Return the modes the mower held each time a report snapshot was requested."""
    modes: list[WorkMode] = []

    async def snapshot() -> None:
        modes.append(entity.coordinator.data.report_data.dev.sys_status)

    entity.coordinator.async_request_report_snapshot.side_effect = snapshot
    return modes


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


async def test_a_slow_resume_is_not_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resume went out; a mower slow to report WORKING is not a failed action."""
    monkeypatch.setattr(lawn_mower_platform, "_MODE_WAIT_TIMEOUT", 0)
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)
    snapshot_modes = _record_snapshot_modes(entity)

    await entity.async_start_mowing()

    assert entity.coordinator.sent == [
        "resume_execute_task",
        "query_generate_route_information",
    ]
    assert snapshot_modes == [WorkMode.MODE_PAUSE]


async def test_a_resume_wait_that_fails_otherwise_is_reported() -> None:
    """Only running out of time is stepped over; a lost link still fails the action."""
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)
    entity.coordinator.async_wait_for.side_effect = HomeAssistantError(
        translation_domain=DOMAIN, translation_key="resume_failed"
    )

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_start_mowing()

    assert raised.value.translation_key == "resume_failed"
    assert (
        entity.coordinator.async_wait_for.await_args.kwargs["failure_key"]
        == "resume_failed"
    )
    entity.coordinator.async_request_report_snapshot.assert_awaited_once()
