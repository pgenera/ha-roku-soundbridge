"""Config flow for Roku SoundBridge integration."""

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_PORT, DOMAIN
from . import protocol


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    host = data[CONF_HOST]
    port = data[CONF_PORT]

    # To validate, we try to connect once.
    client = protocol.RcpClient(host, port, lambda: None)
    if not await client.connect():
        raise CannotConnect

    # Wait a bit for the mac address to be retrieved
    import asyncio
    for _ in range(10):
        if client.mac_address:
            break
        await asyncio.sleep(0.1)

    mac_address = client.mac_address
    await client.disconnect()

    # Return info that you want to store in the config entry.
    return {
        "title": f"Roku SoundBridge ({host})",
        "mac_address": mac_address,
    }


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
                import logging
                logging.getLogger(__name__).error("Unknown error: %s", err, exc_info=err)
                errors["base"] = "unknown"
            else:
                unique_id = info.get("mac_address") or f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                await self.async_set_unique_id(unique_id)
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
