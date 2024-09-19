import unittest
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.mammotion.automation import (
    MammotionAutomation,
    async_setup_automations,
    async_unload_automations,
)


class TestMammotionAutomation(unittest.TestCase):
    def setUp(self):
        self.hass = MagicMock(spec=HomeAssistant)
        self.config = {
            "trigger": [{"platform": "state", "entity_id": "sensor.test", "to": "on"}],
            "condition": [{"condition": "state", "entity_id": "sensor.test", "state": "on"}],
            "action": [{"service": "test.automation"}],
        }
        self.automation = MammotionAutomation(self.hass, self.config)

    @patch("custom_components.mammotion.automation.async_track_state_change_event")
    def test_async_enable(self, mock_async_track_state_change_event):
        self.hass.async_create_task(self.automation.async_enable())
        mock_async_track_state_change_event.assert_called_once()

    def test_async_disable(self):
        self.hass.async_create_task(self.automation.async_enable())
        self.hass.async_create_task(self.automation.async_disable())
        self.assertIsNone(self.automation._remove_listener)

    @patch("custom_components.mammotion.automation.Script.async_run")
    @patch("custom_components.mammotion.automation.async_track_state_change_event")
    def test_handle_trigger(self, mock_async_track_state_change_event, mock_async_run):
        self.hass.async_create_task(self.automation.async_enable())
        event = MagicMock()
        self.hass.async_create_task(self.automation._handle_trigger(event))
        mock_async_run.assert_called_once()

    def test_async_update(self):
        new_config = {
            "trigger": [{"platform": "state", "entity_id": "sensor.new_test", "to": "on"}],
            "condition": [{"condition": "state", "entity_id": "sensor.new_test", "state": "on"}],
            "action": [{"service": "test.new_automation"}],
        }
        self.hass.async_create_task(self.automation.async_update(new_config))
        self.assertEqual(self.automation.config, new_config)

    @patch("custom_components.mammotion.automation.MammotionAutomation.async_enable")
    def test_async_setup_automations(self, mock_async_enable):
        config = {
            "automations": [
                {
                    "trigger": [{"platform": "state", "entity_id": "sensor.test", "to": "on"}],
                    "condition": [{"condition": "state", "entity_id": "sensor.test", "state": "on"}],
                    "action": [{"service": "test.automation"}],
                }
            ]
        }
        automations = self.hass.async_create_task(async_setup_automations(self.hass, config))
        self.assertEqual(len(automations), 1)
        mock_async_enable.assert_called_once()

    @patch("custom_components.mammotion.automation.MammotionAutomation.async_disable")
    def test_async_unload_automations(self, mock_async_disable):
        config = {
            "automations": [
                {
                    "trigger": [{"platform": "state", "entity_id": "sensor.test", "to": "on"}],
                    "condition": [{"condition": "state", "entity_id": "sensor.test", "state": "on"}],
                    "action": [{"service": "test.automation"}],
                }
            ]
        }
        automations = self.hass.async_create_task(async_setup_automations(self.hass, config))
        self.hass.async_create_task(async_unload_automations(automations))
        mock_async_disable.assert_called_once()


if __name__ == "__main__":
    unittest.main()
