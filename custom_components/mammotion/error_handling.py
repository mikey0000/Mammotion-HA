"""Error handling for the Mammotion integration."""

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER

class MammotionErrorHandling:
    """Class to handle errors for the Mammotion integration."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the error handling."""
        self.hass = hass

    def handle_error(self, error: Exception, context: str = ""):
        """Handle different types of errors."""
        if isinstance(error, HomeAssistantError):
            self._handle_home_assistant_error(error, context)
        else:
            self._handle_generic_error(error, context)

    def _handle_home_assistant_error(self, error: HomeAssistantError, context: str):
        """Handle Home Assistant specific errors."""
        LOGGER.error("Home Assistant Error in %s: %s", context, str(error))
        self.hass.components.persistent_notification.create(
            f"Home Assistant Error in {context}: {str(error)}",
            title="Mammotion Integration Error",
            notification_id="mammotion_error",
        )

    def _handle_generic_error(self, error: Exception, context: str):
        """Handle generic errors."""
        LOGGER.error("Error in %s: %s", context, str(error))
        self.hass.components.persistent_notification.create(
            f"Error in {context}: {str(error)}",
            title="Mammotion Integration Error",
            notification_id="mammotion_error",
        )
