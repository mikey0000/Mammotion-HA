"""Repairs for the Mammotion integration."""

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from pymammotion.transport.base import TransportType, is_transient_network_error

from .config_flow import async_sign_in_again, signed_in_data
from .const import CONF_ACCOUNTNAME, DOMAIN, LOGGER


class CloudTransportRepairFlow(RepairsFlow):
    """Bring a given-up cloud transport back: one password-free retry, then a sign-in."""

    def __init__(self, entry_id: str, transport_type: TransportType) -> None:
        """Bind the flow to the entry and the transport it repairs."""
        self._entry_id = entry_id
        self._transport_type = transport_type
        self._placeholders: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show the issue's details on the retry step."""
        if issue := ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id):
            self._placeholders = dict(issue.translation_placeholders or {})
        return await self.async_step_retry()

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Retry the transport from the healthy login; ask for the password if refused."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_retry_transport()
            except Exception as err:  # noqa: BLE001 — only a network fault is retried here
                if not is_transient_network_error(err):
                    LOGGER.warning(
                        "Mammotion %s retry refused: %s",
                        self._transport_type.value,
                        err,
                    )
                    return await self.async_step_password()
                errors["base"] = "cannot_connect"
            else:
                # Only setup builds coordinators, for mowers the restore could not list too.
                self.hass.config_entries.async_schedule_reload(self._entry_id)
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="retry",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders=self._placeholders,
        )

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Sign in again and reload from the fresh session."""
        entry = self.hass.config_entries.async_get_known_entry(self._entry_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD].strip()
            if login := await async_sign_in_again(self.hass, entry, password, errors):
                self.hass.config_entries.async_update_entry(
                    entry, data=signed_in_data(entry, password, login)
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="password",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): cv.string}),
            errors=errors,
            description_placeholders=self._placeholders,
        )

    async def _async_retry_transport(self) -> None:
        entry = self.hass.config_entries.async_get_known_entry(self._entry_id)
        if (
            entry.state is not ConfigEntryState.LOADED
            or entry.runtime_data.client is None
        ):
            msg = "the entry is not loaded"
            raise ValueError(msg)
        await entry.runtime_data.client.retry_transport(
            entry.data[CONF_ACCOUNTNAME], self._transport_type
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the fix flow for a given-up cloud transport."""
    assert data is not None
    return CloudTransportRepairFlow(
        str(data["entry_id"]), TransportType(data["transport"])
    )
