"""The Mammotion integration."""

from __future__ import annotations

from asyncio import CancelledError
from contextlib import suppress
from datetime import datetime
from typing import Any

from aiohttp import ClientConnectorError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HassJob, HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.device_registry import (
    async_get as async_get_device_registry,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_integration
from pymammotion.aliyun.exceptions import TooManyRequestsException
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.transport.base import (
    AccountInUseError,
    LoginFailedError,
    ReLoginRequiredError,
    TransportError,
    TransportType,
)
from pymammotion.utility.device_type import DeviceType
from Tea.exceptions import UnretryableException

from .config import (
    TRANSPORT_BLUETOOTH,
    MammotionConfigStore,
    async_get_store,
    async_pop_store,
)
from .const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_BLE_DEVICES,
    CONF_CONNECT_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MAMMOTION_DEVICE_RECORDS,
    CONF_MAMMOTION_MQTT,
    CONF_MOW_PATH_FETCH_ENABLED,
    CONF_PREFER_BLE,
    CONF_STAY_CONNECTED_BLUETOOTH,
    CONF_USE_WIFI,
    CREDENTIAL_CACHE_KEYS,
    DEVICE_SUPPORT,
    DOMAIN,
    EXPIRED_CREDENTIAL_EXCEPTIONS,
    LOGGER,
)
from .coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionRTKCoordinator,
    MammotionSpinoCoordinator,
)
from .models import (
    MammotionDevices,
    MammotionMowerData,
    MammotionRTKData,
    MammotionSpinoData,
)
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.LAWN_MOWER,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.CAMERA,
    Platform.UPDATE,
    Platform.VACUUM,
]

type MammotionConfigEntry = ConfigEntry[MammotionDevices]


def _clear_cached_credentials(hass: HomeAssistant, entry: MammotionConfigEntry) -> None:
    """Drop every cached credential blob from the entry.

    Called when the server has rejected the cached session: leaving the blobs in
    place makes every subsequent setup attempt (each HA restart or reload) re-spend
    the same dead refresh token on a doomed oauth2/token call and then a doomed
    password login — the retry-per-restart loop that got accounts deactivated.
    """
    hass.config_entries.async_update_entry(
        entry,
        data={k: v for k, v in entry.data.items() if k not in CREDENTIAL_CACHE_KEYS},
    )


async def _async_attempt_login(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    account: str,
    password: str,
    *,
    ble_fallback: bool,
) -> bool:
    """Attempt cloud login with credential-cache support.

    Returns True on success. Returns False when login fails and ``ble_fallback``
    is True (BLE devices are available as a fallback). Raises the appropriate
    ConfigEntry exception when login fails with no BLE fallback available.
    """
    session = aiohttp_client.async_get_clientsession(hass)
    cached = _load_cached_credentials(entry)
    try:
        if cached:
            await mammotion.restore_credentials(
                account, password, cached, session, check_for_new_devices=True
            )
        else:
            await mammotion.login_and_initiate_cloud(account, password, session)
        return True
    except ClientConnectorError as err:
        raise ConfigEntryNotReady(err)
    except LoginFailedError as err:
        # restore_credentials only raises this after the cached login was rejected
        # AND its fallback password login failed — the cache is dead either way.
        _clear_cached_credentials(hass, entry)
        if ble_fallback:
            LOGGER.warning(
                "Mammotion login failed; continuing in BLE-only mode: %s", err
            )
            return False
        raise ConfigEntryAuthFailed(err) from err
    except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
        LOGGER.debug(exc)
        if cached:
            LOGGER.warning(
                "Cached credentials are stale (%s) — clearing them before retrying",
                exc,
            )
            _clear_cached_credentials(hass, entry)
        try:
            await mammotion.login_and_initiate_cloud(
                account, password, aiohttp_client.async_get_clientsession(hass)
            )
            return True
        except (LoginFailedError, ReLoginRequiredError) as retry_err:
            if ble_fallback:
                LOGGER.warning(
                    "Login failed after cache clear; continuing in BLE-only mode: %s",
                    retry_err,
                )
                return False
            raise ConfigEntryAuthFailed(retry_err) from retry_err
    except AccountInUseError as err:
        if ble_fallback:
            LOGGER.warning(
                "Mammotion account in use elsewhere; continuing in BLE-only mode: %s",
                err,
            )
            return False
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="account_in_use"
        ) from err
    except TooManyRequestsException as err:
        if ble_fallback:
            LOGGER.warning("Mammotion API rate limited; continuing in BLE-only mode")
            return False
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="api_limit_exceeded"
        ) from err
    except UnretryableException as err:
        if ble_fallback:
            LOGGER.warning(
                "Unretryable login error; continuing in BLE-only mode: %s", err
            )
            return False
        raise ConfigEntryError(err)
    except Exception:
        return False


async def _register_ble_devices(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
) -> dict[str, str]:
    """Register every configured BLE mower as a device before any cloud login.

    BLE needs no account, so the handles exist first; a cloud login that follows
    adopts them (same handle, BLE transport kept).  A mower out of range is
    registered by address and picks up its BLEDevice from the reconnect callback.
    Returns the ``device_name → mac`` map of mowers registered here.
    """
    registered: dict[str, str] = {}
    for device_name, ble_address in entry.data.get(CONF_BLE_DEVICES, {}).items():
        if not device_name.startswith(DEVICE_SUPPORT):
            continue
        ble_device = bluetooth.async_ble_device_from_address(
            hass, ble_address.upper(), True
        )
        if ble_device is None:
            LOGGER.info(
                "BLE device %s (%s) not in range at startup — registering and waiting",
                device_name,
                ble_address,
            )
        await mammotion.add_ble_only_device(
            device_id=device_name,
            device_name=device_name,
            initial_device=MowingDevice(name=device_name),
            ble_device=ble_device,
            ble_address=None if ble_device is not None else ble_address,
        )
        if (mowing_device := mammotion.get_device_by_name(device_name)) is not None:
            mowing_device.mower_state.ble_mac = ble_address
        _register_ble_reconnect_callback(hass, entry, mammotion, device_name, ble_address)
        registered[device_name] = ble_address
    return registered


async def _attach_ble_to_rtk(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    rtk: Device,
    ble_address: str,
) -> None:
    """Attach a BLE transport to an RTK base station and register a persistent update callback."""
    rtk_device = mammotion.get_device_by_name(rtk.device_name)
    if rtk_device is not None:
        rtk_device.ble_mac = ble_address

    ble_device = bluetooth.async_ble_device_from_address(
        hass, ble_address.upper(), True
    )
    if ble_device:
        await mammotion.add_ble_to_device(rtk.device_name, ble_device)


def _register_ble_reconnect_callback(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    device_name: str,
    ble_address: str,
) -> None:
    """Register a persistent BLE callback to reconnect when a device comes in range."""

    def _ble_seen(
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        handle = mammotion.mower(device_name)
        if handle is None:
            return
        # add_ble_to_device would re-create the transport the Bluetooth switch detached.
        if not async_get_store(hass, entry).transport_enabled(
            device_name, TRANSPORT_BLUETOOTH
        ):
            return
        # Always push the freshest BLEDevice into the transport.  add_ble_to_device
        # is idempotent: it calls set_ble_device() if a transport already exists, or
        # creates a new transport if one doesn't.  We must not short-circuit on
        # has_transport() here because a device registered at startup without being
        # in range has a transport with no BLEDevice — it needs updating too.
        hass.async_create_task(
            mammotion.add_ble_to_device(device_name, service_info.device)
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _ble_seen,
            BluetoothCallbackMatcher(address=ble_address.upper()),
            BluetoothScanningMode.ACTIVE,
        )
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Mammotion integration."""
    async_setup_services(hass)
    return True


async def _await_device_connection(
    mammotion: MammotionClient,
    device_name: str,
    *,
    prefer_ble: bool,
) -> bool:
    """Wait for a transport to connect before the coordinators start polling.

    There's no point hitting the coordinators before MQTT/BLE is up. MQTT
    auto-connects after login, but BLE does not — so when we prefer BLE, kick the
    connection here. Then wait for MQTT to be stable for 10s (or BLE to connect),
    giving up after 60s and continuing regardless.

    Returns False without waiting when the device has nothing that could carry a
    command right now (e.g. a BLE-only mower out of range) — the caller then does a
    best-effort first refresh instead of one that would raise ConfigEntryNotReady.
    """
    handle = mammotion.mower(device_name)
    if handle is None or not handle.has_usable_transport:
        return False
    if (
        prefer_ble
        and (ble := handle.get_transport(TransportType.BLE))
        and ble.is_usable
    ):
        with suppress(TransportError):
            await handle.connect_transport(TransportType.BLE)
    try:
        await handle.wait_until_connected(timeout=60, mqtt_stable_for=10)
    except CancelledError:
        raise HomeAssistantError("Setup cancelled, transport connection timed out")
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version > 1:
        return False

    if entry.version == 1 and entry.minor_version < 2:
        # Entries created before the connect response was stored under
        # CONF_CONNECT_DATA kept it under the legacy "connect_response" key.
        data = dict(entry.data)
        legacy = data.pop("connect_response", None)
        if legacy is not None and CONF_CONNECT_DATA not in data:
            data[CONF_CONNECT_DATA] = legacy
        hass.config_entries.async_update_entry(
            entry, data=data, version=1, minor_version=2
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Set up Mammotion from a config entry."""

    addresses = entry.data.get(CONF_BLE_DEVICES, {})
    integration = await async_get_integration(hass, DOMAIN)
    mammotion = MammotionClient(ha_version=integration.version.split("-")[0])

    store = async_get_store(hass, entry)
    await store.async_load_device_data()

    async def shutdown_mammotion(_: Event | None = None) -> None:
        await mammotion.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown_mammotion)
    )
    entry.async_on_unload(shutdown_mammotion)

    account = entry.data.get(CONF_ACCOUNTNAME)
    password = entry.data.get(CONF_PASSWORD)
    use_wifi = entry.data.get(CONF_USE_WIFI, True)

    # Migrate options: move from stay_connected_bluetooth to prefer_ble default.
    if not entry.options:
        hass.config_entries.async_update_entry(entry, options={CONF_PREFER_BLE: True})
    elif (
        CONF_STAY_CONNECTED_BLUETOOTH in entry.options
        and CONF_PREFER_BLE not in entry.options
    ):
        new_opts = {
            k: v for k, v in entry.options.items() if k != CONF_STAY_CONNECTED_BLUETOOTH
        }
        new_opts[CONF_PREFER_BLE] = True
        hass.config_entries.async_update_entry(entry, options=new_opts)

    prefer_ble = entry.options.get(CONF_PREFER_BLE, True)
    mow_path_fetch_enabled = entry.options.get(CONF_MOW_PATH_FETCH_ENABLED, False)

    # Default to True for older entries that predate this key, as long as they
    # have account credentials configured.
    has_cloud_account = entry.data.get(
        CONF_HAS_CLOUD_ACCOUNT, bool(account and password)
    )

    # Wire credential-save callback before login so any re-login triggered
    # during transport bind setup (e.g. _on_aliyun_auth_failure) is captured.
    if has_cloud_account:

        async def _on_credentials_updated() -> None:
            """Persist refreshed credentials to the config entry."""
            LOGGER.debug(
                "Credentials refreshed for account %s — persisting to config entry",
                account,
            )
            store_cloud_credentials(hass, entry, mammotion)

        mammotion.on_credentials_updated = _on_credentials_updated

    mammotion_mowers: list[MammotionMowerData] = []
    mammotion_devices: MammotionDevices = MammotionDevices([], [], [])
    mammotion_rtk: list[MammotionRTKData] = []
    mammotion_spino: list[MammotionSpinoData] = []

    if has_cloud_account:

        async def _on_unrecoverable_auth_error(
            account_id: str, transport_type: TransportType, _: Exception
        ) -> None:
            """Trigger HA re-authentication when the account's login itself is dead.

            pymammotion fires this only when the HTTP refresh token has been
            rejected, i.e. nothing about the account can be renewed without the
            user.  A single cloud transport failing while the login is still valid
            does NOT reach here — that only marks its own mowers unavailable.

            Raising ConfigEntryAuthFailed here would do nothing: pymammotion
            invokes this callback inside contextlib.suppress(Exception), so the
            exception is discarded and no reauth flow ever starts.  Schedule the
            flow explicitly instead.  async_start_reauth is a no-op when a reauth
            or reconfigure flow is already in progress, so repeated failures from
            several devices collapse into one prompt.

            The client is deliberately left running: pymammotion has already
            quiesced the account's cloud side (transports detached and
            disconnected, refresh scheduler stopped, HTTP failing fast), and BLE
            needs no cloud credentials — so every mower that has a BLE transport
            is switched to prefer it and nudged to connect, and keeps working while
            the user re-authenticates.
            """
            LOGGER.error(
                "Mammotion account %s: %s auth recovery exhausted — re-authentication required",
                account_id,
                transport_type.value,
            )
            # Drop the rejected credential cache now, so a restart before the user
            # completes reauth does not re-spend the dead tokens on setup.
            _clear_cached_credentials(hass, entry)
            for handle in mammotion.device_registry.all_devices:
                if handle.has_transport(TransportType.BLE):
                    handle.set_prefer_ble(value=True)
                    with suppress(TransportError):
                        await mammotion.connect_ble(handle.device_name)
            entry.async_start_reauth(hass)

        mammotion.on_unrecoverable_auth_error = _on_unrecoverable_auth_error

        async def _on_device_removed(device_name: str, iot_id: str) -> None:
            """Delete a device unbound from the account from the HA device registry.

            pymammotion fires this when a device returns 29004 ("device is unbind")
            and is no longer present on either cloud after re-discovery — i.e. it has
            been removed from the account.  Removing the HA device also removes all of
            its entities.
            """
            LOGGER.warning(
                "Mammotion device %s (iot_id=%s) unbound from account — removing from Home Assistant",
                device_name,
                iot_id,
            )
            device_registry = async_get_device_registry(hass)
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, device_name)}
            )
            if device is not None:
                device_registry.async_remove_device(device.id)

        mammotion.on_device_removed = _on_device_removed

    # BLE first: the handles exist before any cloud login, which then adopts them.
    ble_mowers = await _register_ble_devices(hass, entry, mammotion)

    cloud_available = False
    if has_cloud_account and account and password and use_wifi:
        cloud_available = await _async_attempt_login(
            hass,
            entry,
            mammotion,
            account,
            password,
            ble_fallback=bool(ble_mowers),
        )

    mower_devices: list[Device] = []
    mammotion_rtk_devices: list[Device] = []
    spino_devices: list[Device] = []
    if cloud_available:
        store_cloud_credentials(hass, entry, mammotion)
        mower_devices, mammotion_rtk_devices, spino_devices = _build_device_list(
            mammotion
        )

    # One list of mowers: the account's, plus BLE mowers the account doesn't list
    # (or every BLE mower when there is no cloud) as synthetic records.
    cloud_names = {device.device_name for device in mower_devices}
    mower_devices.extend(
        _create_ble_only_device(name) for name in ble_mowers if name not in cloud_names
    )

    for device in mower_devices:
        device_name = device.device_name
        handle = mammotion.mower(device_name)
        if handle is None:
            LOGGER.warning("Mammotion device %s was not registered — skipping", device_name)
            continue

        mammotion.set_mow_path_fetch_enabled(device_name, enabled=mow_path_fetch_enabled)

        unique_name = device_name

        maintenance_coordinator = MammotionMaintenanceUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        version_coordinator = MammotionDeviceVersionUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        report_coordinator = MammotionReportUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        map_coordinator = MammotionMapUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        error_coordinator = MammotionDeviceErrorUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )

        # The connectivity switches survive restarts; apply them before the first
        # connection attempt so a switched-off transport is never brought up.
        use_ble = report_coordinator.bluetooth_enabled and (not use_wifi or prefer_ble)
        mammotion.set_prefer_ble(device_name, prefer_ble=use_ble)
        if not use_wifi or not report_coordinator.cloud_enabled:
            for t_type in (TransportType.CLOUD_ALIYUN, TransportType.CLOUD_MAMMOTION):
                await handle.disconnect_transport(t_type)
        if not report_coordinator.bluetooth_enabled:
            await handle.remove_transport(TransportType.BLE)

        reachable = await _await_device_connection(
            mammotion, device_name, prefer_ble=use_ble
        )

        await report_coordinator.async_restore_data()
        if reachable:
            await version_coordinator.async_config_entry_first_refresh()
            await report_coordinator.async_config_entry_first_refresh()
            await maintenance_coordinator.async_config_entry_first_refresh()
            await error_coordinator.async_config_entry_first_refresh()
        else:
            # Nothing can carry a command yet (e.g. BLE-only mower out of range).
            # Best-effort refreshes that won't raise ConfigEntryNotReady: the
            # coordinators retry on their schedule and entities show unavailable
            # until the device connects.  Raising here would retry the ENTIRE
            # entry, orphaning already-registered devices and their BLE links.
            await version_coordinator.async_refresh()
            await report_coordinator.async_refresh()
            await maintenance_coordinator.async_refresh()
            await error_coordinator.async_refresh()
        await map_coordinator._async_setup()

        mammotion_mowers.append(
            MammotionMowerData(
                name=device_name,
                unique_name=unique_name,
                device=device,
                api=mammotion,
                maintenance_coordinator=maintenance_coordinator,
                reporting_coordinator=report_coordinator,
                version_coordinator=version_coordinator,
                map_coordinator=map_coordinator,
                error_coordinator=error_coordinator,
            )
        )

        if reachable:

            async def _async_refresh_map(
                _: datetime, _coordinator: MammotionMapUpdateCoordinator = map_coordinator
            ) -> None:
                """Call the debouncer at a later time."""
                await _coordinator.async_request_refresh()

            async_call_later(
                hass,
                1,
                HassJob(
                    _async_refresh_map,
                    "map-coordinator-refresh",
                    cancel_on_shutdown=True,
                ),
            )

    for rtk in mammotion_rtk_devices:
        if rtk_ble_address := addresses.get(rtk.device_name, None):
            await _attach_ble_to_rtk(
                hass,
                entry,
                mammotion,
                rtk,
                rtk_ble_address,
            )

        rtk_unique_name = rtk.device_name
        rtk_coordinator = MammotionRTKCoordinator(
            hass, entry, rtk, mammotion, unique_name=rtk_unique_name
        )
        await rtk_coordinator.async_restore_data()
        await rtk_coordinator.async_config_entry_first_refresh()
        mammotion_rtk.append(
            MammotionRTKData(
                name=rtk.device_name,
                unique_name=rtk_unique_name,
                api=mammotion,
                device=rtk,
                coordinator=rtk_coordinator,
            )
        )

    for spino in spino_devices:
        spino_unique_name = spino.device_name
        spino_coordinator = MammotionSpinoCoordinator(
            hass, entry, spino, mammotion, unique_name=spino_unique_name
        )
        await spino_coordinator.async_restore_data()
        await spino_coordinator.async_config_entry_first_refresh()
        mammotion_spino.append(
            MammotionSpinoData(
                name=spino.device_name,
                unique_name=spino_unique_name,
                api=mammotion,
                device=spino,
                coordinator=spino_coordinator,
            )
        )

    mammotion_devices.RTK = mammotion_rtk
    mammotion_devices.mowers = mammotion_mowers
    mammotion_devices.spino = mammotion_spino
    entry.runtime_data = mammotion_devices

    mammotion.setup_all_mower_watchers()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _build_device_list(
    mammotion: MammotionClient,
) -> tuple[list[Device], list[Device], list[Device]]:
    """Return (mower_devices, rtk_devices, spino_devices).

    Combines Aliyun cloud devices and Mammotion-direct devices, filters out
    unsupported device types, and separates RTK/Trackers and Spino pool cleaners.
    """
    all_devices: list[Device] = [
        *mammotion.aliyun_device_list,
        *mammotion.mammotion_device_list,
    ]
    rtk_devices: list[Device] = []
    spino_devices: list[Device] = []
    mower_devices: list[Device] = []

    for device in all_devices:
        if DeviceType.is_swimming_pool(device.device_name, device.product_key):
            spino_devices.append(device)
            continue
        if not device.device_name.startswith(DEVICE_SUPPORT):
            if device.category_key == "Tracker":
                rtk_devices.append(device)
            continue
        mower_devices.append(device)

    return mower_devices, rtk_devices, spino_devices


def _create_ble_only_device(device_name: str) -> Device:
    """Create a synthetic Device record for a BLE-only mower (no cloud account)."""
    return Device(
        gmt_modified=0,
        node_type="DEVICE",
        device_name=device_name,
        product_name=device_name,
        status=1,
        identity_id=device_name,
        net_type="BLE",
        category_key="",
        product_key="",
        is_edge_gateway=False,
        category_name="",
        identity_alias=device_name,
        iot_id="",
        bind_time=0,
        owned=1,
        thing_type="DEVICE",
    )


def store_cloud_credentials(
    hass: HomeAssistant,
    config_entry: MammotionConfigEntry,
    client: MammotionClient,
) -> None:
    """Persist cloud credentials from the client into the config entry.

    A rejected session is never persisted: ``to_cache()`` returns an empty dict
    once the account needs re-authentication, so this quietly skips (notably on
    unload, which persists credentials as a courtesy).
    """
    cache = client.to_cache()
    if not cache:
        return
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, **cache},
    )


def _load_cached_credentials(entry: MammotionConfigEntry) -> dict[str, Any]:
    """Return the entry data when it holds a usable credential cache.

    Returns the entry data when at least one credential path's sentinel
    keys are present and non-None, otherwise returns an empty dict so the
    caller knows to fall back to a full login.
    """
    data = dict(entry.data)
    has_aliyun = bool(data.get(CONF_AEP_DATA))
    has_mammotion = bool(data.get(CONF_MAMMOTION_MQTT)) and bool(
        data.get(CONF_MAMMOTION_DEVICE_RECORDS)
    )
    return data if (has_aliyun or has_mammotion) else {}


async def _async_update_listener(
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if entry.runtime_data.mowers:
            store_cloud_credentials(hass, entry, entry.runtime_data.mowers[0].api)
        for mower in entry.runtime_data.mowers:
            try:
                if handle := mower.api.mower(mower.name):
                    await handle.stop()
                mower.api.teardown_device_watchers(mower.name)
                await mower.api.remove_device(mower.name)
            except TimeoutError:
                """Do nothing as this sometimes occurs with disconnecting BLE."""

        if store := async_pop_store(hass, entry):
            await store.async_flush()
    return bool(unload_ok)


async def async_remove_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> None:
    """Remove stored data when the integration is deleted."""
    async_pop_store(hass, entry)
    await MammotionConfigStore(hass, entry.entry_id).async_remove()
    if not hass.config_entries.async_entries(DOMAIN):
        hass.data.pop(DOMAIN, None)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: MammotionConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    device_identifier = next(
        (
            identifier[1]
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN
        ),
        None,
    )
    mower = next(
        (
            mower
            for mower in config_entry.runtime_data.mowers
            if mower.unique_name == device_identifier
        ),
        None,
    )

    return not bool(mower)
