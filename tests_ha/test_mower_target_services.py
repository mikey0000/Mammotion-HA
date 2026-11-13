"""Map services called the ways their callers do — a UI target (a list) or the cards' plain string.

``mammotion-svg-card`` (ha-mammotion-svg-pick-n-place) calls ``get_map_data`` and the
``svg_*`` services with ``entity_id`` as a string in the service data.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.services import async_setup_services

_MOWER = "Luba-VS1000001"


@pytest.fixture
async def mower(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> tuple[str, MammotionReportUpdateCoordinator]:
    """Register a mower whose coordinator holds an empty map."""
    async_setup_services(hass)
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    coordinator.device_name = _MOWER
    coordinator.unique_name = _MOWER
    coordinator.data = MowingDevice()
    coordinator.send_svg_command = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    entity_id = entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, _MOWER, config_entry=entry
    ).entity_id
    return entity_id, coordinator


@pytest.mark.parametrize("as_list", [False, True], ids=["card string", "ui target"])
async def test_get_map_data_accepts_both_entity_id_forms(
    hass: HomeAssistant,
    mower: tuple[str, MammotionReportUpdateCoordinator],
    as_list: bool,
) -> None:
    """Both call shapes reach the mower's map."""
    entity_id, _ = mower

    response = await hass.services.async_call(
        DOMAIN,
        "get_map_data",
        {"entity_id": [entity_id] if as_list else entity_id},
        blocking=True,
        return_response=True,
    )

    assert response == {"area": {}, "svg": {}, "area_name": []}


@pytest.mark.parametrize("as_list", [False, True], ids=["card string", "ui target"])
async def test_svg_delete_accepts_both_entity_id_forms(
    hass: HomeAssistant,
    mower: tuple[str, MammotionReportUpdateCoordinator],
    as_list: bool,
) -> None:
    """The card's delete call keeps working after the move to a target."""
    entity_id, coordinator = mower

    await hass.services.async_call(
        DOMAIN,
        "svg_delete",
        {
            "entity_id": [entity_id] if as_list else entity_id,
            "device_hash": "123",
            "area_hash": "456",
        },
        blocking=True,
        return_response=True,
    )

    coordinator.send_svg_command.assert_awaited_once()


@pytest.mark.regression
@pytest.mark.usefixtures("mower")
async def test_get_geojson_skips_an_entry_that_is_not_loaded(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A Mammotion entry that is not loaded has no runtime_data (#844).

    The mower lookup read it on every entry, so an entry left behind by a failed
    setup or a replaced account raised AttributeError and get_geojson answered
    with a 500 instead of the translated error.
    """
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entity_id = entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, "Luba-VS1000002", config_entry=entry
    ).entity_id

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "get_geojson",
            {"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )

    assert err.value.translation_key == "mower_not_found"
