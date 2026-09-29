"""Setup drops registry rows for mower entities the integration no longer creates.

A row whose entity is never created again lingers as "unavailable" forever.  The
registry here is Home Assistant's real one, seeded as an earlier version left it.
"""

from battery_support import make_mower
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.entity import async_remove_retired_entities

LUBA_3 = "Luba-VAME9R5S"
LUBA_2 = "Luba-VS563L6H"


def _seed(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    return er.async_get(hass).async_get_or_create(domain, DOMAIN, unique_id).entity_id


def _present(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    registry = er.async_get(hass)
    return [entity_id for entity_id in entity_ids if registry.async_get(entity_id)]


async def test_the_luba_3_s_vision_sensors_are_removed(hass: HomeAssistant) -> None:
    """Earlier versions made them on every Luba 2+; the Luba 3 is not a vision device."""
    stale = [
        _seed(hass, SENSOR_DOMAIN, f"{LUBA_3}_visual_positioning_status"),
        _seed(hass, SENSOR_DOMAIN, f"{LUBA_3}_camera_brightness"),
    ]
    live = [
        _seed(hass, SENSOR_DOMAIN, f"{LUBA_3}_battery_percent"),
        _seed(hass, SENSOR_DOMAIN, f"{LUBA_3}_lidar_positioning_status"),
    ]

    async_remove_retired_entities(hass, [make_mower(LUBA_3)])

    assert _present(hass, stale) == []
    assert _present(hass, live) == live


async def test_a_vision_device_keeps_its_vision_sensors(hass: HomeAssistant) -> None:
    """The cleanup is scoped to devices the app gives no vision row."""
    kept = [
        _seed(hass, SENSOR_DOMAIN, f"{LUBA_2}_visual_positioning_status"),
        _seed(hass, SENSOR_DOMAIN, f"{LUBA_2}_camera_brightness"),
    ]

    async_remove_retired_entities(hass, [make_mower(LUBA_2)])

    assert _present(hass, kept) == kept


async def test_only_the_named_mower_s_rows_are_touched(hass: HomeAssistant) -> None:
    """The unique_id is per mower, so another Luba 3's rows are its own setup's business."""
    other = _seed(hass, SENSOR_DOMAIN, "Luba-VA6ABCDE_visual_positioning_status")

    async_remove_retired_entities(hass, [make_mower(LUBA_3)])

    assert _present(hass, [other]) == [other]


async def test_the_retired_rain_tactics_switch_is_removed(hass: HomeAssistant) -> None:
    """The plan-level switch was dropped; the device-level rain switch stays."""
    stale = _seed(hass, SWITCH_DOMAIN, f"{LUBA_2}_rain_tactics")
    live = _seed(hass, SWITCH_DOMAIN, f"{LUBA_2}_rain_detection")

    async_remove_retired_entities(hass, [make_mower(LUBA_2)])

    assert _present(hass, [stale, live]) == [live]
