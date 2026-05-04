"""Config flow for Mammotion"""

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from aiohttp.web_exceptions import HTTPException
from bleak.backends.device import BLEDevice
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfo,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, format_mac
from homeassistant.loader import async_get_integration
from pymammotion.aliyun.exceptions import CloudSetupError, TooManyRequestsException
from pymammotion.client import MammotionClient

from .const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_BLE_DEVICES,
    CONF_DEVICE_NAME,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MOVEMENT_USE_WIFI,
    CONF_PREFER_BLE,
    CONF_USE_WIFI,
    DEVICE_SUPPORT,
    DOMAIN,
    LOGGER,
)


class MammotionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mammotion."""

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._config: dict = {}
        self._discovered_device: BLEDevice | None = None
        self._discovered_devices: dict[str, str] = {}

    async def check_and_update_bluetooth_device(self, device: BLEDevice) -> ConfigEntry:
        """Check if the device is already configured and update ble mac if needed."""
        device_registry = dr.async_get(self.hass)
        current_entries = self.hass.config_entries.async_entries(DOMAIN)

        for entry in current_entries:
            if not entry.data.get(CONF_ACCOUNT_ID):
                continue

            device_entries = dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            )

            for device_entry in device_entries:
                formatted_ble = format_mac(self._discovered_device.address)
                identifiers = {device_id[1] for device_id in device_entry.identifiers}
                already_connected = (
                    CONNECTION_BLUETOOTH,
                    formatted_ble,
                ) in device_entry.connections
                if device.name in identifiers:
                    await self.async_set_unique_id(entry.data.get(CONF_ACCOUNT_ID))

                    if not already_connected:
                        device_registry.async_update_device(
                            device_entry.id,
                            merge_connections={(CONNECTION_BLUETOOTH, formatted_ble)},
                        )
                        if entry.state == config_entries.ConfigEntryState.LOADED:
                            self.hass.config_entries.async_schedule_reload(
                                entry.entry_id
                            )
                    return entry
        return None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo | None = None
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        LOGGER.debug("Discovered bluetooth device: %s", discovery_info)
        if discovery_info is None:
            return self.async_abort(reason="no_devices_found")

        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()

        device = bluetooth.async_ble_device_from_address(
            self.hass, discovery_info.address
        )

        if device is None:
            return self.async_abort(reason="no_longer_present")

        if device.name is None or not device.name.startswith(DEVICE_SUPPORT):
            return self.async_abort(reason="not_supported")

        self.context["title_placeholders"] = {"name": device.name}

        self._discovered_device = device

        if entry := await self.check_and_update_bluetooth_device(device):
            ble_devices = {
                self._discovered_device.name: format_mac(
                    self._discovered_device.address
                ),
                **entry.data.get(CONF_BLE_DEVICES, {}),
            }
            self._abort_if_unique_id_configured(updates={CONF_BLE_DEVICES: ble_devices})

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""

        assert self._discovered_device

        if entry := await self.check_and_update_bluetooth_device(
            self._discovered_device
        ):
            merged = {
                self._discovered_device.name: format_mac(
                    self._discovered_device.address
                ),
                **entry.data.get(CONF_BLE_DEVICES, None),
            }
            self._abort_if_unique_id_configured(updates={CONF_BLE_DEVICES: merged})

        ble_devices: dict[str, str] = {
            self._discovered_device.name: format_mac(self._discovered_device.address)
        }
        self._config = {
            CONF_BLE_DEVICES: ble_devices,
        }

        if user_input is not None:
            return await self.async_step_wifi(user_input)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            last_step=False,
            description_placeholders={"name": self._discovered_device.name},
            data_schema=vol.Schema({}),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""

        if user_input is not None:
            return await self.async_step_wifi(user_input)

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            name = discovery_info.name
            if address in current_addresses or address in self._discovered_devices:
                continue
            if name is None or not name.startswith(DEVICE_SUPPORT):
                continue
            if self.hass.config_entries.async_entry_for_domain_unique_id(
                self.handler, name
            ):
                continue

            self._discovered_devices[address] = discovery_info.name

        if not self._discovered_devices:
            return await self.async_step_wifi(user_input)

        return self.async_show_form(
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ADDRESS): vol.In(self._discovered_devices),
                }
            ),
        )

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Handle credentials entry or BLE-only setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            account = (user_input.get(CONF_ACCOUNTNAME) or "").strip()
            password = (user_input.get(CONF_PASSWORD) or "").strip()

            if account and password:
                integration = await async_get_integration(self.hass, DOMAIN)
                temp_client = MammotionClient(ha_version=integration.version)
                try:
                    session = aiohttp_client.async_get_clientsession(self.hass)
                    await temp_client.login_and_initiate_cloud(
                        account, password, session
                    )
                    if (
                        temp_client.mammotion_http is None
                        or temp_client.mammotion_http.login_info is None
                    ):
                        errors["base"] = "login_failed"
                    else:
                        user_account = temp_client.mammotion_http.login_info.userInformation.userAccount
                        await self.async_set_unique_id(
                            user_account, raise_on_progress=False
                        )
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=account,
                            data={
                                CONF_ACCOUNTNAME: account,
                                CONF_PASSWORD: password,
                                CONF_ACCOUNT_ID: user_account,
                                CONF_DEVICE_NAME: self._discovered_device.name
                                if self._discovered_device
                                else None,
                                CONF_USE_WIFI: True,
                                CONF_HAS_CLOUD_ACCOUNT: True,
                                **self._config,
                            },
                        )
                except TooManyRequestsException:
                    return self.async_abort(reason="api_limit_exceeded")
                except CloudSetupError as err:
                    LOGGER.error("Aliyun cloud setup failed during login: %s", err)
                    errors["base"] = "cannot_connect"
                except (HTTPException, Exception) as err:
                    LOGGER.error("Unexpected error during login: %s", err)
                    errors["base"] = "cannot_connect"
                finally:
                    await temp_client.stop()
            # BLE-only: blank credentials
            elif not self._config.get(CONF_BLE_DEVICES):
                errors["base"] = "no_account_no_ble"
            else:
                if not self.unique_id:
                    first_mac = next(iter(self._config[CONF_BLE_DEVICES].values()))
                    await self.async_set_unique_id(first_mac, raise_on_progress=False)
                    self._abort_if_unique_id_configured()
                title = (
                    self._discovered_device.name
                    if self._discovered_device
                    else next(iter(self._config[CONF_BLE_DEVICES]))
                )
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_USE_WIFI: False,
                        CONF_HAS_CLOUD_ACCOUNT: False,
                        **self._config,
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_ACCOUNTNAME): cv.string,
                vol.Optional(CONF_PASSWORD): cv.string,
            }
        )
        return self.async_show_form(step_id="wifi", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return MammotionConfigFlowHandler(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if TYPE_CHECKING:
            assert entry

        errors: dict[str, str] = {}

        if user_input:
            account = (user_input.get(CONF_ACCOUNTNAME) or "").strip()
            password = (user_input.get(CONF_PASSWORD) or "").strip()

            has_cloud_account = False
            account_id = entry.data.get(CONF_ACCOUNT_ID)

            if account and password:
                integration = await async_get_integration(self.hass, DOMAIN)
                temp_client = MammotionClient(ha_version=integration.version)
                try:
                    session = aiohttp_client.async_get_clientsession(self.hass)
                    await temp_client.login_and_initiate_cloud(
                        account, password, session
                    )
                    if (
                        temp_client.mammotion_http is not None
                        and temp_client.mammotion_http.login_info is not None
                    ):
                        has_cloud_account = True
                        account_id = temp_client.mammotion_http.login_info.userInformation.userAccount
                    else:
                        errors["base"] = "login_failed"
                except TooManyRequestsException:
                    return self.async_abort(reason="api_limit_exceeded")
                except (CloudSetupError, HTTPException, Exception) as err:
                    LOGGER.error("Login failed during reconfigure: %s", err)
                    errors["base"] = "cannot_connect"
                finally:
                    await temp_client.stop()

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_ACCOUNTNAME: account or None,
                        CONF_PASSWORD: password or None,
                        CONF_ACCOUNT_ID: account_id,
                        CONF_USE_WIFI: bool(account),
                        CONF_HAS_CLOUD_ACCOUNT: has_cloud_account,
                    },
                    reason="reconfigure_successful",
                )

        schema = {
            vol.Optional(
                CONF_ACCOUNTNAME, default=entry.data.get(CONF_ACCOUNTNAME, "")
            ): cv.string,
            vol.Optional(
                CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD, "")
            ): cv.string,
        }

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema),
            errors=errors,
        )


class MammotionConfigFlowHandler(OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self.prefer_ble = config_entry.options.get(CONF_PREFER_BLE, True)
        self.movement_use_wifi = config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options for the custom component."""
        if user_input:
            new_prefer_ble = user_input.get(CONF_PREFER_BLE, True)

            if (
                runtime := getattr(self._config_entry, "runtime_data", None)
            ) is not None:
                for mower in runtime.mowers:
                    mower.api.set_prefer_ble(mower.name, prefer_ble=new_prefer_ble)

            return self.async_create_entry(data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PREFER_BLE,
                    default=self.prefer_ble,
                ): cv.boolean,
                vol.Optional(
                    CONF_MOVEMENT_USE_WIFI,
                    default=self.movement_use_wifi,
                ): cv.boolean,
            }
        )

        return self.async_show_form(
            data_schema=options_schema,
        )
