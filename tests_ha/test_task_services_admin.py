"""Task services that change a device's stored schedules need an admin.

Reading (``get_tasks``, ``refresh_tasks``) and running one (``start_task``) do not.
"""

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import async_setup_services

_TASK = "button.mammotion_luba_task"


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("create_task", {"name": "Front"}),
        ("edit_task", {"name": "Front"}),
        ("rename_task", {"name": "Front"}),
        ("set_task_enabled", {"enabled": False}),
        ("delete_task", {}),
        ("copy_task", {}),
    ],
)
async def test_the_schedule_writing_services_need_an_admin(
    hass: HomeAssistant, hass_read_only_user: MockUser, service: str, data: dict
) -> None:
    """The permission check runs before the handler looks for the task at all."""
    async_setup_services(hass)

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": _TASK, **data},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


@pytest.mark.parametrize(
    ("service", "response"),
    [("get_tasks", True), ("refresh_tasks", False), ("start_task", False)],
)
async def test_reading_and_starting_do_not_need_an_admin(
    hass: HomeAssistant, hass_read_only_user: MockUser, service: str, response: bool
) -> None:
    """A read-only user gets past the permission check (the task is simply unknown)."""
    async_setup_services(hass)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": _TASK},
            blocking=True,
            return_response=response,
            context=Context(user_id=hass_read_only_user.id),
        )

    assert not isinstance(err.value, Unauthorized)
