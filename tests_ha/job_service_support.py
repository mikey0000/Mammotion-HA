"""Shared scaffolding for the job-service tests: the real lawn mower entity over a scripted mower."""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import voluptuous as vol
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.lawn_mower import (
    START_MOW_SCHEMA,
    MammotionLawnMowerEntity,
)


class ScriptedMower:
    """The mower behind the fake coordinator: its state, its replies, what it was sent.

    A command named in *responses* moves the mower to that mode one loop turn
    after it is sent, the way a pushed report does; that report clears the
    breakpoint after cancel_job.  ``wait_for`` models the coordinator's
    ``async_wait_for``: the predicate is checked now and on every report, and
    running out of time raises the HomeAssistantError named by ``failure_key``.
    A command named in *failures* (``plan_route`` for the route planner) raises the
    coordinator's HomeAssistantError with that translation key once sent.
    """

    def __init__(
        self,
        device: MowingDevice,
        responses: dict[str, WorkMode],
        failures: dict[str, str],
    ) -> None:
        """Script the mower: the modes it moves to and the commands that fail."""
        self.device = device
        self.responses = responses
        self.failures = failures
        self.sent: list[str] = []
        self._waiters: list[
            tuple[Callable[[MowingDevice], bool], asyncio.Future[None]]
        ] = []

    def _push(self, new_mode: WorkMode, job_ended: bool) -> None:
        self.device.report_data.dev.sys_status = new_mode
        if job_ended:
            self.device.report_data.work.bp_info = 0
        for predicate, reached in self._waiters:
            if not reached.done() and predicate(self.device):
                reached.set_result(None)

    async def wait_for(
        self,
        predicate: Callable[[MowingDevice], bool],
        *,
        timeout: float,
        failure_key: str = "command_failed",
    ) -> MowingDevice:
        """Wait for *predicate* to hold, as the coordinator's ``async_wait_for`` does."""
        if predicate(self.device):
            return self.device
        waiter = (predicate, asyncio.get_running_loop().create_future())
        self._waiters.append(waiter)
        try:
            async with asyncio.timeout(timeout):
                await waiter[1]
        except TimeoutError as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=failure_key
            ) from exc
        finally:
            self._waiters.remove(waiter)
        return self.device

    def _fail_if_configured(self, command: str) -> None:
        if (key := self.failures.get(command)) is not None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key=key)

    async def send(self, command: str, *_: Any, **__: Any) -> None:
        """Record *command*, then fail or move the mower as scripted."""
        self.sent.append(command)
        self._fail_if_configured(command)
        if (new_mode := self.responses.get(command)) is not None:
            asyncio.get_running_loop().call_soon(
                self._push, new_mode, command == "cancel_job"
            )

    async def plan_route(self, *_: Any, **__: Any) -> bool:
        """Record the route plan with the mode it was planned in."""
        self.sent.append(f"plan_route in {self.device.report_data.dev.sys_status.name}")
        self._fail_if_configured("plan_route")
        return True


def make_mower_entity(
    settings: OperationSettings,
    mode: WorkMode = WorkMode.MODE_READY,
    bp_info: int = 0,
    responses: dict[str, WorkMode] | None = None,
    failures: dict[str, str] | None = None,
    *,
    hass: HomeAssistant | None = None,
) -> MammotionLawnMowerEntity:
    """Build the real lawn mower entity over a ``ScriptedMower`` in *mode*.

    Everything sent lands in ``coordinator.sent``.  Without *hass* the entity gets a
    stand-in that only resolves the two area switches.
    """
    device = MowingDevice()
    device.report_data.dev.sys_status = mode
    device.report_data.dev.charge_state = 1
    device.report_data.work.bp_info = bp_info
    mower = ScriptedMower(device, responses or {}, failures or {})
    coordinator = MagicMock(spec=MammotionReportUpdateCoordinator)
    coordinator.unique_name = "Luba-VA123456"
    coordinator.device_name = "Luba-VA123456"
    coordinator.data = device
    coordinator.operation_settings = settings
    coordinator.sent = mower.sent
    coordinator.async_wait_for = AsyncMock(side_effect=mower.wait_for)
    coordinator.async_ensure_fresh_state = AsyncMock()
    coordinator.async_request_report_snapshot = AsyncMock()
    coordinator.async_plan_route = AsyncMock(side_effect=mower.plan_route)
    coordinator.async_modify_plan_route = AsyncMock(return_value=True)
    coordinator.async_send_command = AsyncMock(side_effect=mower.send)
    coordinator.async_send_and_wait = AsyncMock(side_effect=mower.send)
    coordinator.async_modify_running_job = AsyncMock(return_value=True)
    entity = MammotionLawnMowerEntity(coordinator)
    states = {
        "switch.area_front": State("switch.area_front", "on", {"hash": "111"}),
        "switch.area_back": State("switch.area_back", "on", {"hash": "222"}),
    }
    entity.hass = hass or SimpleNamespace(states=SimpleNamespace(get=states.get))
    return entity


async def start_mow(entity: MammotionLawnMowerEntity, **data: Any) -> OperationSettings:
    """Run start_mow through its own schema and return the settings it planned with."""
    await call_start_mow(entity, **data)
    return entity.coordinator.async_plan_route.await_args.args[0]


async def call_start_mow(entity: MammotionLawnMowerEntity, **data: Any) -> None:
    """Run start_mow through its own schema, as HA's service layer does."""
    await entity.async_start_mowing(**vol.Schema(START_MOW_SCHEMA)(data))
