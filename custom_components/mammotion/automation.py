"""Automation capabilities for the Mammotion integration."""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.script import Script
from homeassistant.helpers.service import async_call_from_config

from .const import DOMAIN

AUTOMATION_SCHEMA = {
    "trigger": list,
    "condition": list,
    "action": list,
}


class MammotionAutomation:
    """Class to handle automations for the Mammotion integration."""

    def __init__(self, hass: HomeAssistant, config: dict):
        """Initialize the automation."""
        self.hass = hass
        self.config = config
        self.script = Script(hass, config.get("action", []), DOMAIN)
        self._remove_listener = None

    async def async_enable(self):
        """Enable the automation."""
        self._remove_listener = async_track_state_change_event(
            self.hass, self.config.get("trigger", []), self._handle_trigger
        )

    async def async_disable(self):
        """Disable the automation."""
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None

    @callback
    async def _handle_trigger(self, event):
        """Handle the trigger event."""
        if all(
            await self.hass.helpers.condition.async_condition(self.config.get("condition", []))
        ):
            await self.script.async_run(context=event.context)

    async def async_update(self, config: dict):
        """Update the automation configuration."""
        await self.async_disable()
        self.config = config
        self.script = Script(self.hass, config.get("action", []), DOMAIN)
        await self.async_enable()


async def async_setup_automations(hass: HomeAssistant, config: dict):
    """Set up automations for the Mammotion integration."""
    automations = []
    for automation_config in config.get("automations", []):
        automation = MammotionAutomation(hass, automation_config)
        await automation.async_enable()
        automations.append(automation)
    return automations


async def async_unload_automations(automations):
    """Unload automations for the Mammotion integration."""
    for automation in automations:
        await automation.async_disable()
