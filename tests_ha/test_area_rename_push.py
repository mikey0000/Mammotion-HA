"""Renaming an area switch in Home Assistant renames the area on the mower (#920).

The rename is a user action pushed from the entity's registry-update hook.  The
coordinator is the shipped class around a spec'd client, so a call the library's
signature rejects fails here as it does in production.
"""

import inspect
import logging
from types import SimpleNamespace

import pytest
from area_rename_support import set_area_name_pushes
from homeassistant.core import HomeAssistant
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from user_command_support import make_coordinator

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.switch import (
    MammotionConfigAreaSwitchEntity,
    MammotionConfigAreaSwitchEntityDescription,
)

#: A Luba 2, which accepts area names pushed back to it.
_MOWER = "Luba-VS123456"
_AREA = 1234


def _area_switch(hass: HomeAssistant) -> MammotionConfigAreaSwitchEntity:
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=_MOWER
    )
    coordinator.unique_name = _MOWER
    coordinator.device = _create_ble_only_device(_MOWER)
    coordinator._operation_settings = SimpleNamespace(areas=[])  # noqa: SLF001
    entity = MammotionConfigAreaSwitchEntity(
        coordinator,
        MammotionConfigAreaSwitchEntityDescription(
            key=f"{_AREA}", area=_AREA, set_fn=lambda *_: None
        ),
    )
    entity.hass = hass
    return entity


async def _rename(
    hass: HomeAssistant, entity: MammotionConfigAreaSwitchEntity, name: str
) -> None:
    """Apply a registry rename the way Home Assistant notifies the entity of one."""
    entity.registry_entry = SimpleNamespace(name=name)
    entity.async_registry_entry_updated()
    await hass.async_block_till_done()


def _pushed_names(entity: MammotionConfigAreaSwitchEntity) -> list[str]:
    return set_area_name_pushes(entity.coordinator.manager)


@pytest.mark.regression
async def test_a_rename_reaches_the_mower_with_the_new_name(
    hass: HomeAssistant,
) -> None:
    """The command's ``name`` collided with the library's device-name parameter.

    ``send_command_and_wait(name, key, ...)`` took the area name as a second
    value for ``name`` and raised TypeError inside the push task, so no rename
    ever reached the mower.
    """
    entity = _area_switch(hass)

    await _rename(hass, entity, "Back lawn")

    assert _pushed_names(entity) == ["Back lawn"]
    call = entity.coordinator.manager.send_command_and_wait.await_args
    # The spec'd mock does not enforce the signature, so bind against the real one.
    bound = inspect.signature(MammotionClient.send_command_and_wait).bind(
        None, *call.args, **call.kwargs
    )
    assert bound.arguments["name"] == _MOWER
    assert call.kwargs["hash_id"] == _AREA


@pytest.mark.regression
async def test_a_rename_the_mower_did_not_take_is_retried_and_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The name was marked pushed before the push ran.

    So a push that failed was never retried: the next registry update with that
    name looked like a no-op, and the mower kept its old name with no sign why.
    """
    entity = _area_switch(hass)
    send = entity.coordinator.manager.send_command_and_wait
    send.side_effect = DeviceOfflineException(29003, "iot-1")

    with caplog.at_level(logging.WARNING):
        await _rename(hass, entity, "Back lawn")
    assert "Back lawn" in caplog.text

    send.side_effect = None
    await _rename(hass, entity, "Back lawn")

    assert _pushed_names(entity) == ["Back lawn", "Back lawn"]


async def test_an_unrelated_registry_update_does_not_push_again(
    hass: HomeAssistant,
) -> None:
    """Once the mower took a name, an icon or area change must not resend it."""
    entity = _area_switch(hass)
    await _rename(hass, entity, "Back lawn")

    await _rename(hass, entity, "Back lawn")

    assert _pushed_names(entity) == ["Back lawn"]
