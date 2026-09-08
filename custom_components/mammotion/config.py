"""Config for Mammotion."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from pymammotion.http.model.http import ErrorInfo

from .const import DOMAIN

# Device state is only read back at startup, so it does not need to hit the disk
# on every poll. The state is kept in memory and written at most once per this
# many seconds, plus a flush on unload and on the final-write event at shutdown.
SAVE_DELAY = 300

STORE_DATA_KEY = f"{DOMAIN}_store"

STORE_VERSION = 1
STORE_MINOR_VERSION = 2

TRANSPORT_BLUETOOTH = "bluetooth_enabled"
TRANSPORT_CLOUD = "cloud_enabled"

LEGACY_STORAGE_VERSION = 1
LEGACY_STORAGE_MINOR_VERSION = 2


class MammotionConfigStore(Store):  # type: ignore[misc]
    """Store the device state of a single config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store for a config entry."""
        super().__init__(
            hass,
            version=STORE_VERSION,
            minor_version=STORE_MINOR_VERSION,
            key=f"{DOMAIN}.{entry_id}",
        )
        # In-memory state of the entry's devices, keyed by device name
        self.device_data: dict[str, Any] = {}
        # Connectivity switch positions per device, keyed by device name
        self.transport_settings: dict[str, dict[str, bool]] = {}
        self._save_pending = False

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Nest the flat device map so transport settings get their own section."""
        if old_major_version == 1 and old_minor_version < 2:
            return {"devices": old_data, "transports": {}}
        return old_data

    async def async_load_device_data(self) -> None:
        """Load the persisted device state and transport settings into memory."""
        data = await self.async_load() or {}
        self.device_data = data.get("devices", {})
        self.transport_settings = data.get("transports", {})

    def transport_enabled(self, device_name: str, transport: str) -> bool:
        """Return the stored switch position of a device's transport, on by default."""
        return self.transport_settings.get(device_name, {}).get(transport, True)

    async def async_set_transport_enabled(
        self, device_name: str, transport: str, enabled: bool
    ) -> None:
        """Persist a connectivity switch position right away; toggles are rare."""
        self.transport_settings.setdefault(device_name, {})[transport] = enabled
        await self.async_save(self._data_to_save())

    async def async_device_data(self, device_name: str) -> dict[str, Any] | None:
        """Return the stored state of a device, migrating any legacy store."""
        if (data := self.device_data.get(device_name)) is not None:
            return data

        legacy_store = MammotionLegacyDeviceStore(self.hass, device_name)
        if (legacy_data := await legacy_store.async_load()) is None:
            return None

        await legacy_store.async_remove()
        self.async_update_device_data(device_name, legacy_data)
        return legacy_data

    @callback
    def async_update_device_data(self, device_name: str, data: dict[str, Any]) -> None:
        """Update a device in memory, writing to disk at most once per SAVE_DELAY."""
        if self.device_data.get(device_name) == data:
            return
        self.device_data[device_name] = data
        # A pending write keeps its own deadline: async_delay_save would push the
        # write back on every call and never fire while polling continues.
        if self._save_pending:
            return
        self._save_pending = True
        self.async_delay_save(self._data_to_save, SAVE_DELAY)

    def _data_to_save(self) -> dict[str, Any]:
        """Return a snapshot to persist; runs in the executor thread."""
        self._save_pending = False
        return {
            "devices": dict(self.device_data),
            "transports": dict(self.transport_settings),
        }

    async def async_flush(self) -> None:
        """Write queued device state to disk, cancelling the delayed write."""
        if not self._save_pending:
            return
        await self.async_save(self._data_to_save())

    async def async_remove_device(self, device_name: str) -> None:
        """Drop the stored state and transport settings of a single device."""
        had_data = self.device_data.pop(device_name, None) is not None
        had_settings = self.transport_settings.pop(device_name, None) is not None
        if not (had_data or had_settings):
            return
        self._save_pending = True
        await self.async_flush()


class MammotionLegacyDeviceStore(Store):  # type: ignore[misc]
    """Per-device store, kept to migrate its data into the config entry store."""

    def __init__(self, hass: HomeAssistant, device_name: str) -> None:
        """Initialize the legacy store of a device."""
        super().__init__(
            hass,
            version=LEGACY_STORAGE_VERSION,
            minor_version=LEGACY_STORAGE_MINOR_VERSION,
            key=device_name,
        )

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Migrate configuration to the new version."""
        if old_major_version < 2 and old_minor_version < 2:
            old_data["errors"] = {
                "error_codes": {},
                "err_code_list": [],
                "err_code_list_time": [],
            }
            error_codes: dict[str, ErrorInfo] | None = old_data.get("error_codes")
            err_code_list: list[Any] | None = old_data.get("err_code_list")
            err_code_list_time: list[Any] | None = old_data.get("err_code_list_time")
            if error_codes is not None:
                old_data["errors"]["error_codes"] = old_data["error_codes"]
                del old_data["error_codes"]
            if err_code_list is not None:
                old_data["errors"]["err_code_list"] = old_data["err_code_list"]
                del old_data["err_code_list"]
            if err_code_list_time is not None:
                old_data["errors"]["err_code_list_time"] = old_data[
                    "err_code_list_time"
                ]
                del old_data["err_code_list_time"]

        return old_data


@callback
def async_get_store(hass: HomeAssistant, entry: ConfigEntry) -> MammotionConfigStore:
    """Return the store shared by every coordinator of a config entry."""
    stores: dict[str, MammotionConfigStore] = hass.data.setdefault(STORE_DATA_KEY, {})
    if (store := stores.get(entry.entry_id)) is None:
        store = stores[entry.entry_id] = MammotionConfigStore(hass, entry.entry_id)
    return store


@callback
def async_pop_store(
    hass: HomeAssistant, entry: ConfigEntry
) -> MammotionConfigStore | None:
    """Remove and return the store of a config entry."""
    stores: dict[str, MammotionConfigStore] = hass.data.get(STORE_DATA_KEY, {})
    return stores.pop(entry.entry_id, None)
