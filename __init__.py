"""The Roku SoundBridge integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MIRROR_ENABLED,
    CONF_MIRROR_IDLE_SECONDS,
    CONF_MIRROR_SOURCE,
    DEFAULT_MIRROR_IDLE_SECONDS,
)
from .mirror import MirrorDisplayController
from .protocol import RcpClient

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SWITCH]


@dataclass
class SoundBridgeRuntimeData:
    """Per-config-entry runtime objects."""

    client: RcpClient
    mirror: MirrorDisplayController
    # Set by the media_player platform once its entity is added, so the
    # mirror controller can detect "I am the universal MP's active child".
    own_entity_id: str | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Roku SoundBridge from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    client = RcpClient(host, port, lambda: None)
    hass.async_create_task(client.connect())

    runtime = SoundBridgeRuntimeData(
        client=client,
        mirror=MirrorDisplayController(
            hass, client, lambda: entry.runtime_data.own_entity_id
        ),
    )
    entry.runtime_data = runtime

    _apply_mirror_options(entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start mirror tracking after platforms are loaded (so own_entity_id is set).
    if entry.options.get(CONF_MIRROR_ENABLED, False):
        await runtime.mirror.set_enabled(True)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: SoundBridgeRuntimeData = entry.runtime_data
        await runtime.mirror.stop()
        await runtime.client.disconnect()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-apply mirror options after a settings change."""
    runtime: SoundBridgeRuntimeData = entry.runtime_data
    _apply_mirror_options(entry)
    await runtime.mirror.set_enabled(
        bool(entry.options.get(CONF_MIRROR_ENABLED, False))
    )


def _apply_mirror_options(entry: ConfigEntry) -> None:
    """Push current options into the mirror controller (no enable/disable)."""
    runtime: SoundBridgeRuntimeData = entry.runtime_data
    runtime.mirror.configure(
        source_entity_id=entry.options.get(CONF_MIRROR_SOURCE) or None,
        idle_seconds=int(
            entry.options.get(CONF_MIRROR_IDLE_SECONDS, DEFAULT_MIRROR_IDLE_SECONDS)
        ),
    )
