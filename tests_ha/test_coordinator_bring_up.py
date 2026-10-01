"""Coordinator set-up wires everything first and retries the start-up reads.

``async_bring_up`` marked itself done before ``_async_setup`` ran, and the
start-up reads marked themselves done before they were sent, so anything that
escaped them left a loaded coordinator with no ``sys_status`` watch (the report
coordinator wired it after the reads) and settings that were never read again
until a reload (#915 family).  The ``watch_field`` subscriptions were also never
stored, so a reload left the old coordinator's handlers firing.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.messaging.command_queue import Priority
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionDeviceErrorUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_NAME = "Luba-VS1000001"

_Factory = Callable[..., Awaitable[tuple[Any, Any]]]


@pytest.fixture
async def make_coordinator(hass: HomeAssistant) -> AsyncIterator[_Factory]:
    """Build real coordinators over a spec'd client and handle; shut them down after."""
    built: list[MammotionBaseUpdateCoordinator[Any]] = []
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-1",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_BLE_DEVICES: {_NAME: "aa:bb:cc:dd:ee:ff"}},
    )
    entry.add_to_hass(hass)
    await async_get_store(hass, entry).async_load_device_data()

    async def _make(
        cls: type[MammotionBaseUpdateCoordinator[Any]], *, usable: bool = True
    ) -> tuple[Any, Any]:
        device = MowingDevice()
        device.online = True
        device.enabled = True
        handle = create_autospec(DeviceHandle, instance=True)
        handle.has_usable_transport = usable
        manager = create_autospec(MammotionClient, instance=True)
        manager.get_device_by_name.return_value = device
        manager.mower.return_value = handle
        coordinator = cls(
            hass, entry, _create_ble_only_device(_NAME), manager, unique_name=_NAME
        )
        built.append(coordinator)
        return coordinator, handle

    yield _make
    for coordinator in built:
        await coordinator.async_shutdown()


def _escaping_reads(coordinator: Any, failures: int) -> AsyncMock:
    """Make the coordinator's start-up reads raise *failures* times, then succeed."""
    reads = AsyncMock(side_effect=[RuntimeError("boom")] * failures + [None] * 5)
    coordinator._async_startup_reads = reads
    return reads


@pytest.mark.regression
async def test_an_escaping_read_still_leaves_the_sys_status_watch(
    make_coordinator: _Factory,
) -> None:
    """The watch is what refreshes the status on every mode change."""
    coordinator, handle = await make_coordinator(MammotionReportUpdateCoordinator)
    _escaping_reads(coordinator, failures=1)

    await coordinator._async_setup()

    (getter, _handler), _ = handle.watch_field.call_args
    device = MowingDevice()
    device.report_data.dev.sys_status = 13
    assert getter(SimpleNamespace(raw=device)) == 13


@pytest.mark.regression
async def test_reads_that_escaped_run_again_on_the_next_refresh(
    make_coordinator: _Factory,
) -> None:
    """Done is recorded only once the reads have completed."""
    coordinator, _ = await make_coordinator(MammotionMaintenanceUpdateCoordinator)
    reads = _escaping_reads(coordinator, failures=1)

    await coordinator._async_setup()
    await coordinator._async_ensure_startup_reads()
    await coordinator._async_ensure_startup_reads()

    assert reads.await_count == 2


async def test_reads_that_keep_escaping_are_given_up(
    make_coordinator: _Factory,
) -> None:
    """A read that fails every time must not resend the batch on every refresh."""
    coordinator, _ = await make_coordinator(MammotionMaintenanceUpdateCoordinator)
    reads = _escaping_reads(coordinator, failures=10)

    for _ in range(6):
        await coordinator._async_ensure_startup_reads()

    assert reads.await_count == 3


async def test_reads_wait_until_the_mower_is_reachable(
    make_coordinator: _Factory,
) -> None:
    """Sent with no transport they would all be dropped and never asked again."""
    coordinator, handle = await make_coordinator(
        MammotionMaintenanceUpdateCoordinator, usable=False
    )
    reads = _escaping_reads(coordinator, failures=0)

    await coordinator._async_setup()
    assert reads.await_count == 0

    handle.has_usable_transport = True
    await coordinator._async_ensure_startup_reads()
    await coordinator._async_ensure_startup_reads()
    assert reads.await_count == 1


@pytest.mark.regression
async def test_a_failed_bring_up_is_retried(make_coordinator: _Factory) -> None:
    """A setup that raised has not been done, so the next bring-up runs it again."""
    coordinator, _ = await make_coordinator(MammotionMaintenanceUpdateCoordinator)
    setup = AsyncMock(side_effect=[RuntimeError("boom"), None])
    coordinator._async_setup = setup
    coordinator.async_refresh = AsyncMock()

    await coordinator.async_bring_up()
    await coordinator.async_bring_up()
    await coordinator.async_bring_up()

    assert setup.await_count == 2
    assert coordinator.async_refresh.await_count == 2


@pytest.mark.regression
@pytest.mark.parametrize(
    "cls",
    [
        MammotionReportUpdateCoordinator,
        MammotionMaintenanceUpdateCoordinator,
        MammotionDeviceErrorUpdateCoordinator,
    ],
)
async def test_the_sys_status_watch_ends_with_the_coordinator(
    make_coordinator: _Factory, cls: type[MammotionBaseUpdateCoordinator[Any]]
) -> None:
    """An unload must stop the old coordinator's handler."""
    coordinator, handle = await make_coordinator(cls)
    coordinator._startup_reads_done = True
    await coordinator._async_setup()

    await coordinator.async_shutdown()

    handle.watch_field.return_value.cancel.assert_called()


@pytest.mark.regression
async def test_the_map_setup_dock_read_is_a_background_send(
    make_coordinator: _Factory,
) -> None:
    """A failed background read is logged; as a user-priority send it raised out of setup."""
    coordinator, _ = await make_coordinator(MammotionMapUpdateCoordinator)
    coordinator.manager.send_command_and_wait.side_effect = DeviceOfflineException(
        29003, "dev-1"
    )

    await coordinator._async_setup()

    (call,) = coordinator.manager.send_command_and_wait.await_args_list
    assert not call.kwargs["priority"].is_direct
    assert call.kwargs["priority"] is not Priority.USER
