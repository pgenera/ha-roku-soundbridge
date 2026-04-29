"""Config flow for Roku SoundBridge integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import protocol
from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    host = data[CONF_HOST]
    port = data[CONF_PORT]

    client = protocol.RcpClient(host, port, lambda: None)
    if not await client.connect():
        raise CannotConnect

    # Wait a bit for the mac address to be retrieved
    for _ in range(10):
        if client.mac_address:
            break
        await asyncio.sleep(0.5)

    mac = client.mac_address
    await client.disconnect()

    if not mac:
        raise CannotConnect

    return {"title": f"SoundBridge ({host})", "unique_id": mac}


class RokuSoundBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Roku SoundBridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Unknown error: %s", err, exc_info=err)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
