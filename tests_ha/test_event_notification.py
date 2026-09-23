"""The notification event entity, against the real EventEntity.

The entity only relays what the mower's notifier hands it; decoding, persistent
notifications and bus events are the notifier's (see test_notifications.py).
"""

from pathlib import Path
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.mammotion.event import MammotionNotificationEventEntity
from custom_components.mammotion.notifications import MowerNotifier


async def _added_entity(
    hass: HomeAssistant,
) -> tuple[MammotionNotificationEventEntity, MowerNotifier]:
    coordinator = MagicMock()
    coordinator.device_name = "Luba-1"
    notifier = MowerNotifier(hass, coordinator)
    entity = MammotionNotificationEventEntity(coordinator, notifier)
    entity.coordinator = coordinator
    entity.hass = hass
    entity.entity_id = "event.luba_1_notification"
    entity.async_write_ha_state = MagicMock()
    await entity.async_added_to_hass()
    return entity, notifier


async def test_a_notification_triggers_the_event_with_its_attributes(
    hass: HomeAssistant,
) -> None:
    """What the notifier hands over becomes the entity's event, then its state is written."""
    entity, notifier = await _added_entity(hass)

    for listener in notifier._listeners:  # noqa: SLF001
        listener("device_warning_code_event", {"codes": [{"code": 2801}]})

    attributes = entity.state_attributes
    assert attributes["event_type"] == "device_warning_code_event"
    assert attributes["codes"] == [{"code": 2801}]
    entity.async_write_ha_state.assert_called_once()


async def test_the_listener_is_removed_with_the_entity(hass: HomeAssistant) -> None:
    """Removing the entity detaches it from the notifier."""
    entity, notifier = await _added_entity(hass)

    for remove in entity._on_remove:  # noqa: SLF001
        remove()

    assert notifier._listeners == []  # noqa: SLF001


def test_event_platform_is_registered() -> None:
    """The event platform is in the integration's platform list."""
    src = (
        Path(__file__).parent.parent / "custom_components" / "mammotion" / "__init__.py"
    ).read_text()
    assert "Platform.EVENT," in src


def test_options_flow_offers_notify_categories() -> None:
    """The options flow exposes every notification category."""
    src = (
        Path(__file__).parent.parent
        / "custom_components"
        / "mammotion"
        / "config_flow.py"
    ).read_text()
    assert "vol.Optional(CONF_NOTIFY, default=self.notify): SelectSelector(" in src
    assert "options=list(NOTIFY_CATEGORIES)" in src
    assert "translation_key=CONF_NOTIFY" in src
