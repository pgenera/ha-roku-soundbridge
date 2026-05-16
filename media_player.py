"""Support for Roku SoundBridge media player."""

from __future__ import annotations

from dataclasses import dataclass
import io
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote, unquote

from PIL import Image
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import protocol
from .const import DEFAULT_PORT, DOMAIN
from .display import (
    bitmap_to_lines,
    render_icon_to_commands,
    render_text_to_commands,
)
from .mdi_mapping import MDI_NAME_TO_CODEPOINT
_LOGGER = logging.getLogger(__name__)

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

# Categorical browse modes. Values become the second segment of a server
# browse path: servers/<i>/<category>[/<name>[/<track_index>]].
_CATEGORIES = ("albums", "artists", "genres", "playlists", "songs")

# Some firmware variants return unset preset slots as the literal string
# "Preset 11", "Preset 12", etc. — surface only "real" presets.
_EMPTY_PRESET_RE = re.compile(r"^\s*Preset\s+\d+\s*$", re.IGNORECASE)


def _is_empty_preset(title: str | None) -> bool:
    if not title or not title.strip():
        return True
    return bool(_EMPTY_PRESET_RE.match(title))


@dataclass
class _ServerNav:
    """Parsed parts of a servers/... browse path."""

    server_index: int
    category: str | None = None
    item_name: str | None = None
    track_index: int | None = None


def _encode(name: str) -> str:
    """URL-encode a path segment; preserves readability for typical names."""
    return quote(name, safe="")


def _decode(seg: str) -> str:
    """Decode a previously _encode()-d segment."""
    return unquote(seg)


def _build_song_listing(
    title: str,
    content_id: str,
    song_titles: list[str],
    make_track_id,
) -> BrowseMedia:
    """Build a BrowseMedia directory whose children are playable song leaves."""
    children = [
        BrowseMedia(
            title=name or f"Track {i + 1}",
            media_class=MediaClass.TRACK,
            media_content_id=make_track_id(i),
            media_content_type=MediaType.MUSIC,
            can_play=True,
            can_expand=False,
        )
        for i, name in enumerate(song_titles)
    ]
    return BrowseMedia(
        title=title,
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type=MediaType.PLAYLIST,
        can_play=False,
        can_expand=True,
        children=children,
    )


def _parse_server_path(media_id: str) -> _ServerNav | None:
    """Parse a servers/... path into its parts, or return None if malformed.

    Examples:
        servers/0                              -> server root
        servers/0/albums                       -> list of albums
        servers/0/albums/Some%20Album          -> songs in album
        servers/0/albums/Some%20Album/3        -> play track 3 in album
        servers/0/songs/12                     -> play track 12 from all songs
    """
    parts = media_id.split("/")
    if len(parts) < 2 or parts[0] != "servers":
        return None
    try:
        server_index = int(parts[1])
    except ValueError:
        return None
    nav = _ServerNav(server_index=server_index)
    if len(parts) >= 3:
        category = parts[2]
        if category not in _CATEGORIES:
            return None
        nav.category = category
    if len(parts) >= 4:
        # For "songs", parts[3] is a track index, not an item name
        if nav.category == "songs":
            try:
                nav.track_index = int(parts[3])
            except ValueError:
                return None
        else:
            nav.item_name = _decode(parts[3])
    if len(parts) >= 5 and nav.category != "songs":
        try:
            nav.track_index = int(parts[4])
        except ValueError:
            return None
    return nav


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,
) -> None:
    """Set up the Roku SoundBridge media player platform via configuration.yaml."""
    host = config[CONF_HOST]
    port = config.get(CONF_PORT, DEFAULT_PORT)

    client = protocol.RcpClient(host, port, lambda: None)
    hass.async_create_task(client.connect())

    async_add_entities(
        [
            RokuSoundBridgeMediaPlayer(
                client, f"Roku SoundBridge ({host})", "manual", None
            )
        ]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Roku SoundBridge media player platform."""
    client = entry.runtime_data
    async_add_entities(
        [
            RokuSoundBridgeMediaPlayer(
                client, entry.title, entry.entry_id, entry.unique_id
            )
        ]
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "send_command",
        {
            vol.Required("command"): cv.string,
        },
        "async_send_command",
    )
    platform.async_register_entity_service(
        "draw",
        {
            vol.Optional("clear", default=True): cv.boolean,
            vol.Required("items"): vol.All(
                cv.ensure_list,
                [
                    vol.Schema(
                        {
                            vol.Required("type"): vol.In(
                                [
                                    "text",
                                    "icon",
                                    "rect",
                                    "line",
                                ]
                            ),
                            # Fields for 'text' and 'icon'
                            vol.Optional("text"): cv.string,
                            vol.Optional("icon"): cv.string,
                            vol.Optional("size", default=32): cv.positive_int,
                            vol.Optional("font_path"): cv.string,
                            vol.Optional("anchor"): cv.string,
                            vol.Optional("font_index"): cv.positive_int,
                            # Common fields
                            vol.Optional("x"): vol.Any(
                                cv.positive_int, vol.In(["c"])
                            ),
                            vol.Optional("y"): vol.Any(
                                cv.positive_int, vol.In(["c"])
                            ),
                            vol.Optional("x1"): cv.positive_int,
                            vol.Optional("y1"): cv.positive_int,
                            vol.Optional("x2"): cv.positive_int,
                            vol.Optional("y2"): cv.positive_int,
                            vol.Optional("w"): cv.positive_int,
                            vol.Optional("h"): cv.positive_int,
                            vol.Optional("filled", default=False): cv.boolean,
                        }
                    )
                ],
            ),
        },
        "async_draw",
    )
    platform.async_register_entity_service(
        "play_preset",
        {
            vol.Required("preset"): cv.string,
        },
        "async_play_preset",
    )
    platform.async_register_entity_service(
        "clear_display",
        {},
        "async_clear_display",
    )
    platform.async_register_entity_service(
        "sketch_command",
        {
            vol.Required("command"): cv.string,
        },
        "async_sketch_command",
    )


class RokuSoundBridgeMediaPlayer(MediaPlayerEntity):
    """Representation of a Roku SoundBridge media player."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC

    def __init__(
        self,
        client: protocol.RcpClient,
        name: str,
        entry_id: str,
        unique_id: str | None,
    ) -> None:
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
            return MediaPlayerState.OFF

        state = self._client.state
        if state == "play":
            return MediaPlayerState.PLAYING
        if state == "pause":
            return MediaPlayerState.PAUSED
        if state == "buffering":
            return MediaPlayerState.BUFFERING
        if state == "stop":
            return MediaPlayerState.IDLE
        return MediaPlayerState.ON

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
        """Play media.

        Supported media_id forms:
          play_preset:<index>             play user preset by zero-based index (0..17)
          servers/<i>/<cat>/<name>/<n>    play song <n> on server <i> filtered by
                                          category <cat> and item <name>; <cat> is
                                          one of albums, artists, genres, playlists
          servers/<i>/songs/<n>           play song <n> from the unfiltered song list
          connect_server:<i>              (legacy) connect to server <i>
          <url>                           play an arbitrary URL via PlayStation
        """
        if media_id.startswith("play_preset:"):
            preset_index = int(media_id.split(":", 1)[1])
            await self._client.play_preset(preset_index)
            return
        if media_id.startswith("connect_server:"):
            server_index = int(media_id.split(":", 1)[1])
            await self._client.connect_server(server_index)
            return
        if media_id.startswith("servers/"):
            await self._async_play_server_track(media_id)
            return
        await self._client.play_url(media_id)

    async def _async_play_server_track(self, media_id: str) -> None:
        """Resolve a server/<i>/<cat>/<name>/<index> path and play it.

        Re-runs the listing right before PlayIndex so the SoundBridge's "last
        list result" matches what we computed the index against — the device
        only keeps one list at a time and any other command (or another HA
        browser session) could have clobbered it.
        """
        nav = _parse_server_path(media_id)
        if nav is None or nav.track_index is None:
            raise ValueError(f"Not a playable server path: {media_id}")
        await self._client.connect_server(nav.server_index)
        await self._refresh_filtered_song_list(nav.category, nav.item_name)
        await self._client.play_index(nav.track_index)

    async def _refresh_filtered_song_list(
        self, category: str | None, item_name: str | None
    ) -> list[str]:
        """Apply browse filters and run the list command matching the path.

        Returns the resulting list of song titles, and leaves the SoundBridge's
        "last list result" populated so a subsequent PlayIndex picks the right
        song.
        """
        if category in (None, "songs"):
            return await self._client.list_songs()
        if category == "albums":
            await self._client.set_browse_filter_album(item_name or "")
            return await self._client.list_songs()
        if category == "artists":
            await self._client.set_browse_filter_artist(item_name or "")
            return await self._client.list_songs()
        if category == "genres":
            await self._client.set_browse_filter_genre(item_name or "")
            return await self._client.list_songs()
        if category == "playlists":
            # Playlist contents are addressed by playlist index, not name —
            # the index comes from the last ListPlaylists result, so we re-run
            # ListPlaylists to populate the SoundBridge's list, then map name
            # to index, then ListPlaylistSongs <index>.
            playlists = await self._client.list_playlists()
            try:
                idx = playlists.index(item_name) if item_name else 0
            except ValueError:
                _LOGGER.warning(
                    "Playlist %r not found in current ListPlaylists result", item_name
                )
                idx = 0
            return await self._client.list_playlist_songs(idx)
        raise ValueError(f"Unknown browse category: {category!r}")

    async def async_turn_on(self) -> None:
        """Turn on the media player."""
        await self._client.turn_on()

    async def async_turn_off(self) -> None:
        """Turn off the media player."""
        await self._client.turn_off()

    async def async_toggle(self) -> None:
        """Toggle the power state."""
        if self.state == MediaPlayerState.OFF:
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

    async def async_send_command(self, command: str) -> None:
        """Send a raw RCP command."""
        await self._client.send_command(command)

    async def async_play_preset(self, preset: str) -> None:
        """Play a specific preset by index or name."""
        await self._client.play_preset(preset)

    async def async_draw(self, items: list[dict[str, Any]], clear: bool) -> None:
        """Draw composited content on the display."""
        await self._async_ensure_display_ready()
        width = self._client.display_width
        height = self._client.display_height
        
        commands = []
        if clear:
            commands.append("clear")

        for item in items:
            itype = item["type"]
            x = item.get("x", 0)
            y = item.get("y", 0)
            
            # Support 'c' for centering
            if x == "c":
                x = width // 2
            if y == "c":
                y = height // 2

            if itype == "text":
                text = item.get("text", "")
                font_path = item.get("font_path")
                
                if font_path is not None:
                    if font_path.lower() == "default":
                        font_path = None
                    size = item.get("size", height)
                    anchor = item.get("anchor")
                    lines = render_text_to_commands(
                        text, size=size, x=x, y=y, font_path=font_path, anchor=anchor,
                        width=width, height=height
                    )
                    commands.extend(lines)
                else:
                    font_index = item.get("font_index", 3)
                    commands.append(f"font {font_index}")
                    commands.append(f'text {x} {y} "{text}"')

            elif itype == "icon":
                icon = item.get("icon", "")
                icon = icon.removeprefix("mdi:")
                size = item.get("size", height)
                anchor = item.get("anchor", "lm")
                codepoint_hex = MDI_NAME_TO_CODEPOINT.get(icon)
                if codepoint_hex:
                    icon_char = chr(int(codepoint_hex, 16))
                    lines = render_icon_to_commands(
                        icon_char, size=size, x=x, y=y, anchor=anchor,
                        width=width, height=height
                    )
                    commands.extend(lines)

            elif itype == "rect":
                w = item.get("w", 10)
                h = item.get("h", 10)
                filled = item.get("filled", False)
                cmd = "rect" if filled else "framerect"
                commands.append(f"{cmd} {x} {y} {w} {h}")

            elif itype == "line":
                x1 = item.get("x1", 0)
                y1 = item.get("y1", 0)
                x2 = item.get("x2", 10)
                y2 = item.get("y2", 10)
                commands.append(f"line {x1} {y1} {x2} {y2}")

        if commands:
            await self._client.send_sketch_commands(commands)

    async def _async_ensure_display_ready(self) -> None:
        """Wake the device from standby before using the display."""
        if self._client.power_state == "standby":
            _LOGGER.debug("Waking device from standby for display service")
            await self._client.turn_on()
            await self._client.wait_for_power_on()

    async def async_sketch_command(self, command: str) -> None:
        """Send a raw sketch command."""
        await self._async_ensure_display_ready()
        await self._client.send_sketch_commands([command])

    async def async_clear_display(self) -> None:
        """Release the held sketch frame and return the device to its native UI.

        Closes the persistent sketch socket on port 4444; the SoundBridge
        reverts to whatever the firmware would otherwise be showing (now
        playing, idle screen, etc.).
        """
        await self._client.close_sketch()

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Implement the browsing of media.

        Hierarchy:
            (root)
            ├── presets                           leaf-list of all 18 presets
            └── servers
                └── servers/<i>                   per-server category menu
                    ├── servers/<i>/albums        list of album names
                    │   └── servers/<i>/albums/<name>
                    ├── servers/<i>/artists
                    │   └── servers/<i>/artists/<name>
                    ├── servers/<i>/genres
                    │   └── servers/<i>/genres/<name>
                    ├── servers/<i>/playlists
                    │   └── servers/<i>/playlists/<name>
                    └── servers/<i>/songs
        """
        if media_content_id is None or media_content_id == "root":
            return await self._async_browse_root()
        if media_content_id == "presets":
            return await self._async_browse_presets()
        if media_content_id == "servers":
            return await self._async_browse_servers()
        nav = _parse_server_path(media_content_id)
        if nav is None:
            raise ValueError(f"Unknown media_content_id: {media_content_id}")
        if nav.category is None:
            return await self._async_browse_server_root(nav.server_index)
        if nav.item_name is None and nav.track_index is None:
            return await self._async_browse_category(nav.server_index, nav.category)
        # We're at a category-item (e.g., a specific album) — show its songs.
        return await self._async_browse_category_item(nav)

    async def _async_browse_root(self) -> BrowseMedia:
        """Browse the root."""
        children = [
            BrowseMedia(
                title="Presets",
                media_class=MediaClass.DIRECTORY,
                media_content_id="presets",
                media_content_type=MediaType.PLAYLIST,
                can_play=False,
                can_expand=True,
            ),
            BrowseMedia(
                title="Servers",
                media_class=MediaClass.DIRECTORY,
                media_content_id="servers",
                media_content_type=MediaType.PLAYLIST,
                can_play=False,
                can_expand=True,
            ),
        ]
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
        """Browse presets, skipping unset slots."""
        presets = await self._client.list_presets()
        children = [
            BrowseMedia(
                title=title,
                media_class=MediaClass.MUSIC,
                media_content_id=f"play_preset:{i}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            )
            for i, title in enumerate(presets)
            if not _is_empty_preset(title)
        ]
        return BrowseMedia(
            title="Presets",
            media_class=MediaClass.DIRECTORY,
            media_content_id="presets",
            media_content_type=MediaType.PLAYLIST,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_browse_servers(self) -> BrowseMedia:
        """List available servers (each is a sub-directory, not directly playable)."""
        servers = await self._client.list_servers()
        children = [
            BrowseMedia(
                title=title or f"Server {i}",
                media_class=MediaClass.DIRECTORY,
                media_content_id=f"servers/{i}",
                media_content_type=MediaType.PLAYLIST,
                can_play=False,
                can_expand=True,
            )
            for i, title in enumerate(servers)
        ]
        return BrowseMedia(
            title="Servers",
            media_class=MediaClass.DIRECTORY,
            media_content_id="servers",
            media_content_type=MediaType.PLAYLIST,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_browse_server_root(self, server_index: int) -> BrowseMedia:
        """Show the category menu (Albums/Artists/Genres/Playlists/Songs) for a server."""
        await self._client.connect_server(server_index)
        category_titles = {
            "albums": "Albums",
            "artists": "Artists",
            "genres": "Genres",
            "playlists": "Playlists",
            "songs": "All Songs",
        }
        children = [
            BrowseMedia(
                title=category_titles[cat],
                media_class=MediaClass.DIRECTORY,
                media_content_id=f"servers/{server_index}/{cat}",
                media_content_type=MediaType.PLAYLIST,
                can_play=False,
                can_expand=True,
            )
            for cat in _CATEGORIES
        ]
        return BrowseMedia(
            title=f"Server {server_index}",
            media_class=MediaClass.DIRECTORY,
            media_content_id=f"servers/{server_index}",
            media_content_type=MediaType.PLAYLIST,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_browse_category(
        self, server_index: int, category: str
    ) -> BrowseMedia:
        """List items in a category (album names, artist names, etc.)."""
        await self._client.connect_server(server_index)
        if category == "albums":
            items = await self._client.list_albums()
            child_class = MediaClass.ALBUM
        elif category == "artists":
            items = await self._client.list_artists()
            child_class = MediaClass.ARTIST
        elif category == "genres":
            items = await self._client.list_genres()
            child_class = MediaClass.GENRE
        elif category == "playlists":
            items = await self._client.list_playlists()
            child_class = MediaClass.PLAYLIST
        elif category == "songs":
            songs = await self._client.list_songs()
            return _build_song_listing(
                title="All Songs",
                content_id=f"servers/{server_index}/songs",
                song_titles=songs,
                make_track_id=lambda i: f"servers/{server_index}/songs/{i}",
            )
        else:
            raise ValueError(f"Unknown category: {category}")
        children = [
            BrowseMedia(
                title=name or f"Item {i + 1}",
                media_class=child_class,
                media_content_id=f"servers/{server_index}/{category}/{_encode(name)}",
                media_content_type=MediaType.PLAYLIST,
                can_play=False,
                can_expand=True,
            )
            for i, name in enumerate(items)
        ]
        return BrowseMedia(
            title=category.capitalize(),
            media_class=MediaClass.DIRECTORY,
            media_content_id=f"servers/{server_index}/{category}",
            media_content_type=MediaType.PLAYLIST,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_browse_category_item(self, nav: _ServerNav) -> BrowseMedia:
        """List songs filtered by a specific album/artist/genre/playlist."""
        await self._client.connect_server(nav.server_index)
        songs = await self._refresh_filtered_song_list(nav.category, nav.item_name)
        encoded_name = _encode(nav.item_name or "")
        return _build_song_listing(
            title=nav.item_name or (nav.category or "Songs").capitalize(),
            content_id=f"servers/{nav.server_index}/{nav.category}/{encoded_name}",
            song_titles=songs,
            make_track_id=lambda i: f"servers/{nav.server_index}/{nav.category}/{encoded_name}/{i}",
        )
