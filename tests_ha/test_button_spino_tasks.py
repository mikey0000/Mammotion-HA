"""The per-schedule Spino task buttons follow their plan's name.

The coordinator is the shipped class built by its own ``__init__`` and the buttons
are registered on a real entity platform, so the names asserted are the registry's.
"""

from collections.abc import AsyncIterator
from functools import partial
from unittest.mock import create_autospec

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.data.model.pool_state import PoolPlan
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.button import async_add_spino_task_entities
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionSpinoCoordinator

_CLEANER = "Spino-E1C36JT4"
_JOBID = 4242


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
) -> AsyncIterator[MammotionSpinoCoordinator]:
    """Build a Spino coordinator holding one plan; shut it down after."""
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = None
    manager.to_cache.return_value = {}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=_CLEANER)
    entry.add_to_hass(hass)
    coordinator = MammotionSpinoCoordinator(
        hass, entry, _create_ble_only_device(_CLEANER), manager, unique_name=_CLEANER
    )
    cleaner = PoolCleanerDevice()
    cleaner.plans = {_JOBID: PoolPlan(jobid=_JOBID, jobname="Morning")}
    coordinator.async_set_updated_data(cleaner)
    yield coordinator
    await coordinator.async_shutdown()


@pytest.mark.regression
async def test_a_renamed_plan_renames_its_task_button(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    coordinator: MammotionSpinoCoordinator,
) -> None:
    """The button's description took the new name but its cached name did not.

    ``update_name`` replaced ``entity_description``; Home Assistant kept rendering
    the cached ``Entity.name``, so the registry's ``original_name`` stayed on the
    plan's old name.
    """
    platform = MockEntityPlatform(hass, domain="button", platform_name=DOMAIN)
    platform.config_entry = coordinator.config_entry
    await platform.platform_data.async_load_translations()
    added: list[Entity] = []
    sync = partial(async_add_spino_task_entities, coordinator, set(), {}, added.extend)
    sync()
    await platform.async_add_entities(added)
    coordinator.async_add_listener(sync)
    (button,) = added
    assert entity_registry.async_get(button.entity_id).original_name == "Morning", (
        "premise: the button starts on the plan's old name"
    )

    renamed = PoolCleanerDevice()
    renamed.plans = {_JOBID: PoolPlan(jobid=_JOBID, jobname="Evening")}
    coordinator.async_set_updated_data(renamed)
    await hass.async_block_till_done()

    assert entity_registry.async_get(button.entity_id).original_name == "Evening"
