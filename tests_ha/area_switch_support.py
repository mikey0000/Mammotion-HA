"""Shared scaffolding for the area-switch tests that run against a real Home Assistant.

The stubbed versions of these tests re-implemented ``HashList.computed_areas``
and faked the entity registry, so they asserted against a mock's call log.  Here
the device map is a real :class:`MowingDevice`, the naming comes from
pymammotion's own ``computed_areas``, and entities are handed to a real
``EntityPlatform`` so the registry rows, unique_ids and states are the ones Home
Assistant would actually hold.
"""

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import AreaHashNameList, FrameList
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.switch import (
    MammotionConfigAreaSwitchEntity,
    async_add_area_entities,
)

#: A Luba 2, so the real ``DeviceType.is_luba1`` answers False as the stubbed
#: suite's patched-out check used to.
MOWER = "Luba-VS123456"
#: A first-generation Luba, which never reports area names.
LUBA1 = "Luba-AVS12345"


def area_name(name: str, hash_val: int) -> AreaHashNameList:
    """Build one entry of the device's reported area-name list."""
    return AreaHashNameList(name=name, hash=hash_val)


def make_coordinator(
    hass: HomeAssistant,
    area_hashes: list[int],
    area_names: list[AreaHashNameList],
    *,
    device_name: str = MOWER,
    unique_name: str | None = None,
) -> MagicMock:
    """Build a coordinator whose ``data`` is a real device with a real map.

    Only the coordinator itself is a stand-in: ``computed_areas``, the naming
    fallback and every registry interaction below are the production ones.
    """
    device = MowingDevice()
    device.map.area = {h: FrameList() for h in area_hashes}
    device.map.area_name = list(area_names)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = device
    coordinator.device_name = device_name
    coordinator.unique_name = unique_name or device_name
    coordinator.operation_settings.areas = []
    coordinator.async_get_area_list = AsyncMock()
    # No Aliyun binding, so device_info falls back to identifiers and a name —
    # the device registry rejects the MagicMock fields the full card would carry.
    coordinator.manager.get_device_by_name.return_value = None
    return coordinator


def set_map(
    coordinator: MagicMock,
    area_hashes: list[int],
    area_names: list[AreaHashNameList],
) -> None:
    """Replace what the device reports, as a fresh map frame would."""
    coordinator.data.map.area = {h: FrameList() for h in area_hashes}
    coordinator.data.map.area_name = list(area_names)


class AreaSwitches:
    """Drives ``async_add_area_entities`` over a real entity platform.

    ``sync`` is one map-update cycle.  ``register`` then hands whatever it
    created to Home Assistant, which is what gives the entities a ``hass``, an
    ``entity_id``, a registry row and a state — the conditions the stubbed
    tests had to fake by assigning ``entity.hass = MagicMock()``.
    """

    def __init__(self, hass: HomeAssistant, coordinator: MagicMock) -> None:
        """Start a session with empty tracking state, as platform setup does."""
        self.hass = hass
        self.coordinator = coordinator
        self.added_areas: set[int] = set()
        self.by_name: dict[str, MammotionConfigAreaSwitchEntity] = {}
        self.added: list[MammotionConfigAreaSwitchEntity] = []
        self._pending: list[MammotionConfigAreaSwitchEntity] = []
        self.config_entry = MockConfigEntry(domain=DOMAIN)
        self.config_entry.add_to_hass(hass)
        self.platform = MockEntityPlatform(hass, domain="switch", platform_name=DOMAIN)
        self.platform.config_entry = self.config_entry

    def sync(self) -> list[MammotionConfigAreaSwitchEntity]:
        """Run one update cycle; returns only the entities created by it."""
        new: list[MammotionConfigAreaSwitchEntity] = []
        async_add_area_entities(
            self.coordinator, self.added_areas, self.by_name, new.extend
        )
        self.added.extend(new)
        self._pending.extend(new)
        return new

    async def register(self) -> None:
        """Add the entities created since the last call to Home Assistant."""
        pending, self._pending = self._pending, []
        if pending:
            await self.platform.async_add_entities(pending)

    async def sync_live(self) -> list[MammotionConfigAreaSwitchEntity]:
        """One update cycle with the new entities registered straight away."""
        new = self.sync()
        await self.register()
        return new

    def entity_id_for(self, area_hash: int) -> str | None:
        """Resolve the registry row an area hash currently owns."""
        return er.async_get(self.hass).async_get_entity_id(
            "switch", DOMAIN, f"{self.coordinator.unique_name}_{area_hash}"
        )
