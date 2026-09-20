"""BLE is registered per device before any cloud login, and survives the cloud dying.

The equivalent tests in ``tests/`` loaded ``__init__.py`` against stubs and
checked the ordering invariants by reading ``async_setup_entry`` as text.  Here
the entry is really set up against a real Home Assistant, so the ordering is
observed through its effects: which coordinators hold the restored record, that
the entry loads while the devices are still connecting, and that unload
cancels the bring-up before it tears the handles down.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pymammotion.data.model.device import MowingDevice, PoolCleanerDevice
from pymammotion.transport.base import TransportError, TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import (
    _async_bring_up_devices,
    _async_bring_up_mower,
    _async_guarded,
    _await_device_connection,
    _create_ble_only_device,
    _register_ble_devices,
    async_remove_config_entry_device,
)
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)
from custom_components.mammotion.models import MammotionDevices, MammotionMowerData
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_MOWER_MAC = "aa:bb:cc:dd:ee:ff"
_FAR_MOWER = "Yuka-AB654321"
_FAR_MAC = "11:22:33:44:55:66"
_SPINO = "Spino-E1C36JT4"
_SPINO_MAC = "77:88:99:aa:bb:cc"
_RTK = "RTK-3"
_RTK_MAC = "de:ad:be:ef:00:00"
_ACCOUNT = "owner@example.com"
_ENTRY_ID = "entry-1"


def _entry(hass: HomeAssistant, ble_devices: dict[str, str]) -> MockConfigEntry:
    """Add a cloud entry that also has BLE devices configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=_ENTRY_ID,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            "password": "hunter2",
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_BLE_DEVICES: ble_devices,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _client(records: dict[str, Any] | None = None) -> MagicMock:
    """Return a MammotionClient stand-in that models the record it hands back.

    ``restore_device`` really replaces the record ``get_device_by_name`` returns,
    which is what lets the coordinator-ordering test observe the restore.
    """
    devices: dict[str, Any] = records if records is not None else {}
    handles: dict[str, MagicMock] = {}

    def _handle(name: str) -> MagicMock:
        if name not in handles:
            handle = MagicMock()
            handle.device_name = name
            handle.restore_device = MagicMock(
                side_effect=lambda record, _n=name: devices.__setitem__(_n, record)
            )
            handle.stop = AsyncMock()
            handle.remove_transport = AsyncMock()
            handle.disconnect_transport = AsyncMock()
            handles[name] = handle
        return handles[name]

    client = MagicMock()
    client.devices = devices
    client.handles = handles
    client.mower = MagicMock(side_effect=_handle)
    client.get_device_by_name = MagicMock(side_effect=devices.get)
    client.add_ble_only_device = AsyncMock(
        side_effect=lambda *, device_id, device_name, initial_device, **_kwargs: (
            devices.setdefault(device_name, initial_device)
        )
    )
    client.add_ble_to_device = AsyncMock()
    client.remove_device = AsyncMock()
    client.set_cloud_attached = AsyncMock()
    client.stop = AsyncMock()
    client.to_cache.return_value = {}
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    return client


@pytest.mark.usefixtures("enable_bluetooth")
async def test_register_ble_devices_registers_every_configured_device(
    hass: HomeAssistant,
) -> None:
    """Every BLE device becomes a per-device registration — in range or not — before login."""
    entry = _entry(
        hass,
        {
            _MOWER: _MOWER_MAC,
            _FAR_MOWER: _FAR_MAC,
            _SPINO: _SPINO_MAC,
            _RTK: _RTK_MAC,
        },
    )
    in_range = inject_advertisement(hass, _MOWER, _MOWER_MAC.upper()).device
    inject_advertisement(hass, _SPINO, _SPINO_MAC.upper())
    client = _client()

    registered = await _register_ble_devices(hass, entry, client)

    assert registered == {_MOWER: _MOWER_MAC, _FAR_MOWER: _FAR_MAC, _SPINO: _SPINO_MAC}
    calls = {
        call.kwargs["device_name"]: call.kwargs
        for call in client.add_ble_only_device.await_args_list
    }
    assert calls[_MOWER]["ble_device"] is in_range
    assert calls[_MOWER]["ble_address"] is None
    assert calls[_FAR_MOWER]["ble_device"] is None
    assert calls[_FAR_MOWER]["ble_address"] == _FAR_MAC
    # RTK base stations are cloud-discovered only.
    assert _RTK not in calls


@pytest.mark.usefixtures("enable_bluetooth")
async def test_the_initial_record_matches_the_kind_of_device(
    hass: HomeAssistant,
) -> None:
    """The handle picks its reducer from the record, so a Spino must not be fed mower state."""
    entry = _entry(hass, {_MOWER: _MOWER_MAC, _SPINO: _SPINO_MAC})
    client = _client()

    await _register_ble_devices(hass, entry, client)

    assert isinstance(client.devices[_MOWER], MowingDevice)
    assert isinstance(client.devices[_SPINO], PoolCleanerDevice)
    assert client.devices[_MOWER].mower_state.ble_mac == _MOWER_MAC
    assert client.devices[_SPINO].bt_mac == _SPINO_MAC


@pytest.mark.usefixtures("enable_bluetooth")
async def test_an_out_of_range_device_picks_up_its_ble_device_when_it_returns(
    hass: HomeAssistant,
) -> None:
    """Registration installs the reconnect callback, which is how a far mower ever connects."""
    entry = _entry(hass, {_FAR_MOWER: _FAR_MAC})
    client = _client()

    await _register_ble_devices(hass, entry, client)
    inject_advertisement(hass, _FAR_MOWER, _FAR_MAC.upper(), rssi=-70)
    await hass.async_block_till_done()

    client.add_ble_to_device.assert_awaited_once()
    assert client.add_ble_to_device.await_args.args[0] == _FAR_MOWER
    assert client.add_ble_to_device.await_args.kwargs["rssi"] == -70


async def test_await_device_connection_skips_waiting_without_a_usable_transport() -> (
    None
):
    """An out-of-range BLE-only mower must not block setup (nor raise ConfigEntryNotReady later)."""
    handle = MagicMock()
    handle.has_usable_transport = False
    handle.wait_until_connected = AsyncMock()
    client = MagicMock()
    client.mower = MagicMock(return_value=handle)

    assert await _await_device_connection(client, _MOWER, prefer_ble=True) is False
    handle.wait_until_connected.assert_not_awaited()

    handle.has_usable_transport = True
    handle.get_transport = MagicMock(return_value=None)
    handle.wait_until_connected = AsyncMock(return_value=True)
    assert await _await_device_connection(client, _MOWER, prefer_ble=True) is True
    handle.wait_until_connected.assert_awaited_once()

    # The library returns False on its 60 s timeout; that must reach the caller.
    handle.wait_until_connected = AsyncMock(return_value=False)
    assert await _await_device_connection(client, _MOWER, prefer_ble=True) is False


def _blocked_bring_up() -> tuple[Any, asyncio.Event, list[int]]:
    """Return a bring-up stand-in that blocks until released, recording its cancellation."""
    release = asyncio.Event()
    stops_at_cancel: list[int] = []
    client: dict[str, Any] = {}

    async def _bring_up(mammotion: Any, *_args: Any, **_kwargs: Any) -> None:
        client["api"] = mammotion
        try:
            await release.wait()
        except asyncio.CancelledError:
            stops_at_cancel.append(
                sum(h.stop.await_count for h in mammotion.handles.values())
            )
            raise

    return _bring_up, release, stops_at_cancel


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: MagicMock,
    bring_up: Any,
    *,
    login: bool = False,
    on_login: Any = None,
) -> None:
    """Set the entry up with the platforms and the device bring-up held back."""

    async def _attempt_login(*_args: Any, **_kwargs: Any) -> bool:
        if on_login is not None:
            on_login()
        return login

    with (
        patch("custom_components.mammotion.MammotionClient", return_value=client),
        patch("custom_components.mammotion.PLATFORMS", []),
        patch("custom_components.mammotion._async_attempt_login", _attempt_login),
        patch("custom_components.mammotion._async_bring_up_devices", bring_up),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_ble_is_registered_before_the_cloud_login_is_attempted(
    hass: HomeAssistant,
) -> None:
    """BLE needs no account, so the handles must exist before a login that may fail."""
    entry = _entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()
    bring_up, release, _ = _blocked_bring_up()
    registered_at_login: list[int] = []

    await _setup(
        hass,
        entry,
        client,
        bring_up,
        on_login=lambda: registered_at_login.append(
            client.add_ble_only_device.await_count
        ),
    )

    assert registered_at_login == [1]
    release.set()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_failed_login_leaves_the_account_configured(
    hass: HomeAssistant,
) -> None:
    """Rewriting the entry as account-less would silently strand the cloud devices."""
    entry = _entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()
    bring_up, release, _ = _blocked_bring_up()

    await _setup(hass, entry, client, bring_up, login=False)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True
    assert [mower.name for mower in entry.runtime_data.mowers] == [_MOWER]
    release.set()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_an_exhausted_account_keeps_its_ble_mowers_running(
    hass: HomeAssistant,
) -> None:
    """The cloud side is already quiesced, and BLE needs no credentials — so it carries on.

    Raising ConfigEntryAuthFailed from this callback would do nothing: pymammotion
    invokes it inside ``contextlib.suppress(Exception)``.
    """
    entry = _entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()
    client.connect_ble = AsyncMock()
    bring_up, release, _ = _blocked_bring_up()

    await _setup(hass, entry, client, bring_up)
    handle = client.mower(_MOWER)
    handle.has_transport = MagicMock(return_value=True)
    client.device_registry.all_devices = [handle]

    await client.on_unrecoverable_auth_error(
        _ACCOUNT, TransportType.CLOUD_ALIYUN, RuntimeError("refresh token rejected")
    )

    handle.set_prefer_ble.assert_called_once_with(value=True)
    client.connect_ble.assert_awaited_once_with(_MOWER)
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )

    release.set()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_finishes_while_the_devices_are_still_connecting(
    hass: HomeAssistant,
) -> None:
    """One unreachable mower must never delay the entry or the others."""
    entry = _entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()
    bring_up, release, _ = _blocked_bring_up()

    await _setup(hass, entry, client, bring_up)

    assert entry.state is ConfigEntryState.LOADED
    task = entry.runtime_data.bring_up_task
    assert task is not None
    assert not task.done()

    release.set()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_restored_record_reaches_every_coordinator(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The other coordinators copy the device record in their constructors, so restore must come first."""
    restored = MowingDevice(name=_MOWER)
    restored.mower_state.ble_mac = "99:99:99:99:99:99"
    hass_storage[f"{DOMAIN}.{_ENTRY_ID}"] = {
        "version": 1,
        "minor_version": 2,
        "key": f"{DOMAIN}.{_ENTRY_ID}",
        "data": {"devices": {_MOWER: restored.to_dict()}, "transports": {}},
    }
    entry = _entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()
    bring_up, release, _ = _blocked_bring_up()

    await _setup(hass, entry, client, bring_up)

    mower = entry.runtime_data.mowers[0]
    assert mower.reporting_coordinator.data.mower_state.ble_mac == "99:99:99:99:99:99"
    for coordinator in (
        mower.maintenance_coordinator,
        mower.version_coordinator,
        mower.map_coordinator,
        mower.error_coordinator,
    ):
        assert coordinator.data is mower.reporting_coordinator.data

    release.set()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_the_bring_up_task_before_tearing_handles_down(
    hass: HomeAssistant,
) -> None:
    """Home Assistant cancels entry tasks only after async_unload_entry, so unload must do it itself."""
    entry = _entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()
    bring_up, _release, stops_at_cancel = _blocked_bring_up()

    await _setup(hass, entry, client, bring_up)
    task = entry.runtime_data.bring_up_task

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert task.cancelled()
    # No handle had been stopped yet when the cancellation reached the bring-up.
    assert stops_at_cancel == [0]
    assert client.handles[_MOWER].stop.await_count == 1


def _mower_data(name: str, api: MagicMock) -> MammotionMowerData:
    """Build a mower record whose coordinators are stand-ins."""
    reporting = MagicMock(async_bring_up=AsyncMock())
    reporting.bluetooth_enabled = True
    reporting.cloud_enabled = True
    return MammotionMowerData(
        name=name,
        unique_name=name,
        device=_create_ble_only_device(name),
        api=api,
        maintenance_coordinator=MagicMock(async_bring_up=AsyncMock()),
        reporting_coordinator=reporting,
        version_coordinator=MagicMock(async_bring_up=AsyncMock()),
        map_coordinator=MagicMock(
            async_bring_up=AsyncMock(), async_request_refresh=AsyncMock()
        ),
        error_coordinator=MagicMock(async_bring_up=AsyncMock()),
    )


async def test_bring_up_mower_waits_then_brings_up_every_coordinator_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable mower brings up all five coordinators in order and then requests the map."""
    client = _client()
    handle = client.mower(_MOWER)
    mower = _mower_data(_MOWER, client)
    order: list[str] = []
    mower.version_coordinator.async_bring_up.side_effect = lambda: order.append(
        "version"
    )
    mower.reporting_coordinator.async_bring_up.side_effect = lambda: order.append(
        "report"
    )
    mower.map_coordinator.async_bring_up.side_effect = lambda: order.append("map")
    monkeypatch.setattr(
        "custom_components.mammotion._await_device_connection",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("custom_components.mammotion.asyncio.sleep", AsyncMock())

    await _async_bring_up_mower(client, mower, use_wifi=True, prefer_ble=True)

    client.set_prefer_ble.assert_called_once_with(_MOWER, prefer_ble=True)
    handle.remove_transport.assert_not_awaited()
    assert order == ["version", "report", "map"]
    mower.maintenance_coordinator.async_bring_up.assert_awaited_once()
    mower.error_coordinator.async_bring_up.assert_awaited_once()
    mower.map_coordinator.async_request_refresh.assert_awaited_once()


async def test_bring_up_mower_with_bluetooth_off_detaches_and_skips_map_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bluetooth off detaches the transport; an unreachable mower still refreshes but requests no map."""
    client = _client()
    handle = client.mower(_MOWER)
    mower = _mower_data(_MOWER, client)
    mower.reporting_coordinator.bluetooth_enabled = False
    monkeypatch.setattr(
        "custom_components.mammotion._await_device_connection",
        AsyncMock(return_value=False),
    )

    await _async_bring_up_mower(client, mower, use_wifi=True, prefer_ble=True)

    client.set_prefer_ble.assert_called_once_with(_MOWER, prefer_ble=False)
    handle.remove_transport.assert_awaited_once_with(TransportType.BLE)
    mower.reporting_coordinator.async_bring_up.assert_awaited_once()
    mower.map_coordinator.async_request_refresh.assert_not_awaited()


async def test_bring_up_devices_isolates_one_failing_mower(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mower whose bring-up raises is logged and must not stop the others or propagate."""
    client = _client()
    good = _mower_data("Luba-good", client)
    bad = _mower_data("Luba-bad", client)
    rtk = MagicMock()
    rtk.name = _RTK
    rtk.coordinator.async_bring_up = AsyncMock()
    devices = MammotionDevices(mowers=[bad, good], RTK=[rtk], spino=[])
    calls: list[str] = []

    async def _fake_bring_up(
        _client: MagicMock, mower: MammotionMowerData, **_kwargs: bool
    ) -> None:
        calls.append(mower.name)
        if mower is bad:
            raise TransportError("boom")

    monkeypatch.setattr(
        "custom_components.mammotion._async_bring_up_mower", _fake_bring_up
    )

    await _async_bring_up_devices(client, devices, use_wifi=True, prefer_ble=True)

    assert sorted(calls) == ["Luba-bad", "Luba-good"]
    rtk.coordinator.async_bring_up.assert_awaited_once()


async def test_guarded_logs_a_foreign_cancellation_but_propagates_our_own() -> None:
    """A cancellation raised inside a child is logged; cancelling the task itself still propagates."""

    async def cancelled_child() -> None:
        raise asyncio.CancelledError

    await _async_guarded(_MOWER, cancelled_child())

    async def slow_child() -> None:
        await asyncio.sleep(10)

    task = asyncio.ensure_future(_async_guarded(_MOWER, slow_child()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_devices_of_an_unloaded_entry_can_be_removed(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """A disabled entry has no runtime_data; its devices must still be removable from the UI."""
    entry = _entry(hass, {})
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )

    assert entry.state is not ConfigEntryState.LOADED
    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_devices_still_present_on_a_loaded_entry_are_protected(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Removing a mower the account still lists is refused; an orphaned device is allowed."""
    entry = _entry(hass, {})
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MammotionDevices(
        mowers=[_mower_data(_MOWER, _client())], RTK=[], spino=[]
    )
    present = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )
    orphan = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "Luba-gone")}
    )

    assert await async_remove_config_entry_device(hass, entry, present) is False
    assert await async_remove_config_entry_device(hass, entry, orphan) is True
