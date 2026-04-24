"""Support for Roku SoundBridge media player."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import RokuSoundBridgeConfigEntry
from .const import DOMAIN
from . import protocol

SUPPORT_ROKU_SOUNDBRIDGE = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.BROWSE_MEDIA
)

REPEAT_MODE_MAP = {
    "none": RepeatMode.OFF,
    "all": RepeatMode.ALL,
    "one": RepeatMode.ONE,
}

REPEAT_MODE_MAP_REV = {v: k for k, v in REPEAT_MODE_MAP.items()}


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,
) -> None:
    """Set up the Roku SoundBridge media player platform via configuration.yaml."""
    from .protocol import RcpClient
    from homeassistant.const import CONF_HOST, CONF_PORT
    from .const import DEFAULT_PORT
    
    host = config[CONF_HOST]
    port = config.get(CONF_PORT, DEFAULT_PORT)
    
    client = RcpClient(host, port, lambda: None)
    hass.async_create_task(client.connect())
    
    async_add_entities([RokuSoundBridgeMediaPlayer(client, f"Roku SoundBridge ({host})", "manual", None)])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RokuSoundBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Roku SoundBridge media player platform."""
    client = entry.runtime_data
    async_add_entities([RokuSoundBridgeMediaPlayer(client, entry.title, entry.entry_id, entry.unique_id)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "send_command",
        {
            vol.Required("command"): cv.string,
        },
        "async_send_command",
    )


class RokuSoundBridgeMediaPlayer(MediaPlayerEntity):
    """Representation of a Roku SoundBridge media player."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, client: protocol.RcpClient, name: str, entry_id: str, unique_id: str | None) -> None:
        """Initialize the Roku SoundBridge media player."""
        self._client = client
        self._attr_unique_id = unique_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, unique_id or f"{client.host}:{client.port}")},
            "name": name,
            "manufacturer": "Roku",
            "model": "SoundBridge",
        }

    async def async_added_to_hass(self) -> None:
        """Register callback."""
        self._client.update_callback = self._update_state

    @callback
    def _update_state(self) -> None:
        """Update the state of the entity."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.is_connected

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the player."""
        if not self.available:
            return MediaPlayerState.OFF
        
        if self._client.power_state == "standby":
            return MediaPlayerState.STANDBY

        state = self._client.state
        if state == "play":
            return MediaPlayerState.PLAYING
        if state == "pause":
            return MediaPlayerState.PAUSED
        if state == "buffering":
            return MediaPlayerState.BUFFERING
        if state == "stop":
            return MediaPlayerState.IDLE
        return MediaPlayerState.IDLE

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return SUPPORT_ROKU_SOUNDBRIDGE

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._client.volume / 100.0

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        return self._client.mute

    @property
    def media_content_id(self) -> str | None:
        """Content ID of current playing media."""
        return self._client.url

    @property
    def media_title(self) -> str | None:
        """Title of current playing media."""
        return self._client.title

    @property
    def media_artist(self) -> str | None:
        """Artist of current playing media (Music track only)."""
        return self._client.artist

    @property
    def media_album_name(self) -> str | None:
        """Album name of current playing media (Music track only)."""
        return self._client.album

    @property
    def media_duration(self) -> int | None:
        """Duration of current playing media in seconds."""
        return self._client.duration

    @property
    def media_position(self) -> int | None:
        """Position of current playing media in seconds."""
        return self._client.position

    @property
    def media_position_updated_at(self) -> dt_util.datetime | None:
        """When was the position of the current playing media valid."""
        if self._client.position_updated_at is not None:
            return dt_util.utc_from_timestamp(self._client.position_updated_at)
        return None

    @property
    def media_content_id(self) -> str | None:
        """Content ID of current playing media."""
        return self._client.url

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        await self._client.set_volume(int(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        await self._client.set_mute(mute)

    async def async_media_play(self) -> None:
        """Send play command."""
        await self._client.play()

    @property
    def shuffle(self) -> bool:
        """Boolean if shuffle is enabled."""
        return self._client.shuffle

    @property
    def repeat(self) -> RepeatMode:
        """Return current repeat mode."""
        return REPEAT_MODE_MAP.get(self._client.repeat, RepeatMode.OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "display_line1": self._client.display_lines[0],
            "display_line2": self._client.display_lines[1],
            "mac_address": self._client.mac_address,
            "version": self._client.version,
            **self._client.metadata,
        }

    async def async_media_pause(self) -> None:
        """Send pause command."""
        await self._client.pause()

    async def async_media_stop(self) -> None:
        """Send stop command."""
        await self._client.stop()

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        await self._client.next()

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        await self._client.previous()

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play media."""
        if media_id.startswith("play_preset:"):
            preset_index = int(media_id.split(":")[1])
            await self._client.play_preset(preset_index)
        else:
            await self._client.play_url(media_id)

    async def async_turn_on(self) -> None:
        """Turn on the media player."""
        await self._client.turn_on()

    async def async_turn_off(self) -> None:
        """Turn off the media player."""
        await self._client.turn_off()

    async def async_toggle(self) -> None:
        """Toggle the power state."""
        if self.state == MediaPlayerState.STANDBY:
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Enable/disable shuffle mode."""
        await self._client.set_shuffle(shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set repeat mode."""
        mode = REPEAT_MODE_MAP_REV.get(repeat, "none")
        await self._client.set_repeat(mode)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        await self._client.set_mute(mute)

    async def async_send_command(self, command: str) -> None:
        """Send an arbitrary RCP command."""
        await self._client.send_ir_command(command)

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Implement the browsing of media."""
        if media_content_id is None:
            return await self._async_browse_root()
        
        if media_content_id.startswith("presets"):
            return await self._async_browse_presets()
            
        raise ValueError(f"Unknown media_content_id: {media_content_id}")

    async def _async_browse_root(self) -> BrowseMedia:
        """Browse the root."""
        children = []
        
        children.append(
            BrowseMedia(
                title="Presets",
                media_class=MediaClass.DIRECTORY,
                media_content_id="presets",
                media_content_type=MediaType.PLAYLIST,
                can_play=False,
                can_expand=True,
            )
        )
        
        return BrowseMedia(
            title="Roku SoundBridge",
            media_class=MediaClass.DIRECTORY,
            media_content_id="root",
            media_content_type=MediaType.PLAYLIST,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_browse_presets(self) -> BrowseMedia:
        """Browse presets."""
        presets = await self._client.list_presets()
        children = []
        
        for i, title in enumerate(presets):
            children.append(
                BrowseMedia(
                    title=title or f"Preset {i+1}",
                    media_class=MediaClass.MUSIC,
                    media_content_id=f"play_preset:{i}",
                    media_content_type=MediaType.MUSIC,
                    can_play=True,
                    can_expand=False,
                )
            )
            
        return BrowseMedia(
            title="Presets",
            media_class=MediaClass.DIRECTORY,
            media_content_id="presets",
            media_content_type=MediaType.PLAYLIST,
            can_play=False,
            can_expand=True,
            children=children,
        )
