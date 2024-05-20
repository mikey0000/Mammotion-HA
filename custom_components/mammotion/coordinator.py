"""Provides the mammotion DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING
from datetime import timedelta

from pyluba.mammotion.devices import MammotionBaseBLEDevice

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice


_LOGGER = logging.getLogger(__name__)

DEVICE_STARTUP_TIMEOUT = 5


class MammotionDataUpdateCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        ble_device: BLEDevice,
        device: MammotionBaseBLEDevice,
        base_unique_id: str,
        device_name: str,
        update_interval: timedelta,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            logger=logger,
            address=ble_device.address,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_update,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
            update_interval=update_interval,
        )
        self.ble_device = ble_device
        self.device = device
        self.device_name = device_name
        self.base_unique_id = base_unique_id
        self._ready_event = asyncio.Event()
        self._was_unavailable = True
        self.last_update_success = True

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        # Only poll if hass is running, we need to poll,
        # and we actually have a way to connect to the device
        print("try and poll")
        time_to_poll = (
            True if seconds_since_last_poll is None else seconds_since_last_poll > 300
        )
        print(time_to_poll)
        print(seconds_since_last_poll)
        return (
            self.hass.state is CoreState.running
            and time_to_poll
            and bool(
                bluetooth.async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        )

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Poll the device."""
        await self.device.start_sync("key", 0)

    @callback
    def _async_handle_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle the device going unavailable."""
        super()._async_handle_unavailable(service_info)
        self._was_unavailable = True

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.ble_device = service_info.device
        if service_info.name:
            self._ready_event.set()
        _LOGGER.debug(
            "%s: mammotion Luba data: %s", self.ble_device.address, self.device.raw_data
        )
        self._was_unavailable = False
        super()._async_handle_bluetooth_event(service_info, change)

    async def async_wait_ready(self) -> bool:
        """Wait for the device to be ready."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(DEVICE_STARTUP_TIMEOUT):
                await self._ready_event.wait()
                return True
        return False
