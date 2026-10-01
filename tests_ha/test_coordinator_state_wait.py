"""The report coordinator's state waits: ``async_wait_for`` and ``async_ensure_fresh_state``.

Both sit in front of a user action.  A mode wait that runs out fails the action
with the step's translated key; a fresh-state read that runs out does not, the
action going ahead on the snapshot already held.
"""

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceStateTimeoutError
from pymammotion.transport.base import NoTransportAvailableError
from pymammotion.utility.constant import WorkMode
from user_command_support import make_cloud_report_coordinator

from custom_components.mammotion import coordinator as coordinator_module
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_DEVICE_NAME = "Luba-VAME9R5S"
#: Bound on a wait the code under test must end by itself.
_WAIT_BOUND_S = 5


def _coordinator() -> MammotionReportUpdateCoordinator:
    return make_cloud_report_coordinator(_DEVICE_NAME)


def _is_paused(device: MowingDevice) -> bool:
    return device.report_data.dev.sys_status == WorkMode.MODE_PAUSE


async def test_wait_for_returns_the_state_the_library_reached() -> None:
    """The device name and deadline go to the library, its state comes back."""
    coordinator = _coordinator()
    reached = MowingDevice()
    coordinator.manager.wait_for.return_value = reached

    result = await coordinator.async_wait_for(_is_paused, timeout=60)

    assert result is reached
    coordinator.manager.wait_for.assert_awaited_once_with(
        _DEVICE_NAME, _is_paused, timeout=60
    )


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(DeviceStateTimeoutError(_DEVICE_NAME, 60), id="timeout"),
        pytest.param(NoTransportAvailableError("no transport"), id="no_transport"),
    ],
)
async def test_wait_for_fails_with_the_steps_translation_key(exc: Exception) -> None:
    """A mower that never reports the mode fails the action, not a raw TimeoutError."""
    coordinator = _coordinator()
    coordinator.manager.wait_for.side_effect = exc

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_wait_for(_is_paused, timeout=60, failure_key="start_failed")

    assert raised.value.translation_key == "start_failed"
    assert raised.value.__cause__ is exc


async def test_a_waiting_fresh_state_read_asks_the_library_to_wait() -> None:
    """A user action reads state after the report lands, not before."""
    coordinator = _coordinator()

    await coordinator.async_ensure_fresh_state(wait=True)

    coordinator.manager.ensure_fresh_state.assert_awaited_once_with(
        _DEVICE_NAME, max_age_s=120.0, wait=True
    )


async def test_a_fresh_state_read_the_mower_never_answers_lets_the_action_proceed() -> None:
    """A silent mower leaves the current snapshot to act on: the read returns, not raises."""
    coordinator = _coordinator()
    coordinator.manager.ensure_fresh_state.side_effect = DeviceStateTimeoutError(
        _DEVICE_NAME
    )

    await coordinator.async_ensure_fresh_state(wait=True)


async def test_a_fresh_state_read_is_bounded_by_the_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A library call that never returns cannot hold the action past the bound."""
    monkeypatch.setattr(coordinator_module, "FRESH_STATE_TIMEOUT", 0)
    coordinator = _coordinator()
    never = asyncio.Event()

    async def _hang(*_: object, **__: object) -> None:
        await never.wait()

    coordinator.manager.ensure_fresh_state.side_effect = _hang

    await asyncio.wait_for(coordinator.async_ensure_fresh_state(wait=True), _WAIT_BOUND_S)


async def test_a_fresh_state_read_with_no_transport_fails_translated() -> None:
    """Nothing can carry the read, so nothing can carry the command either."""
    coordinator = _coordinator()
    coordinator.manager.ensure_fresh_state.side_effect = NoTransportAvailableError(
        "no transport"
    )

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_ensure_fresh_state(wait=True)

    assert raised.value.translation_key == "command_failed"


async def test_a_background_fresh_state_read_does_not_wait() -> None:
    """Without wait the library's fire-and-forget snapshot is used, as before."""
    coordinator = _coordinator()

    await coordinator.async_ensure_fresh_state()

    coordinator.manager.ensure_fresh_state.assert_awaited_once_with(
        _DEVICE_NAME, max_age_s=120.0
    )
