"""A switch press is not undone by an unrelated push that lands before the device replies.

Recorder, smart charging: on at 15:06:25.125 (the press), off at .313 (area-name
frames pushed an update and ``is_on_func`` still read the old value), on at .678
(the device's ack).  The guard is on ``MammotionSwitchEntity`` itself, so every
switch built from it is covered; the entity here is the shipped class.
"""

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice
from user_command_support import make_coordinator

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.switch import (
    MammotionAsyncSwitchEntityDescription,
    MammotionSwitchEntity,
)

_MOWER = "Luba-VS123456"
_TIMEOUT = 1


def _switch(
    hass: HomeAssistant, ack: asyncio.Event, *, fail: bool = False
) -> tuple[MammotionSwitchEntity, MowingDevice]:
    """Return a rain-detection switch whose setter waits for *ack*."""
    device = MowingDevice()
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )
    coordinator.unique_name = _MOWER

    async def _set(_coordinator: object, _value: bool) -> None:
        await ack.wait()
        if fail:
            raise RuntimeError("device refused")

    entity = MammotionSwitchEntity(
        coordinator,
        MammotionAsyncSwitchEntityDescription(
            key="rain_detection",
            is_on_func=lambda c: c.data.mower_state.rain_detection,
            set_fn=_set,
        ),
    )
    entity.hass = hass
    entity.entity_id = "switch.luba_rain_detection"
    # Not added to a platform, so there is no state machine entry to write.
    entity.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    return entity, device


@pytest.mark.regression
async def test_an_unrelated_update_does_not_undo_a_switch_press_in_flight(
    hass: HomeAssistant,
) -> None:
    """The optimistic state flipped back on the push that preceded the ack."""
    ack = asyncio.Event()
    entity, _ = _switch(hass, ack)
    press = asyncio.create_task(entity.async_turn_on())
    await asyncio.sleep(0)

    try:
        entity._handle_coordinator_update()  # noqa: SLF001
        assert entity.is_on is True
    finally:
        ack.set()
        await asyncio.wait_for(press, _TIMEOUT)


async def test_updates_sync_the_switch_again_once_the_press_settles(
    hass: HomeAssistant,
) -> None:
    """The guard only holds while the press is outstanding."""
    ack = asyncio.Event()
    entity, device = _switch(hass, ack)
    press = asyncio.create_task(entity.async_turn_on())
    await asyncio.sleep(0)
    ack.set()
    await asyncio.wait_for(press, _TIMEOUT)

    device.mower_state.rain_detection = False
    entity._handle_coordinator_update()  # noqa: SLF001

    assert entity.is_on is False


async def test_a_failed_press_still_reverts_and_releases_the_guard(
    hass: HomeAssistant,
) -> None:
    """A refused press reverts, and a later update is not ignored."""
    ack = asyncio.Event()
    ack.set()
    entity, device = _switch(hass, ack, fail=True)

    with pytest.raises(RuntimeError):
        await entity.async_turn_on()
    assert entity.is_on is False

    device.mower_state.rain_detection = True
    entity._handle_coordinator_update()  # noqa: SLF001
    assert entity.is_on is True
