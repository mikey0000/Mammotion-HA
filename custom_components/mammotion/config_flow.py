"""Config flow for Mammotion."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from bleak.backends.device import BLEDevice
from homeassistant import config_entries, data_entry_flow
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
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.loader import async_get_integration
from pymammotion.aliyun.exceptions import CloudSetupError, TooManyRequestsException
from pymammotion.client import MammotionClient
from pymammotion.transport.base import LoginFailedError

from .const import (
    BLE_SUPPORT,
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_BLE_DEVICES,
    CONF_DEVICE_NAME,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MOVEMENT_USE_WIFI,
    CONF_MOW_PATH_FETCH_ENABLED,
    CONF_NOTIFY,
    CONF_PREFER_BLE,
    CONF_USE_WIFI,
    CREDENTIAL_CACHE_KEYS,
    DEFAULT_NOTIFY,
    DOMAIN,
    LOGGER,
    NOTIFY_CATEGORIES,
)

CONF_REMOVE_ACCOUNT = "remove_account"

# Everything a removed account must not leave behind; a stale blob is replayed on setup.
_ACCOUNT_KEYS = (
    CONF_ACCOUNTNAME,
    CONF_PASSWORD,
    CONF_ACCOUNT_ID,
    *CREDENTIAL_CACHE_KEYS,
)


@dataclass(frozen=True)
class _CloudLogin:
    """What a flow keeps from a successful login once its client is stopped."""

    user_account: str
    cache: dict[str, Any]
    device_names: frozenset[str]


def _with_login(data: Mapping[str, Any], login: _CloudLogin) -> dict[str, Any]:
    """Return *data* with its credential cache replaced by *login*'s."""
    fresh = {k: v for k, v in data.items() if k not in CREDENTIAL_CACHE_KEYS}
    fresh.update(login.cache)
    return fresh


def _has_cloud_account(entry: ConfigEntry) -> bool:
    """Return whether *entry* has a cloud account, defaulting like setup for old entries."""
    return bool(
        entry.data.get(
            CONF_HAS_CLOUD_ACCOUNT,
            bool(entry.data.get(CONF_ACCOUNTNAME) and entry.data.get(CONF_PASSWORD)),
        )
    )


def _same_account(left: Any, right: Any) -> bool:
    return bool(left and right) and (
        str(left).strip().casefold() == str(right).strip().casefold()
    )


class MammotionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mammotion."""

    VERSION = 1
    MINOR_VERSION = 4

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._config: dict[str, Any] = {}
        self._discovered_device: BLEDevice | None = None
        self._discovered_devices: dict[str, str] = {}
        # Set when check_and_update_bluetooth_device has already asked for the
        # one reload this flow may cause.
        self._reload_scheduled = False

    def _ble_device_name(self) -> str | None:
        """Return the name of the mower this flow was started for, if any."""
        if self._discovered_device is not None:
            return self._discovered_device.name
        return next(iter(self._config.get(CONF_BLE_DEVICES, {})), None)

    def _entry_listing_device(self, name: str, address: str) -> ConfigEntry | None:
        """Return the entry that lists this device by name or by normalised BLE address.

        Legacy entries are keyed by the raw (uppercase) address or the device name.
        """
        mac = format_mac(address)
        for entry in self.hass.config_entries.async_entries(
            DOMAIN, include_ignore=False
        ):
            ble_devices = entry.data.get(CONF_BLE_DEVICES, {})
            if name in ble_devices or mac in {
                format_mac(known) for known in ble_devices.values()
            }:
                return entry
            if entry.unique_id is not None and (
                entry.unique_id == name or format_mac(entry.unique_id) == mac
            ):
                return entry
        return None

    def _entry_for_account(
        self, *accounts: str | None, exclude: str | None = None
    ) -> ConfigEntry | None:
        """Return the entry holding any of *accounts*, matched on its data not its unique_id."""
        for entry in self.hass.config_entries.async_entries(
            DOMAIN, include_ignore=False
        ):
            if entry.entry_id == exclude:
                continue
            held = (entry.data.get(CONF_ACCOUNTNAME), entry.data.get(CONF_ACCOUNT_ID))
            if any(_same_account(a, h) for a in accounts for h in held):
                return entry
        return None

    async def _async_login(
        self, account: str, password: str, errors: dict[str, str]
    ) -> _CloudLogin | None:
        """Log in with a throwaway client, stopped on every exit; on failure fill *errors*."""
        integration = await async_get_integration(self.hass, DOMAIN)
        client = MammotionClient(ha_version=integration.version.split("-")[0])
        try:
            await client.login_and_initiate_cloud(
                account, password, aiohttp_client.async_get_clientsession(self.hass)
            )
            if (http := client.mammotion_http) is None or http.login_info is None:
                errors["base"] = "login_failed"
                return None
            return _CloudLogin(
                user_account=http.login_info.userInformation.userAccount,
                cache=client.to_cache(),
                device_names=frozenset(
                    device.device_name
                    for device in (
                        *client.aliyun_device_list,
                        *client.mammotion_device_list,
                    )
                ),
            )
        except TooManyRequestsException as err:
            raise data_entry_flow.AbortFlow("api_limit_exceeded") from err
        except LoginFailedError as err:
            LOGGER.warning("Mammotion login rejected: %s", err)
            errors["base"] = "login_failed"
        except CloudSetupError as err:
            LOGGER.error("Aliyun cloud setup failed during login: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:
            LOGGER.exception("Unexpected error during Mammotion login")
            errors["base"] = "cannot_connect"
        finally:
            await client.stop()
        return None

    async def _async_take_ble_devices(
        self, source: ConfigEntry, names: Collection[str] | None = None
    ) -> dict[str, str]:
        """Move *names* (all when None) out of *source*; *source* is removed once it holds none."""
        held: dict[str, str] = source.data.get(CONF_BLE_DEVICES, {})
        taken = {n: mac for n, mac in held.items() if names is None or n in names}
        if remaining := {n: mac for n, mac in held.items() if n not in taken}:
            self.hass.config_entries.async_update_entry(
                source, data={**source.data, CONF_BLE_DEVICES: remaining}
            )
            self.hass.config_entries.async_schedule_reload(source.entry_id)
        else:
            await self.hass.config_entries.async_remove(source.entry_id)
        return taken

    async def _async_merge_into(
        self,
        target: ConfigEntry,
        source: ConfigEntry,
        login: _CloudLogin | None = None,
    ) -> ConfigFlowResult:
        """Move *source*'s BLE devices into the account entry *target* and drop *source*.

        *login*, when given, replaced *target*'s session server-side, so it is saved too.
        """
        data = dict(target.data) if login is None else _with_login(target.data, login)
        data[CONF_BLE_DEVICES] = {
            **target.data.get(CONF_BLE_DEVICES, {}),
            **await self._async_take_ble_devices(source),
        }
        self.hass.config_entries.async_update_entry(target, data=data)
        self.hass.config_entries.async_schedule_reload(target.entry_id)
        return self.async_abort(reason="merged_into_existing_account")

    async def _async_abort_if_owned(self, name: str, address: str) -> None:
        """Abort when an entry owns this device, recording it in that entry first."""
        if entry := await self.check_and_update_bluetooth_device(name, address):
            ble_devices = {
                name: format_mac(address),
                **entry.data.get(CONF_BLE_DEVICES, {}),
            }
            # Exactly one reload: check_and_update_bluetooth_device schedules it
            # when an address changed, and core schedules it here when this adds a
            # device to the entry.  Two would set up a competing client while the
            # first still holds the MQTT session; both connect with the same
            # client_id and the broker rejects them.
            self._abort_if_unique_id_configured(
                updates={CONF_BLE_DEVICES: ble_devices},
                reload_on_update=not self._reload_scheduled,
            )

    async def check_and_update_bluetooth_device(
        self, name: str, address: str
    ) -> ConfigEntry | None:
        """Return the entry that should own the device, updating its BLE MAC if needed.

        A device an entry lists (``_entry_listing_device``) or has in the device
        registry belongs to that entry whether or not it has a cloud account.  A
        device nobody knows joins the household's single BLE-only entry when there
        is exactly one, so BLE devices share one client instead of one per entry.
        """
        device_registry = dr.async_get(self.hass)
        current_entries = self.hass.config_entries.async_entries(
            DOMAIN, include_ignore=False
        )
        formatted_ble = format_mac(address)

        if (listed := self._entry_listing_device(name, address)) is not None:
            await self.async_set_unique_id(listed.unique_id)
            return listed

        for entry in current_entries:
            device_entries = dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            )
            for device_entry in device_entries:
                identifiers = {device_id[1] for device_id in device_entry.identifiers}
                if name not in identifiers:
                    continue
                await self.async_set_unique_id(entry.unique_id)
                already_added = (
                    CONNECTION_BLUETOOTH,
                    formatted_ble,
                ) in device_entry.connections
                if not already_added:
                    device_registry.async_update_device(
                        device_entry.id,
                        merge_connections={(CONNECTION_BLUETOOTH, formatted_ble)},
                    )
                    if entry.state == config_entries.ConfigEntryState.LOADED:
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                        self._reload_scheduled = True
                return entry

        ble_only_entries = [
            entry for entry in current_entries if not _has_cloud_account(entry)
        ]
        if len(ble_only_entries) == 1:
            await self.async_set_unique_id(ble_only_entries[0].unique_id)
            return ble_only_entries[0]
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

        if device.name is None or not device.name.startswith(BLE_SUPPORT):
            return self.async_abort(reason="not_supported")

        self.context["title_placeholders"] = {"name": device.name}

        self._discovered_device = device

        await self._async_abort_if_owned(device.name, device.address)

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""

        assert self._discovered_device
        assert self._discovered_device.name

        await self._async_abort_if_owned(
            self._discovered_device.name, self._discovered_device.address
        )

        ble_devices: dict[str, str] = {
            self._discovered_device.name: format_mac(self._discovered_device.address)
        }
        self._config = {
            CONF_BLE_DEVICES: ble_devices,
        }

        if user_input is not None:
            # Opens the login form, which is optional; it is not a blank submit.
            return await self.async_step_wifi()

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
            if address := user_input.get(CONF_ADDRESS):
                name = self._discovered_devices[address]
                self._discovered_device = bluetooth.async_ble_device_from_address(
                    self.hass, address
                )
                await self._async_abort_if_owned(name, address)
                self._config = {CONF_BLE_DEVICES: {name: format_mac(address)}}
            return await self.async_step_wifi()

        # Ignored entries count too, so a dismissed device is not offered again.
        current_ids = {format_mac(uid) for uid in self._async_current_ids() if uid}
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            name = discovery_info.name
            if address in self._discovered_devices:
                continue
            if name is None or not name.startswith(BLE_SUPPORT):
                continue
            if format_mac(address) in current_ids or name in current_ids:
                continue
            if self._entry_listing_device(name, address) is not None:
                continue

            self._discovered_devices[address] = name

        if not self._discovered_devices:
            return await self.async_step_wifi()

        return self.async_show_form(
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ADDRESS): vol.In(self._discovered_devices),
                }
            ),
        )

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle credentials entry or BLE-only setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            account = (user_input.get(CONF_ACCOUNTNAME) or "").strip()
            password = (user_input.get(CONF_PASSWORD) or "").strip()

            if account and password:
                # A login replaces the live entry's session, so recognise it first.
                if self._entry_for_account(account) is not None:
                    return self.async_abort(reason="already_configured")
                if login := await self._async_login(account, password, errors):
                    return await self._async_create_account_entry(
                        account, password, login
                    )
            # BLE-only: blank credentials
            elif not self._config.get(CONF_BLE_DEVICES):
                errors["base"] = "no_account_no_ble"
            else:
                ble_only_entries = [
                    e
                    for e in self.hass.config_entries.async_entries(
                        DOMAIN, include_ignore=False
                    )
                    if not _has_cloud_account(e)
                ]
                if len(ble_only_entries) == 1:
                    existing = ble_only_entries[0]
                    merged = {
                        **existing.data.get(CONF_BLE_DEVICES, {}),
                        **self._config[CONF_BLE_DEVICES],
                    }
                    await self.async_set_unique_id(
                        existing.unique_id, raise_on_progress=False
                    )
                    self._abort_if_unique_id_configured(
                        updates={CONF_BLE_DEVICES: merged}
                    )
                if not self.unique_id:
                    first_mac = next(iter(self._config[CONF_BLE_DEVICES].values()))
                    await self.async_set_unique_id(first_mac, raise_on_progress=False)
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._ble_device_name(),
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

    async def _async_create_account_entry(
        self, account: str, password: str, login: _CloudLogin
    ) -> ConfigFlowResult:
        """Create the account's entry, absorbing BLE-only entries of its devices."""
        if (existing := self._entry_for_account(login.user_account)) is not None:
            # Typed as an alias, so only known now; the login replaced its session.
            self.hass.config_entries.async_update_entry(
                existing, data=_with_login(existing.data, login)
            )
            self.hass.config_entries.async_schedule_reload(existing.entry_id)
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(login.user_account, raise_on_progress=False)
        self._abort_if_unique_id_configured()

        ble_devices: dict[str, str] = {}
        for entry in self.hass.config_entries.async_entries(
            DOMAIN, include_ignore=False
        ):
            if not _has_cloud_account(entry) and not login.device_names.isdisjoint(
                entry.data.get(CONF_BLE_DEVICES, {})
            ):
                ble_devices.update(
                    await self._async_take_ble_devices(entry, login.device_names)
                )
        ble_devices.update(self._config.get(CONF_BLE_DEVICES, {}))

        data: dict[str, Any] = {
            CONF_ACCOUNTNAME: account,
            CONF_PASSWORD: password,
            CONF_ACCOUNT_ID: login.user_account,
            CONF_DEVICE_NAME: self._ble_device_name(),
            CONF_USE_WIFI: True,
            CONF_HAS_CLOUD_ACCOUNT: True,
            **self._config,
            **login.cache,
        }
        if ble_devices:
            data[CONF_BLE_DEVICES] = ble_devices
        return self.async_create_entry(title=account, data=data)

    def _account_unique_id(self, entry: ConfigEntry, login: _CloudLogin) -> str | None:
        """Return the account as *entry*'s unique_id, unless another entry already holds it."""
        holder = self.hass.config_entries.async_entry_for_domain_unique_id(
            self.handler, login.user_account
        )
        if holder is not None and holder.entry_id != entry.entry_id:
            return entry.unique_id
        return login.user_account

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after the account's refresh token was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the account password and rebuild the credential cache from a fresh login."""
        entry = self._get_reauth_entry()
        account = entry.data.get(CONF_ACCOUNTNAME) or ""
        errors: dict[str, str] = {}

        if user_input is not None:
            password = (user_input.get(CONF_PASSWORD) or "").strip()
            if login := await self._async_login(account, password, errors):
                # Legacy entries are keyed by a BLE address or device name, so the
                # account id in the data is the only reliable identity.
                if (expected := entry.data.get(CONF_ACCOUNT_ID)) and not _same_account(
                    expected, login.user_account
                ):
                    return self.async_abort(reason="wrong_account")
                # One atomic update: the reload restores from the fresh login only.
                data = _with_login(entry.data, login)
                data[CONF_PASSWORD] = password
                data[CONF_ACCOUNT_ID] = login.user_account
                return self.async_update_reload_and_abort(
                    entry, unique_id=self._account_unique_id(entry, login), data=data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): cv.string}),
            description_placeholders={"account": account},
            errors=errors,
        )

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
        """Handle reconfiguration: add, change or remove the cloud account of an entry.

        Adding an account to a BLE-only entry makes the account the entry's identity
        (``unique_id``).  If another entry already holds that account, this entry's
        BLE mowers are merged into it and this entry is removed — one client per
        account, with every BLE mower attached to it.  Credentials are persisted on
        success only.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if TYPE_CHECKING:
            assert entry

        errors: dict[str, str] = {}

        if user_input is not None:
            account = (user_input.get(CONF_ACCOUNTNAME) or "").strip()
            password = (user_input.get(CONF_PASSWORD) or "").strip()

            if user_input.get(CONF_REMOVE_ACCOUNT):
                if not entry.data.get(CONF_BLE_DEVICES):
                    errors["base"] = "no_account_no_ble"
                else:
                    return self._async_remove_account(entry)
            elif not (account and password):
                errors["base"] = "credentials_required"
            elif other := self._entry_for_account(account, exclude=entry.entry_id):
                # The other entry's session already works; a login would replace it.
                return await self._async_merge_into(other, entry)
            elif login := await self._async_login(account, password, errors):
                if other := self._entry_for_account(
                    login.user_account, exclude=entry.entry_id
                ):
                    return await self._async_merge_into(other, entry, login)
                data = _with_login(entry.data, login)
                data.update(
                    {
                        CONF_ACCOUNTNAME: account,
                        CONF_PASSWORD: password,
                        CONF_ACCOUNT_ID: login.user_account,
                        CONF_USE_WIFI: True,
                        CONF_HAS_CLOUD_ACCOUNT: True,
                    }
                )
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=self._account_unique_id(entry, login),
                    title=account,
                    data=data,
                    reason="reconfigure_successful",
                )

        # A default would render the stored password into the browser.
        schema: dict[vol.Marker, Any] = {
            vol.Optional(
                CONF_ACCOUNTNAME,
                description={"suggested_value": entry.data.get(CONF_ACCOUNTNAME)},
            ): cv.string,
            vol.Optional(CONF_PASSWORD): cv.string,
        }
        # The frontend omits cleared fields, so removal needs a control of its own.
        if _has_cloud_account(entry):
            schema[vol.Optional(CONF_REMOVE_ACCOUNT, default=False)] = cv.boolean

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    @callback
    def _async_remove_account(self, entry: ConfigEntry) -> ConfigFlowResult:
        """Turn *entry* into a BLE-only entry keyed by its first device."""
        ble_devices: dict[str, str] = entry.data[CONF_BLE_DEVICES]
        data = {k: v for k, v in entry.data.items() if k not in _ACCOUNT_KEYS}
        data.update({CONF_USE_WIFI: False, CONF_HAS_CLOUD_ACCOUNT: False})
        # Keyed by the account, adding it again later would abort as already configured.
        first_mac = next(iter(ble_devices.values()))
        holder = self.hass.config_entries.async_entry_for_domain_unique_id(
            self.handler, first_mac
        )
        return self.async_update_reload_and_abort(
            entry,
            unique_id=first_mac if holder is None else UNDEFINED,
            title=next(iter(ble_devices)),
            data=data,
            reason="reconfigure_successful",
        )


class MammotionConfigFlowHandler(OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self.prefer_ble = config_entry.options.get(CONF_PREFER_BLE, True)
        self.movement_use_wifi = config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False)
        self.mow_path_fetch_enabled = config_entry.options.get(
            CONF_MOW_PATH_FETCH_ENABLED, False
        )
        self.notify = config_entry.options.get(CONF_NOTIFY, DEFAULT_NOTIFY)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options for the custom component."""
        if user_input:
            new_prefer_ble = user_input.get(CONF_PREFER_BLE, True)
            use_wifi = self._config_entry.data.get(CONF_USE_WIFI, True)

            if (
                runtime := getattr(self._config_entry, "runtime_data", None)
            ) is not None:
                for mower in runtime.mowers:
                    # Setup's rule (_async_bring_up_mower): the Bluetooth switch wins.
                    use_ble = mower.reporting_coordinator.bluetooth_enabled and (
                        not use_wifi or new_prefer_ble
                    )
                    mower.api.set_prefer_ble(mower.name, prefer_ble=use_ble)
                    mower.api.set_mow_path_fetch_enabled(
                        mower.name,
                        enabled=user_input.get(CONF_MOW_PATH_FETCH_ENABLED, False),
                    )

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
                vol.Optional(
                    CONF_MOW_PATH_FETCH_ENABLED,
                    default=self.mow_path_fetch_enabled,
                ): cv.boolean,
                vol.Optional(CONF_NOTIFY, default=self.notify): SelectSelector(
                    SelectSelectorConfig(
                        options=list(NOTIFY_CATEGORIES),
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_NOTIFY,
                    )
                ),
            }
        )

        return self.async_show_form(
            data_schema=options_schema,
        )
