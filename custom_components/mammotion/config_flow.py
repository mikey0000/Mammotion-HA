"""Config flow for Mammotion Luba."""

from typing import Any

import voluptuous as vol
from bleak import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfo,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithConfigEntry, ConfigEntry, \
    OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import callback

from homeassistant.helpers import config_validation as cv

from .const import DEVICE_SUPPORT, DOMAIN, LOGGER, CONF_USE_BLUETOOTH, CONF_USE_WIFI, CONF_STAY_CONNECTED_BLUETOOTH


class MammotionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mammotion."""

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_device: BLEDevice | None = None
        self._discovered_devices: dict[str, str] = {}


    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""

        LOGGER.debug("Discovered bluetooth device: %s", discovery_info)
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured(
            updates={CONF_ADDRESS: discovery_info.address}
        )

        device = bluetooth.async_ble_device_from_address(
            self.hass, discovery_info.address
        )

        if device is None:
            return self.async_abort(reason="no_longer_present")

        if device.name is None or not device.name.startswith(DEVICE_SUPPORT):
            return self.async_abort(reason="not_supported")

        self.context["title_placeholders"] = {"name": device.name}

        self._discovered_device = device

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""

        assert self._discovered_device

        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_device.name or "",
                data={
                    CONF_ADDRESS: self._discovered_device.address,
                },
            )

        self._set_confirm_only()
        return self.async_show_form(
            description_placeholders={"name": self._discovered_device.name},
        )


    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            name = self._discovered_devices.get(address)
            if name is None:
                return self.async_abort(reason="no_longer_present")

            return self.async_create_entry(
                title=name,
                data={
                    CONF_ADDRESS: address,
                },
            )

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            name = discovery_info.name
            if address in current_addresses or address in self._discovered_devices:
                continue
            if name is None or not name.startswith(DEVICE_SUPPORT):
                continue
            self._discovered_devices[address] = discovery_info.name

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(self._discovered_devices),
                },
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
            config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return MammotionConfigFlowHandler(config_entry)

class MammotionConfigFlowHandler(OptionsFlowWithConfigEntry):
    """Handles options flow for the component."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options for the custom component."""
        if user_input:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_USE_BLUETOOTH,
                    default=self.options.get(CONF_USE_BLUETOOTH, True),
                ): cv.boolean,
                vol.Optional(
                    CONF_STAY_CONNECTED_BLUETOOTH,
                    default=self.options.get(CONF_STAY_CONNECTED_BLUETOOTH, False),
                ): cv.boolean,
                vol.Optional(
                    CONF_USE_WIFI,
                    default=self.options.get(CONF_USE_WIFI, True),
                ): cv.boolean,
            }
        )

        return self.async_show_form(
            data_schema=options_schema,
        )
