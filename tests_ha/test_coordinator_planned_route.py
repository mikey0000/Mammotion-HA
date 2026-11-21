"""The route a plan produced, as ``mammotion.plan_route`` returns it.

It has to be the route of *this* plan, so the library fetches it by the plan
reply's own hash; a route that cannot be fetched is an error, never an older one.
"""

from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from user_command_support import make_coordinator

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.geojson_utils import apply_geojson_offset

_MOWER = "Luba-VS123456"
_OFFSET = (0.5, -0.25)
_PATH_HASH = 7372458040660269014
_ROUTE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[10.0, 50.0], [10.001, 50.001]],
            },
        }
    ],
}


def _coordinator(*, fetched: bool, route: dict[str, Any]) -> Any:
    """Return the real coordinator over a mower whose planned route fetch ends as given."""
    device = MowingDevice()
    device.map.generated_mow_path_geojson = route
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )
    coordinator.map_offset_lat, coordinator.map_offset_lon = _OFFSET
    coordinator.manager.fetch_planned_mow_path.return_value = (
        _PATH_HASH if fetched else 0
    )
    return coordinator


async def test_the_route_comes_back_shifted_like_the_map_it_is_drawn_on() -> None:
    """Unshifted, it would sit beside the areas of a user who corrected their map."""
    coordinator = _coordinator(fetched=True, route=_ROUTE)

    response = await coordinator.async_planned_route()

    shifted = apply_geojson_offset(_ROUTE, *_OFFSET)
    assert shifted["features"] != _ROUTE["features"]
    assert (response["type"], response["features"]) == (
        shifted["type"],
        shifted["features"],
    )
    coordinator.manager.fetch_planned_mow_path.assert_awaited_once_with(_MOWER)


async def test_the_route_carries_the_hash_that_identifies_it() -> None:
    """As text: a 64-bit number is rounded by a browser's JSON parser."""
    coordinator = _coordinator(fetched=True, route=_ROUTE)

    response = await coordinator.async_planned_route()

    assert response["path_hash"] == "7372458040660269014"


@pytest.mark.parametrize(
    ("fetched", "route"),
    [(False, _ROUTE), (True, {})],
    ids=["not fetched", "fetched but nothing to draw"],
)
async def test_a_route_that_could_not_be_fetched_is_an_error(
    fetched: bool, route: dict[str, Any]
) -> None:
    """Returning what is cached instead would pass an earlier job's route off as this plan."""
    coordinator = _coordinator(fetched=fetched, route=route)

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_planned_route()

    assert raised.value.translation_key == "planned_route_unavailable"
