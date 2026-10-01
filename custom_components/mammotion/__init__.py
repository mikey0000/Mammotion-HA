"""The Mammotion integration."""

from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import Coroutine
from contextlib import suppress
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any, cast

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.device_registry import (
    async_get as async_get_device_registry,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_integration
from pymammotion.aliyun.exceptions import CloudSetupError, TooManyRequestsException
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice, PoolCleanerDevice
from pymammotion.http.model.http import UnauthorizedExceptionError
from pymammotion.transport.base import (
    AuthError,
    LoginFailedError,
    TransportError,
    TransportRateLimitedError,
    TransportType,
    is_transient_network_error,
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
    BLE_SUPPORT,
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_BLE_DEVICES,
    CONF_CONNECT_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MAMMOTION_DEVICE_RECORDS,
    CONF_MAMMOTION_MQTT,
    CONF_MOW_PATH_FETCH_ENABLED,
    CONF_NOTIFY,
    CONF_PREFER_BLE,
    CONF_STAY_CONNECTED_BLUETOOTH,
    CONF_USE_WIFI,
    CREDENTIAL_CACHE_KEYS,
    DEVICE_SUPPORT,
    DOMAIN,
    LOGGER,
    NOTIFY_SELF_CHECK,
    NOTIFY_WARNINGS,
    POOL_CLEANER_SUPPORT,
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
from .entity import async_remove_retired_entities
from .error_codes import async_preload_error_codes
from .models import (
    MammotionDevices,
    MammotionMowerData,
    MammotionRTKData,
    MammotionSpinoData,
)
from .notifications import MowerNotifier
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


CLOUD_LOGIN_RETRY_INTERVAL = timedelta(minutes=15)
CLOUD_LOGIN_MAX_RETRIES = 5


class _LoginFailure(Enum):
    """How a failed cloud login is answered."""

    REJECTED = auto()
    """The account's login is dead: reauthenticate, never retry."""
    TRANSIENT = auto()
    """The server was unreachable or one transport failed: retry from the cache."""
    FAILED = auto()
    """Anything else: show it and leave it to a reload."""


def _classify_login_failure(
    err: Exception, mammotion: MammotionClient
) -> _LoginFailure:
    """Classify *err*; only ``reauth_required`` makes an auth error terminal.

    A transport-scoped give-up raises the same ``ReLoginRequiredError`` while the
    login stays valid, so the exception type alone cannot decide it.
    """
    if isinstance(err, LoginFailedError) or mammotion.reauth_required is not None:
        return _LoginFailure.REJECTED
    if isinstance(err, UnretryableException):
        # Tea keeps the cause in inner_exception, not __cause__.
        inner = err.inner_exception
        if isinstance(inner, Exception) and is_transient_network_error(inner):
            return _LoginFailure.TRANSIENT
        return _LoginFailure.FAILED
    if isinstance(
        err,
        (
            AuthError,
            UnauthorizedExceptionError,
            CloudSetupError,
            TooManyRequestsException,
            TransportRateLimitedError,
        ),
    ) or is_transient_network_error(err):
        return _LoginFailure.TRANSIENT
    return _LoginFailure.FAILED


class _CloudLogin:
    """The entry's cloud login: one attempt at setup, then bounded cached-session retries.

    Setup never raises ``ConfigEntryNotReady`` for a login: Home Assistant would
    retry it every 5…600 s with no end, and without a cache every retry is a
    password grant.  A retry only ever calls ``restore_credentials`` with the
    cache; a success reloads the entry, since only setup builds the cloud devices.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MammotionConfigEntry,
        mammotion: MammotionClient,
        account: str,
        password: str,
    ) -> None:
        """Bind the login to its entry and client."""
        self._hass = hass
        self._entry = entry
        self._mammotion = mammotion
        self._account = account
        self._password = password
        self._failed_retries = 0
        self._cancel_retry: CALLBACK_TYPE | None = None
        self._restored_cache: dict[str, Any] = {}

    @property
    def _issue_id(self) -> str:
        return f"cloud_login_{self._entry.entry_id}"

    def _load_cache(self) -> dict[str, Any]:
        cached = _load_cached_credentials(self._entry)
        self._restored_cache = {
            k: cached[k] for k in CREDENTIAL_CACHE_KEYS if k in cached
        }
        return cached

    @callback
    def _async_clear_own_cache(self) -> bool:
        """Clear the cache only while every blob in it is one this client restored or wrote.

        Returns False, clearing nothing, when a flow has saved a fresh login since:
        this client is then stale and its rejection must not wipe that.
        """
        written = _WRITTEN_CACHES.get(self._mammotion, {})
        if not all(
            value is self._restored_cache.get(key) or value is written.get(key)
            for key in CREDENTIAL_CACHE_KEYS
            if (value := self._entry.data.get(key)) is not None
        ):
            return False
        _clear_cached_credentials(self._hass, self._entry)
        return True

    async def async_setup_login(self, *, ble_fallback: bool) -> bool:
        """Log in once; return True when the cloud is up.

        Raises ConfigEntryAuthFailed only for a dead login with no BLE mower to
        keep the entry useful.
        """
        session = aiohttp_client.async_get_clientsession(self._hass)
        cached = self._load_cache()
        try:
            if cached:
                await self._mammotion.restore_credentials(
                    self._account,
                    self._password,
                    cached,
                    session,
                    check_for_new_devices=True,
                )
            else:
                await self._mammotion.login_and_initiate_cloud(
                    self._account, self._password, session
                )
        except Exception as err:  # noqa: BLE001 — every failure is classified below
            outcome = _classify_login_failure(err, self._mammotion)
            if outcome is _LoginFailure.REJECTED and not ble_fallback:
                self._async_clear_own_cache()
                raise ConfigEntryAuthFailed(err) from err
            self._async_failed(err, outcome, can_retry=bool(cached))
            return False
        return True

    @callback
    def async_rejected(self) -> None:
        """Stop retrying, drop the dead cache and ask the user to reauthenticate."""
        self._async_cancel_timer()
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)
        if not self._async_clear_own_cache():
            LOGGER.debug(
                "Mammotion %s: stale session rejected after a flow saved a fresh "
                "login; no reauth needed",
                self._account,
            )
            return
        self._entry.async_start_reauth(self._hass)

    @callback
    def async_cancel(self) -> None:
        """Cancel a pending retry and clear the repair (on unload)."""
        self._async_cancel_timer()
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)

    @callback
    def _async_cancel_timer(self) -> None:
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None

    @callback
    def _async_failed(
        self, err: Exception, outcome: _LoginFailure, *, can_retry: bool
    ) -> None:
        if outcome is _LoginFailure.REJECTED:
            LOGGER.warning(
                "Mammotion login for %s was rejected; continuing in BLE-only mode "
                "until re-authenticated: %s",
                self._account,
                err,
            )
            self.async_rejected()
            return
        retrying = (
            outcome is _LoginFailure.TRANSIENT
            and can_retry
            and self._failed_retries < CLOUD_LOGIN_MAX_RETRIES
        )
        if outcome is _LoginFailure.FAILED:
            LOGGER.error(
                "Mammotion cloud login for %s failed", self._account, exc_info=err
            )
        else:
            LOGGER.warning(
                "Mammotion cloud is unavailable for %s (%s); %s",
                self._account,
                err,
                f"retrying in {CLOUD_LOGIN_RETRY_INTERVAL}"
                if retrying
                else "not retrying until the integration is reloaded",
            )
        key, severity = (
            ("cloud_login_retrying", ir.IssueSeverity.WARNING)
            if retrying
            else ("cloud_login_failed", ir.IssueSeverity.ERROR)
        )
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=severity,
            translation_key=key,
            translation_placeholders={
                "account": self._account,
                "error": str(err) or type(err).__name__,
            },
        )
        if retrying:
            self._cancel_retry = async_call_later(
                self._hass, CLOUD_LOGIN_RETRY_INTERVAL, self._async_retry_due
            )

    @callback
    def _async_retry_due(self, _now: datetime) -> None:
        self._cancel_retry = None
        # An entry task, so unloading cancels a restore still in flight.
        self._entry.async_create_background_task(
            self._hass,
            self._async_retry(),
            name=f"{DOMAIN}_cloud_login_retry_{self._entry.entry_id}",
        )

    async def _async_retry(self) -> None:
        if not (cached := self._load_cache()):
            # Only a password grant could follow, and retries never make one.
            LOGGER.debug("Mammotion cloud retry skipped: no cached session left")
            return
        try:
            await self._mammotion.restore_credentials(
                self._account,
                self._password,
                cached,
                aiohttp_client.async_get_clientsession(self._hass),
                check_for_new_devices=True,
            )
        except Exception as err:  # noqa: BLE001 — every failure is classified below
            self._failed_retries += 1
            self._async_failed(
                err, _classify_login_failure(err, self._mammotion), can_retry=True
            )
            return
        LOGGER.info(
            "Mammotion cloud is reachable again for %s; reloading to bring it up",
            self._account,
        )
        store_cloud_credentials(self._hass, self._entry, self._mammotion)
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)
        self._hass.config_entries.async_schedule_reload(self._entry.entry_id)


async def _async_attempt_login(login: _CloudLogin, *, ble_fallback: bool) -> bool:
    """Attempt the setup's one cloud login; True when the cloud is up."""
    return await login.async_setup_login(ble_fallback=ble_fallback)


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
        if not device_name.startswith(BLE_SUPPORT):
            continue
        is_pool_cleaner = device_name.startswith(POOL_CLEANER_SUPPORT)
        ble_device = bluetooth.async_ble_device_from_address(
            hass, ble_address.upper(), True
        )
        if ble_device is None:
            LOGGER.info(
                "BLE device %s (%s) not in range at startup — registering and waiting",
                device_name,
                ble_address,
            )
        # The handle picks its reducer from the device name, so the initial
        # state object has to match or a Spino would be fed mower state.
        await mammotion.add_ble_only_device(
            device_id=device_name,
            device_name=device_name,
            initial_device=PoolCleanerDevice(name=device_name)
            if is_pool_cleaner
            else MowingDevice(name=device_name),
            ble_device=ble_device,
            ble_address=None if ble_device is not None else ble_address,
        )
        if (device := mammotion.get_device_by_name(device_name)) is not None:
            if is_pool_cleaner:
                cast(PoolCleanerDevice, device).bt_mac = ble_address
            else:
                device.mower_state.ble_mac = ble_address
        _register_ble_reconnect_callback(
            hass, entry, mammotion, device_name, ble_address
        )
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
        #
        # The RSSI has to travel with it: BLETransport.is_usable fails closed below
        # min_rssi, and only a stronger reading reopens it.  This callback is the one
        # that always runs, so dropping the RSSI here left a mower that faded out of
        # range unusable no matter how strongly it came back.
        hass.async_create_task(
            mammotion.add_ble_to_device(
                device_name, service_info.device, rssi=service_info.rssi
            )
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
    return await handle.wait_until_connected(timeout=60, mqtt_stable_for=10)


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

    if entry.version == 1 and entry.minor_version < 3:
        # Persistent notifications became opt-in; keep them on for existing users.
        options = dict(entry.options)
        options.setdefault(CONF_NOTIFY, [NOTIFY_WARNINGS])
        hass.config_entries.async_update_entry(
            entry, options=options, version=1, minor_version=3
        )

    if entry.version == 1 and entry.minor_version < 4:
        # Self-check notifications are on by default; a list saved before the
        # category existed cannot have turned it off, so add it.
        options = dict(entry.options)
        if (notify := options.get(CONF_NOTIFY)) is not None and (
            NOTIFY_SELF_CHECK not in notify
        ):
            options[CONF_NOTIFY] = [*notify, NOTIFY_SELF_CHECK]
        hass.config_entries.async_update_entry(
            entry, options=options, version=1, minor_version=4
        )

    return True


def _track_account_in_use(
    hass: HomeAssistant, entry: MammotionConfigEntry, mammotion: MammotionClient
) -> None:
    """Show a repair while the Mammotion app holds the account's cloud lock.

    pymammotion retries the Aliyun transport on its own and the login stays valid,
    so this is a hint for the user, not a reauth.
    """
    held: set[str] = set()

    async def _on_account_in_use_changed(account_id: str, in_use: bool) -> None:
        issue_id = f"account_in_use_{account_id}"
        if in_use:
            held.add(account_id)
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="account_in_use",
                translation_placeholders={"account": account_id},
            )
        else:
            held.discard(account_id)
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    @callback
    def _clear_issues() -> None:
        for account_id in held:
            ir.async_delete_issue(hass, DOMAIN, f"account_in_use_{account_id}")
        held.clear()

    mammotion.on_account_in_use_changed = _on_account_in_use_changed
    entry.async_on_unload(_clear_issues)


async def async_setup_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:  # noqa: C901
    """Set up Mammotion from a config entry.

    Blocks only on the store, BLE registration and the cloud login.  Coordinators
    are built from restored data and every device round-trip runs afterwards in a
    background task, so one unreachable mower never delays the others or the entry.
    """
    started = time.monotonic()
    addresses = entry.data.get(CONF_BLE_DEVICES, {})
    integration = await async_get_integration(hass, DOMAIN)
    mammotion = MammotionClient(ha_version=integration.version.split("-")[0])

    store = async_get_store(hass, entry)
    await store.async_load_device_data()
    await async_preload_error_codes(hass)

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

    cloud_login = _CloudLogin(hass, entry, mammotion, account or "", password or "")
    entry.async_on_unload(cloud_login.async_cancel)

    if has_cloud_account:

        async def _on_unrecoverable_auth_error(
            account_id: str, transport_type: TransportType, _: Exception
        ) -> None:
            """Trigger HA re-authentication when the account's login itself is dead.

            pymammotion fires this only when the HTTP refresh token has been
            rejected; a single transport failing on a valid login does not reach
            here.  It runs inside the library's quiesce under suppress(Exception),
            so raising ConfigEntryAuthFailed would be discarded: start the flow
            explicitly, first, and leave the BLE connects to background tasks.
            The client keeps running so mowers with BLE carry on meanwhile.
            """
            LOGGER.error(
                "Mammotion account %s: %s auth recovery exhausted — re-authentication required",
                account_id,
                transport_type.value,
            )
            cloud_login.async_rejected()
            for handle in mammotion.device_registry.all_devices:
                if handle.has_transport(TransportType.BLE):
                    handle.set_prefer_ble(value=True)
                    entry.async_create_background_task(
                        hass,
                        _async_connect_ble_after_cloud_loss(
                            mammotion, handle.device_name
                        ),
                        name=f"{DOMAIN}_ble_after_cloud_loss_{handle.device_name}",
                    )

        mammotion.on_unrecoverable_auth_error = _on_unrecoverable_auth_error
        _track_account_in_use(hass, entry, mammotion)

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
            cloud_login, ble_fallback=bool(ble_mowers)
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
        _create_ble_only_device(name)
        for name in ble_mowers
        if name not in cloud_names and not name.startswith(POOL_CLEANER_SUPPORT)
    )
    # A BLE-only pool cleaner belongs on the Spino path; left in mower_devices it
    # would be handed mower coordinators and a lawn_mower entity.
    spino_cloud_names = {device.device_name for device in spino_devices}
    spino_devices.extend(
        _create_ble_only_device(name)
        for name in ble_mowers
        if name not in spino_cloud_names and name.startswith(POOL_CLEANER_SUPPORT)
    )

    for device in mower_devices:
        device_name = device.device_name
        handle = mammotion.mower(device_name)
        if handle is None:
            LOGGER.warning(
                "Mammotion device %s was not registered — skipping", device_name
            )
            continue

        mammotion.set_mow_path_fetch_enabled(
            device_name, enabled=mow_path_fetch_enabled
        )

        unique_name = device_name

        # Restore before the other coordinators are built: their constructors copy
        # the device record, so entities show last-known values straight away.
        report_coordinator = MammotionReportUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        await report_coordinator.async_restore_data()
        maintenance_coordinator = MammotionMaintenanceUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        version_coordinator = MammotionDeviceVersionUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        map_coordinator = MammotionMapUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )
        error_coordinator = MammotionDeviceErrorUpdateCoordinator(
            hass, entry, device, mammotion, unique_name=unique_name
        )

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
                notifier=MowerNotifier(hass, report_coordinator),
            )
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
    for mower in mammotion_mowers:
        entry.async_on_unload(mower.notifier.async_start())

    async_remove_retired_entities(hass, mammotion_mowers)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Unload cancels it explicitly (Home Assistant cancels entry tasks only after
    # async_unload_entry has already torn the device handles down).
    mammotion_devices.bring_up_task = entry.async_create_background_task(
        hass,
        _async_bring_up_devices(
            mammotion, mammotion_devices, use_wifi=use_wifi, prefer_ble=prefer_ble
        ),
        name=f"{DOMAIN}_bring_up_{entry.entry_id}",
    )
    LOGGER.debug(
        "Setup of %s blocked for %.1fs; devices connect in the background",
        entry.title,
        time.monotonic() - started,
    )

    return True


async def _async_bring_up_devices(
    mammotion: MammotionClient,
    devices: MammotionDevices,
    *,
    use_wifi: bool,
    prefer_ble: bool,
) -> None:
    """Connect every device and run its first refreshes, concurrently across devices."""
    await asyncio.gather(
        *(
            _async_guarded(
                mower.name,
                _async_bring_up_mower(
                    mammotion, mower, use_wifi=use_wifi, prefer_ble=prefer_ble
                ),
            )
            for mower in devices.mowers
        ),
        *(
            _async_guarded(rtk.name, rtk.coordinator.async_bring_up())
            for rtk in devices.RTK
        ),
        *(
            _async_guarded(spino.name, spino.coordinator.async_bring_up())
            for spino in devices.spino
        ),
    )


async def _async_connect_ble_after_cloud_loss(
    mammotion: MammotionClient, device_name: str
) -> None:
    """Nudge one mower onto BLE; a failure is logged, the next advertisement retries."""
    try:
        await mammotion.connect_ble(device_name)
    except Exception as exc:  # noqa: BLE001 — one mower must not take the others down
        LOGGER.debug("%s: BLE connect after cloud loss failed: %s", device_name, exc)


async def _async_guarded(name: str, coro: Coroutine[Any, Any, None]) -> None:
    """Run one device's bring-up; a failure is logged and never reaches its siblings.

    A cancellation that is not ours (bleak_retry_connector raises CancelledError
    when no BLE slot is free) is logged too, so an aborted bring-up is visible.
    """
    try:
        await coro
    except asyncio.CancelledError:
        if (task := asyncio.current_task()) is not None and task.cancelling():
            raise
        LOGGER.warning("%s: bring-up cancelled, entities stay on restored data", name)
    except Exception as exc:  # noqa: BLE001 — one device must not take the others down
        LOGGER.warning(
            "%s: bring-up failed, entities stay on restored data: %s",
            name,
            exc,
            exc_info=exc,
        )


async def _async_bring_up_mower(
    mammotion: MammotionClient,
    mower: MammotionMowerData,
    *,
    use_wifi: bool,
    prefer_ble: bool,
) -> None:
    """Apply the stored transport switches, wait for a link, then bring each coordinator up.

    Coordinators run sequentially within a mower because they share one command
    queue and the cloud send quota.  ``async_bring_up`` never raises: an
    unreachable mower keeps its restored data, and the start-up reads retry on each
    refresh until they complete.
    """
    device_name = mower.name
    handle = mammotion.mower(device_name)
    if handle is None:
        return
    report_coordinator = mower.reporting_coordinator

    # The connectivity switches survive restarts; apply them before the first
    # connection attempt so a switched-off transport is never brought up.
    use_ble = report_coordinator.bluetooth_enabled and (not use_wifi or prefer_ble)
    mammotion.set_prefer_ble(device_name, prefer_ble=use_ble)
    if not use_wifi or not report_coordinator.cloud_enabled:
        await mammotion.set_cloud_attached(device_name, attached=False)
    if not report_coordinator.bluetooth_enabled:
        await handle.remove_transport(TransportType.BLE)

    reachable = await _await_device_connection(
        mammotion, device_name, prefer_ble=use_ble
    )
    if not reachable:
        LOGGER.debug(
            "%s: no transport reachable yet; entities fill in once it connects",
            device_name,
        )

    for coordinator in (
        mower.version_coordinator,
        report_coordinator,
        mower.maintenance_coordinator,
        mower.error_coordinator,
        mower.map_coordinator,
    ):
        await coordinator.async_bring_up()

    if reachable:
        # Let the first report land before the (heavy) map fetch is requested.
        await asyncio.sleep(1)
        await mower.map_coordinator.async_request_refresh()


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
        device_type = DeviceType.value_of_str(device.device_name, device.product_key)
        # is_swimming_pool() also claims SD_PX, the PC210's charging pile, which has
        # none of the cleaner state the Spino platform entities read.
        if device_type is not DeviceType.SD_PX and DeviceType.is_swimming_pool(
            device.device_name, device.product_key
        ):
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


#: The cache each client last wrote, to tell its own writes from a flow's.
_WRITTEN_CACHES: weakref.WeakKeyDictionary[MammotionClient, dict[str, Any]] = (
    weakref.WeakKeyDictionary()
)


def store_cloud_credentials(
    hass: HomeAssistant,
    config_entry: MammotionConfigEntry,
    client: MammotionClient,
) -> None:
    """Persist *client*'s credential cache into the entry, if the entry is still its own.

    Skipped when the session is rejected (``to_cache()`` is empty), when the entry
    has no cloud account or names another account, and once a reauth or
    reconfigure flow has replaced what this client last wrote: the client then
    holds another account's session, or one the flow's login replaced server-side.
    """
    data = config_entry.data
    if not data.get(
        CONF_HAS_CLOUD_ACCOUNT,
        bool(data.get(CONF_ACCOUNTNAME) and data.get(CONF_PASSWORD)),
    ):
        return
    if (expected := data.get(CONF_ACCOUNT_ID)) is not None and (
        (http := client.mammotion_http) is not None
        and http.login_info is not None
        and http.login_info.userInformation.userAccount != str(expected)
    ):
        return
    # A cleared key is refilled; one holding someone else's value is not ours.
    if (written := _WRITTEN_CACHES.get(client)) is not None and any(
        data.get(key) is not None and data.get(key) is not value
        for key, value in written.items()
    ):
        return
    if not (cache := client.to_cache()):
        return
    hass.config_entries.async_update_entry(config_entry, data={**data, **cache})
    _WRITTEN_CACHES[client] = dict(cache)


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
    if (task := entry.runtime_data.bring_up_task) is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    # No credential write-back: every refresh already persisted its rotation through
    # on_credentials_updated, and a flow that reloads saved a newer cache first.
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
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
    """Remove a config entry from a device.

    A disabled or otherwise unloaded entry has no runtime data and nothing to
    protect, so its devices can always be removed.
    """
    if config_entry.state is not ConfigEntryState.LOADED:
        return True
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
