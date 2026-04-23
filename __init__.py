"""The Roku SoundBridge integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DATA_CLIENT, DOMAIN
from .protocol import RcpClient

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]

type RokuSoundBridgeConfigEntry = ConfigEntry[RcpClient]


async def async_setup_entry(hass: HomeAssistant, entry: RokuSoundBridgeConfigEntry) -> bool:
    """Set up Roku SoundBridge from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    def _update_callback() -> None:
        """Handle state updates from the client."""
        # This will be used by the media player entity to trigger async_write_ha_state
        pass

    client = RcpClient(host, port, _update_callback)
    
    # We don't await connect() here because we want the entry to be ready 
    # even if the device is currently offline, as per requirements.
    # However, we start the connection process.
    hass.async_create_task(client.connect())
    
    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: RokuSoundBridgeConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.disconnect()

    return unload_ok
