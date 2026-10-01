"""svg_update keeps the stored tile's transform for every field the call omits.

The schema once filled in svg_add's defaults (scale 1, no rotation, 2.5 m,
"pattern.svg"), so replacing a tile's artwork reset its size and rotation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import SvgFrameList, SvgMessage, SvgMessageData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import async_setup_services

_MOWER = "Luba-VS1000001"
_TILE = 987654321
_AREA = 123456789


def _setup(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, device: MowingDevice
) -> tuple[MagicMock, str]:
    coordinator = MagicMock(unique_name=_MOWER, data=device)
    coordinator.send_svg_command = AsyncMock(return_value=_TILE)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    mower = entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, _MOWER, config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return coordinator, mower


def _stored_tile() -> SvgFrameList:
    return SvgFrameList(
        total_frame=1,
        data=[
            SvgMessage(
                data_hash=_TILE,
                paternal_hash_a=_AREA,
                type=13,
                svg_message=SvgMessageData(
                    x_move=1.5,
                    y_move=-2.0,
                    scale=2.0,
                    rotate=0.5,
                    base_width_m=4.0,
                    base_height_m=3.0,
                    svg_file_name="star.svg",
                ),
            )
        ],
    )


async def _update(hass: HomeAssistant, mower: str, **fields: object) -> None:
    await hass.services.async_call(
        DOMAIN,
        "svg_update",
        {
            "entity_id": mower,
            "device_hash": _TILE,
            "area_hash": _AREA,
            "svg_data": "<svg/>",
            **fields,
        },
        blocking=True,
        return_response=True,
    )


@pytest.mark.regression
async def test_new_artwork_keeps_the_stored_transform(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Only the artwork changes."""
    device = MowingDevice()
    device.map.svg[_TILE] = _stored_tile()
    coordinator, mower = _setup(hass, entity_registry, device)

    await _update(hass, mower)

    (msg,) = coordinator.send_svg_command.await_args.args
    sent = msg.svg_message
    assert (sent.scale, sent.rotate) == (2.0, 0.5)
    assert (sent.base_width_m, sent.base_height_m) == (4.0, 3.0)
    assert (sent.x_move, sent.y_move) == (1.5, -2.0)
    assert sent.svg_file_name == "star.svg"
    assert sent.svg_file_data == "<svg/>"


async def test_given_fields_replace_the_stored_ones(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A field in the call wins over the stored tile."""
    device = MowingDevice()
    device.map.svg[_TILE] = _stored_tile()
    coordinator, mower = _setup(hass, entity_registry, device)

    await _update(hass, mower, scale=3.0, x_move=0.0)

    (msg,) = coordinator.send_svg_command.await_args.args
    assert msg.svg_message.scale == 3.0
    assert msg.svg_message.x_move == 0.0
    assert msg.svg_message.rotate == 0.5


async def test_an_unknown_tile_gets_svg_add_defaults(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """With no stored tile the library's defaults apply, as svg_add's do."""
    coordinator, mower = _setup(hass, entity_registry, MowingDevice())

    await _update(hass, mower)

    (msg,) = coordinator.send_svg_command.await_args.args
    assert (msg.svg_message.scale, msg.svg_message.rotate) == (1.0, 0.0)
    assert msg.svg_message.svg_file_name == "pattern.svg"
