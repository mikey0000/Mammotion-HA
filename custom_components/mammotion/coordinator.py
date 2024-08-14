"""Provides the mammotion DataUpdateCoordinator."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymammotion.data.model.device import MowingDevice
from pymammotion.mammotion.devices import MammotionBaseBLEDevice
from pymammotion.proto.mctrl_sys import RptAct, RptInfoType
from pymammotion.utility.constant import WorkMode

from .const import COMMAND_EXCEPTIONS, DOMAIN, LOGGER

if TYPE_CHECKING:
    from . import MammotionConfigEntry

SCAN_INTERVAL = timedelta(minutes=1)


class MammotionDataUpdateCoordinator(DataUpdateCoordinator[MowingDevice]):
    """Class to manage fetching mammotion data."""

    address: str
    config_entry: MammotionConfigEntry
    device_name: str
    device: MammotionBaseBLEDevice

    def __init__(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.update_failures = 0

    async def async_setup(self) -> None:
        """Set coordinator up."""

        address = self.config_entry.data[CONF_ADDRESS]
        ble_device = bluetooth.async_ble_device_from_address(self.hass, address)
        if not ble_device:
            raise ConfigEntryNotReady(
                f"Could not find Mammotion lawn mower with address {address}"
            )
        self.device = MammotionBaseBLEDevice(ble_device)
        self.device_name = ble_device.name or "Unknown"
        self.address = ble_device.address
        self.device.update_device(ble_device)
        try:
            await self.device.start_sync(0)
        except COMMAND_EXCEPTIONS as exc:
            raise ConfigEntryNotReady("Unable to setup Mammotion device") from exc


    async def async_sync_maps(self) -> None:
        """Get map data from the device."""
        await self.device.start_map_sync()

    async def async_start_stop_blades(self, start_stop: bool) -> None:
        if start_stop:
            await self.async_send_command("set_blade_control", on_off=1)
        else:
            await self.async_send_command("set_blade_control", on_off=0)

    async def async_blade_height(self, height: int) -> None:
        await self.async_send_command("set_blade_height", height=height)


    async def async_rtk_dock_location(self):
        """RTK and dock location."""
        await self.async_send_command("allpowerfull_rw", id=5, rw=1, context=1)


    async def async_request_iot_sync(self) -> None:
        await self.async_send_command("request_iot_sys", rpt_act=RptAct.RPT_START,
                               rpt_info_type=[RptInfoType.RIT_CONNECT, RptInfoType.RIT_DEV_STA, RptInfoType.RIT_DEV_LOCAL, RptInfoType.RIT_RTK, RptInfoType.RIT_WORK],
                               timeout=1000,
                               period=3000,
                               no_change_period=4000,
                               count=0)

    async def async_send_command(self, command: str, **kwargs) -> None:
        try:
            await self.device.command(command, **kwargs)
        except COMMAND_EXCEPTIONS as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed"
            ) from exc

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if not (
            ble_device := bluetooth.async_ble_device_from_address(
                self.hass, self.address
            )
        ):
            self.update_failures += 1
            raise UpdateFailed("Could not find device")

        self.device.update_device(ble_device)
        try:
            if len(self.device.luba_msg.net.toapp_devinfo_resp.resp_ids) == 0:
                await self.device.start_sync(0)
            if self.device.luba_msg.report_data.dev.sys_status != WorkMode.MODE_WORKING:
                await self.async_send_command("get_report_cfg")

            else:
                await self.async_request_iot_sync()


        except COMMAND_EXCEPTIONS as exc:
            self.update_failures += 1
            raise UpdateFailed(f"Updating Mammotion device failed: {exc}") from exc


        LOGGER.debug("Updated Mammotion device %s", self.device_name)
        LOGGER.debug("================= Debug Log =================")
        LOGGER.debug("Mammotion device data: %s", asdict(self.device.luba_msg))
        LOGGER.debug("==================================")

        self.update_failures = 0
        return self.device.luba_msg

    async def _async_setup(self) -> None:
        try:
            await self.device.start_sync(0)
        except COMMAND_EXCEPTIONS as exc:
            raise UpdateFailed(f"Setting up Mammotion device failed: {exc}") from exc


