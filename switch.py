"""Switch platform for the Roku SoundBridge: mirror-display toggle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MIRROR_ENABLED, DOMAIN

if TYPE_CHECKING:
    from . import SoundBridgeRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SoundBridge switch entities."""
    async_add_entities([MirrorDisplaySwitch(entry)])


class MirrorDisplaySwitch(SwitchEntity):
    """Toggle mirror-display mode on/off.

    The switch state is persisted as a config-entry option so it survives
    restarts. Flipping the switch reconfigures the running mirror controller
    via the entry's update listener.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mirror_display"
    _attr_icon = "mdi:monitor-share"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_mirror_display"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.unique_id or entry.entry_id)},
        }

    @property
    def is_on(self) -> bool:
        """Mirror enabled flag from the entry's options."""
        return bool(self._entry.options.get(CONF_MIRROR_ENABLED, False))

    @property
    def available(self) -> bool:
        """Switch is available whenever a mirror source is configured."""
        runtime: SoundBridgeRuntimeData = self._entry.runtime_data
        return runtime.mirror.source_entity_id is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Persist enabled=True and let the update listener apply it."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Persist enabled=False and let the update listener apply it."""
        await self._set_enabled(False)

    async def _set_enabled(self, value: bool) -> None:
        new_options = dict(self._entry.options)
        new_options[CONF_MIRROR_ENABLED] = value
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_options
        )
        self.async_write_ha_state()
