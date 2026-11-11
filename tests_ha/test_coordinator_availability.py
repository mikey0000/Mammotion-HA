"""Entities must not show stale cached data as "available" while a connect attempt is failing.

``is_online()`` (``handle.has_usable_transport``) answers "is there a transport
permitted to attempt a send right now" — true for the entire multi-second window an
attempt is in flight, whether or not it ever succeeds. Deployed against a real
instance, a mower whose BLE link tried and failed every ~5-10 minute cycle (out of
proxy connection slots / no response) left every entity "available" with stale
cached data (an error code logged days earlier) for the whole failed-attempt window,
flipping back to unavailable only once the attempt gave up — a clean
``unavailable -> stale value -> unavailable`` cycle on every single attempt,
regardless of whether the mower was ever actually reachable. That repeatedly
re-triggered a user's alerting automation on the same stale fault.

``is_reachable()`` answers the right question instead, backed by pymammotion's own
``DeviceAvailability.is_available``: BLE counts only once actually connected
(``TransportAvailability.CONNECTED``, not ``CONNECTING``); cloud MQTT counts only
once connected *and* the cloud hasn't reported the device offline.
"""

from unittest.mock import create_autospec

import pytest
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.state.device_state import DeviceAvailability
from pymammotion.transport.base import TransportAvailability
from user_command_support import make_cloud_report_coordinator

from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_DEVICE_NAME = "Luba-VS1000001"


def _coordinator[CoordinatorT: MammotionBaseUpdateCoordinator](
    coordinator_cls: type[CoordinatorT] = MammotionReportUpdateCoordinator,
) -> CoordinatorT:
    """Build a real coordinator over a bare handle whose ``availability`` is under test control."""
    coordinator = coordinator_cls.__new__(coordinator_cls)
    device = MowingDevice()
    coordinator.data = device
    coordinator.device = device
    coordinator.device_name = _DEVICE_NAME
    coordinator.manager = create_autospec(MammotionClient, instance=True)
    coordinator.manager.get_device_by_name.return_value = device
    handle = DeviceHandle.__new__(DeviceHandle)
    handle._availability = DeviceAvailability()
    coordinator.manager.mower.return_value = handle
    return coordinator


def _set_ble(
    coordinator: MammotionBaseUpdateCoordinator, state: TransportAvailability
) -> None:
    coordinator.manager.mower.return_value.availability.ble = state


@pytest.mark.regression
def test_a_connection_attempt_that_never_confirms_is_not_reachable() -> None:
    """The exact real-world bug: a failed attempt must never read as reachable.

    BLE's own state machine goes DISCONNECTED -> CONNECTING (the attempt is in
    flight, ``is_online()`` would already say True here) -> DISCONNECTED (it gave
    up). ``is_reachable()`` must stay False throughout — only CONNECTED counts.
    """
    coordinator = _coordinator()

    _set_ble(coordinator, TransportAvailability.DISCONNECTED)
    assert coordinator.is_reachable() is False

    _set_ble(coordinator, TransportAvailability.CONNECTING)
    assert coordinator.is_reachable() is False

    _set_ble(coordinator, TransportAvailability.DISCONNECTED)
    assert coordinator.is_reachable() is False


def test_an_actually_connected_ble_link_is_reachable() -> None:
    """Once BLE genuinely connects, is_reachable() reflects it."""
    coordinator = _coordinator()

    _set_ble(coordinator, TransportAvailability.CONNECTED)

    assert coordinator.is_reachable() is True


def test_a_cloud_connection_the_device_reported_offline_is_not_reachable() -> None:
    """An open MQTT socket does not mean the device itself is reachable."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, reported_offline=True)

    assert coordinator.is_reachable() is False


def test_a_cloud_connection_not_reported_offline_is_reachable() -> None:
    """The same cloud-only handle, without the offline flag, is reachable."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, reported_offline=False)

    assert coordinator.is_reachable() is True


def test_no_handle_is_not_reachable() -> None:
    """Before setup registers a handle, the coordinator reports unreachable, not an error."""
    coordinator = _coordinator()
    coordinator.manager.mower.return_value = None

    assert coordinator.is_reachable() is False
