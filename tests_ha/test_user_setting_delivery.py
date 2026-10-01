"""A setting a person changes is a user action: a change that did not land raises.

Sent at the default priority these went through the library's queue, which
absorbs a failure, so an offline mower dropped the change while the action
reported success and the switch kept the state it had shown optimistically.
The coordinator is the shipped class around a spec'd client whose send is
rejected the way the cloud rejects a send to an offline mower.
"""

from typing import Any

import pytest
from battery_support import make_mower, platform_entities
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.data.model.device_info import ChargeSettings
from user_command_support import make_coordinator

from custom_components.mammotion import select as select_platform
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.lawn_mower import MammotionLawnMowerEntity
from custom_components.mammotion.number import (
    CHARGE_LIMIT_NUMBER_ENTITY,
    LUBA_WORKING_ENTITIES,
    NUMBER_ENTITIES,
    NUMBER_WORKING_ENTITIES,
    MammotionConfigNumberEntity,
)
from custom_components.mammotion.switch import (
    CHARGE_SWITCH_ENTITIES,
    MammotionSwitchEntity,
)

_LUBA_PRO = "Luba-VS123456"


def _offline_coordinator(device: MowingDevice) -> MammotionReportUpdateCoordinator:
    """Return the coordinator over *device*, with every send rejected as offline."""
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_LUBA_PRO
    )
    coordinator.unique_name = _LUBA_PRO
    rejected = DeviceOfflineException(29003, "iot-1")
    coordinator.manager.send_command_and_wait.side_effect = rejected
    coordinator.manager.send_command_with_args.side_effect = rejected
    return coordinator


def _charge_reported() -> MowingDevice:
    device = MowingDevice()
    device.mower_state.charge_settings = ChargeSettings(
        smart_charge=False,
        charge_limit=85,
        peak_valley_charge=True,
        valley_charge_start_time=1320,
        valley_charge_end_time=360,
    )
    device.mower_state.recharge_level = 20
    device.mower_state.resume_level = 80
    return device


def _without_state_machine(entity: Any, hass: HomeAssistant) -> Any:
    entity.hass = hass
    entity.entity_id = f"test.{entity.entity_description.key}"
    # Not added to a platform, so there is no state machine entry to write.
    entity.async_write_ha_state = lambda: None
    return entity


@pytest.mark.regression
async def test_a_smart_charge_change_the_mower_never_got_reverts_the_switch(
    hass: HomeAssistant,
) -> None:
    """The write went out at NORMAL, so the offline rejection was swallowed.

    The switch had already shown the new state and only reverts on an exception,
    so it kept showing smart charging on although the mower never changed.
    """
    coordinator = _offline_coordinator(_charge_reported())
    description = next(d for d in CHARGE_SWITCH_ENTITIES if d.key == "smart_charge")
    switch = _without_state_machine(
        MammotionSwitchEntity(coordinator, description), hass
    )

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()

    assert switch.is_on is False


@pytest.mark.regression
async def test_a_charge_limit_the_mower_never_got_is_reported(
    hass: HomeAssistant,
) -> None:
    """The number reported success and showed a limit the mower never took."""
    coordinator = _offline_coordinator(_charge_reported())
    number = _without_state_machine(
        MammotionConfigNumberEntity(coordinator, CHARGE_LIMIT_NUMBER_ENTITY), hass
    )

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(90)

    assert number.native_value == 85


@pytest.mark.regression
@pytest.mark.parametrize("key", ["smart_recharge_level", "smart_resume_level"], ids=str)
async def test_a_charge_level_change_the_mower_never_got_is_reported(
    hass: HomeAssistant, key: str
) -> None:
    """The recharge and resume levels went out at NORMAL too."""
    coordinator = _offline_coordinator(_charge_reported())
    description = next(d for d in CHARGE_SWITCH_ENTITIES if d.key == key)
    switch = _without_state_machine(
        MammotionSwitchEntity(coordinator, description), hass
    )

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()

    assert switch.is_on is False


def _running_job_coordinator() -> MammotionReportUpdateCoordinator:
    """Return the offline coordinator over a Luba Pro that is running a route job."""
    device = MowingDevice()
    device.work.zone_hashs = [123]
    device.report_data.work.bp_hash = "123"
    device.report_data.work.area = 5
    coordinator = _offline_coordinator(device)
    coordinator._operation_settings = OperationSettings()  # noqa: SLF001
    return coordinator


def _number(key: str) -> Any:
    return next(
        d
        for d in (*NUMBER_ENTITIES, *NUMBER_WORKING_ENTITIES, *LUBA_WORKING_ENTITIES)
        if d.key == key
    )


@pytest.mark.regression
@pytest.mark.parametrize(
    ("key", "value"),
    [("working_speed", 0.5), ("start_progress", 40), ("blade_height", 50)],
    ids=["speed", "progress", "blade_height"],
)
async def test_a_mid_job_change_the_mower_never_got_is_reported(
    key: str, value: float
) -> None:
    """The running job's route went out at NORMAL, so the queue absorbed the rejection."""
    coordinator = _running_job_coordinator()
    description = _number(key)
    description.set_fn(coordinator, value)

    with pytest.raises(HomeAssistantError):
        await description.set_async_fn(coordinator, value)


@pytest.mark.regression
async def test_a_mid_job_bypass_change_the_mower_never_got_is_reported() -> None:
    """The obstacle-detection select re-issued the route at NORMAL as well."""
    coordinator = _running_job_coordinator()
    entities = await platform_entities(select_platform, make_mower(_LUBA_PRO))
    description = entities["bypass_mode"].entity_description

    with pytest.raises(HomeAssistantError):
        await description.async_set_fn(coordinator)


@pytest.mark.regression
async def test_a_modify_running_job_the_mower_never_got_is_reported() -> None:
    """The ``modify_running_job`` action reported success for a dropped change."""
    entity = MammotionLawnMowerEntity(_running_job_coordinator())

    with pytest.raises(HomeAssistantError):
        await entity.async_modify_running_job(speed=0.5)


@pytest.mark.regression
async def test_a_route_plan_that_was_not_delivered_is_not_reported_as_planned() -> None:
    """``async_plan_route`` returned True whatever the send did."""
    coordinator = _offline_coordinator(MowingDevice())

    assert not await coordinator.async_plan_route(OperationSettings())
