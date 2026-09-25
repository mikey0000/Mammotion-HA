"""BLE must come back on its own once the mower is in range again.

Two defects kept it down. The advertisement callback that always runs dropped
the RSSI, and ``BLETransport.is_usable`` fails closed below ``min_rssi`` until a
stronger reading arrives — so a mower that faded out of range stayed unusable
however strongly it came back. And the reconnect attempt sat after the refresh's
``is_online()`` early return, which on a BLE-only mower is False precisely when
BLE is down, so it only ever ran while already connected.

The version in ``tests/`` checked the order of two tokens in the source.  Here
the refresh actually runs with the transport reporting itself offline, and the
advertisement callback is the one the integration registers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.transport.base import TransportType

from custom_components.mammotion import _register_ble_reconnect_callback
from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_DEVICE_NAME = "Luba-VS1000001"
_BLE_MAC = "AA:BB:CC:DD:EE:FF"


def _transport() -> MagicMock:
    """Return a BLE transport in range and idle — the case that must reconnect."""
    ble = MagicMock()
    ble.is_connected = False
    ble.is_usable = True
    ble.connect = AsyncMock()
    return ble


def _handle(ble: MagicMock) -> MagicMock:
    """Stand in for the pymammotion device handle that owns the transport."""
    handle = MagicMock()
    handle.prefer_ble = True
    handle.get_transport = MagicMock(
        side_effect=lambda t_type: ble if t_type is TransportType.BLE else None
    )
    return handle


def _coordinator(
    ble: MagicMock | None = None, *, ble_mac: str = ""
) -> MammotionReportUpdateCoordinator:
    """Build the real report coordinator; the transport layer under it is a stand-in."""
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    device = MowingDevice()
    device.mower_state.ble_mac = ble_mac
    coordinator.data = device
    coordinator.device = device
    coordinator.device_name = _DEVICE_NAME
    coordinator.update_failures = 0
    coordinator._startup_reads_done = True
    coordinator._bluetooth_enabled = True
    coordinator._on_stop = []
    coordinator._subscriptions = []
    coordinator._mow_progress_debouncer = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.manager.get_device_by_name.return_value = device
    coordinator.manager.mower.return_value = _handle(ble if ble else _transport())
    coordinator.async_save_data = MagicMock()
    coordinator.async_flush_saved_data = AsyncMock()
    coordinator.is_online = MagicMock(return_value=False)
    return coordinator


async def test_the_advertisement_callback_carries_the_rssi() -> None:
    """Without it a weak last reading latches the transport unusable for good."""
    hass, entry, mammotion = MagicMock(), MagicMock(), MagicMock()
    mammotion.add_ble_to_device = MagicMock()
    service_info = MagicMock(rssi=-52)

    with (
        patch("custom_components.mammotion.bluetooth.async_register_callback") as reg,
        patch("custom_components.mammotion.async_get_store"),
    ):
        _register_ble_reconnect_callback(hass, entry, mammotion, _DEVICE_NAME, _BLE_MAC)
        ble_seen = reg.call_args.args[1]
        ble_seen(service_info, MagicMock())

    mammotion.add_ble_to_device.assert_called_once_with(
        _DEVICE_NAME, service_info.device, rssi=-52
    )


async def test_reconnect_runs_before_the_refresh_can_bail_out() -> None:
    """A BLE-only mower is offline exactly while the link it needs is down."""
    ble = _transport()
    coordinator = _coordinator(ble)

    await coordinator._async_update_data()

    assert coordinator.is_online() is False
    ble.connect.assert_awaited_once()


async def test_the_advertisement_subscription_is_wired_at_setup() -> None:
    """A mower unreachable at startup never reached the old inline registration."""
    coordinator = _coordinator(ble_mac=_BLE_MAC)

    with patch(
        "custom_components.mammotion.coordinator.async_register_callback"
    ) as register:
        await coordinator._async_setup()

    register.assert_called_once()


async def test_registration_is_idempotent() -> None:
    """Setup and every refresh both call it, so it must not stack subscriptions."""
    coordinator = _coordinator(ble_mac=_BLE_MAC)

    with patch(
        "custom_components.mammotion.coordinator.async_register_callback"
    ) as register:
        await coordinator._async_setup()
        await coordinator._async_update_data()
        await coordinator._async_update_data()

    register.assert_called_once()


async def test_a_mower_without_a_known_mac_is_not_subscribed_yet() -> None:
    """It is first seen after startup; the refresh picks it up once the MAC lands."""
    coordinator = _coordinator()

    with patch(
        "custom_components.mammotion.coordinator.async_register_callback"
    ) as register:
        await coordinator._async_update_data()
        register.assert_not_called()

        coordinator.data.mower_state.ble_mac = _BLE_MAC
        await coordinator._async_update_data()

    register.assert_called_once()


async def test_the_subscription_is_dropped_on_shutdown() -> None:
    """_async_stop was never called by anything before."""
    coordinator = _coordinator(ble_mac=_BLE_MAC)
    unsubscribe = MagicMock()

    with patch(
        "custom_components.mammotion.coordinator.async_register_callback",
        return_value=unsubscribe,
    ):
        await coordinator._async_setup()
    with patch.object(MammotionBaseUpdateCoordinator, "async_shutdown", AsyncMock()):
        await coordinator.async_shutdown()

    unsubscribe.assert_called_once()
    assert coordinator._on_stop == []


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("is_connected", True),
        ("is_usable", False),
    ],
)
async def test_the_transport_state_still_gates_the_reconnect(
    attribute: str, value: bool
) -> None:
    """BLETransport owns the cooldown and the RSSI floor; this must not bypass them."""
    ble = _transport()
    setattr(ble, attribute, value)

    await _coordinator(ble)._async_reconnect_ble()

    ble.connect.assert_not_awaited()


async def test_the_bluetooth_switch_still_gates_the_reconnect() -> None:
    """Turning Bluetooth off has to keep the transport detached."""
    ble = _transport()
    coordinator = _coordinator(ble)
    coordinator._bluetooth_enabled = False

    await coordinator._async_reconnect_ble()

    ble.connect.assert_not_awaited()


async def test_a_cloud_preferring_mower_is_left_alone() -> None:
    """prefer_ble is the library's own view of which transport should lead."""
    ble = _transport()
    coordinator = _coordinator(ble)
    coordinator.manager.mower.return_value.prefer_ble = False

    await coordinator._async_reconnect_ble()

    ble.connect.assert_not_awaited()
