"""Tests for the Roku SoundBridge media player platform."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roku_soundbridge.const import DOMAIN
from custom_components.roku_soundbridge.media_player import (
    RokuSoundBridgeMediaPlayer,
    async_setup_platform,
)
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from tests.common import MockConfigEntry  # noqa: TID251


@pytest.fixture
def mock_client():
    """Mock RcpClient."""
    client = MagicMock()
    client.host = "127.0.0.1"
    client.port = 5555
    client.is_connected = True
    client.power_state = "on"
    client.state = "play"
    client.volume = 50
    client.mute = False
    client.title = "Mock Title"
    client.artist = "Mock Artist"
    client.album = "Mock Album"
    client.url = "http://mock"
    client.duration = 300
    client.position = 100
    client.position_updated_at = 123456789.0
    client.shuffle = False
    client.repeat = "off"
    client.display_lines = ["Line 1", "Line 2"]
    client.mac_address = "00:11:22:33:44:55"
    client.version = "1.0"
    client.metadata = {"bitrate": "128"}

    # Async methods
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.play = AsyncMock()
    client.pause = AsyncMock()
    client.stop = AsyncMock()
    client.next = AsyncMock()
    client.previous = AsyncMock()
    client.set_volume = AsyncMock()
    client.set_mute = AsyncMock()
    client.turn_on = AsyncMock()
    client.turn_off = AsyncMock()
    client.set_shuffle = AsyncMock()
    client.set_repeat = AsyncMock()
    client.play_url = AsyncMock()
    client.play_preset = AsyncMock()
    client.connect_server = AsyncMock()
    client.list_servers = AsyncMock(return_value=["Server 1"])
    client.list_presets = AsyncMock(return_value=["Preset 1"])
    client.list_albums = AsyncMock(return_value=["Album A", "Album B"])
    client.list_artists = AsyncMock(return_value=["Artist X"])
    client.list_genres = AsyncMock(return_value=["Rock", "Jazz"])
    client.list_playlists = AsyncMock(return_value=["My Mix", "Workout"])
    client.list_playlist_songs = AsyncMock(return_value=["P-Song 1", "P-Song 2"])
    client.list_songs = AsyncMock(return_value=["Track 1", "Track 2", "Track 3"])
    client.set_browse_filter_album = AsyncMock()
    client.set_browse_filter_artist = AsyncMock()
    client.set_browse_filter_genre = AsyncMock()
    client.play_index = AsyncMock()
    client.send_sketch_commands = AsyncMock(return_value=True)
    client.wait_for_power_on = AsyncMock(return_value=True)

    return client


@pytest.mark.asyncio
async def test_media_player_properties(hass: HomeAssistant, mock_client) -> None:
    """Test media player properties."""
    player = RokuSoundBridgeMediaPlayer(
        mock_client, "Mock Player", "entry_id", "unique_id"
    )
    player.hass = hass

    assert player.name is None  # has_entity_name = True
    assert player.state == MediaPlayerState.PLAYING
    assert player.volume_level == 0.5
    assert player.is_volume_muted is False
    assert player.media_title == "Mock Title"
    assert player.media_artist == "Mock Artist"
    assert player.extra_state_attributes["display_line1"] == "Line 1"
    assert player.available is True


@pytest.mark.asyncio
async def test_media_player_actions(hass: HomeAssistant, mock_client) -> None:
    """Test media player actions."""
    player = RokuSoundBridgeMediaPlayer(
        mock_client, "Mock Player", "entry_id", "unique_id"
    )
    player.hass = hass

    await player.async_media_play()
    mock_client.play.assert_called_once()

    await player.async_media_pause()
    mock_client.pause.assert_called_once()

    await player.async_media_stop()
    mock_client.stop.assert_called_once()

    await player.async_set_volume_level(0.7)
    mock_client.set_volume.assert_called_with(70)

    await player.async_mute_volume(True)
    mock_client.set_mute.assert_called_with(True)


@pytest.mark.asyncio
async def test_media_player_service_calls(hass: HomeAssistant, mock_client) -> None:
    """Test media player service calls."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: 4444},
        title="Mock SoundBridge",
        unique_id="00:11:22:33:44:55",
    )
    entry.add_to_hass(hass)
    # When ha-core loads a custom_component, it imports the module under the
    # `homeassistant.components.<domain>` namespace (not `custom_components.*`),
    # so the patch must target the name *as imported into __init__* via that
    # namespace. `from .protocol import RcpClient` binds RcpClient at module
    # level in __init__, and that's what async_setup_entry resolves at call time.
    with patch(
        "homeassistant.components.roku_soundbridge.RcpClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        "media_play",
        {"entity_id": "media_player.mock_soundbridge"},
        blocking=True,
    )
    mock_client.play.assert_called_once()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        "volume_set",
        {"entity_id": "media_player.mock_soundbridge", "volume_level": 0.8},
        blocking=True,
    )
    mock_client.set_volume.assert_called_with(80)


@pytest.mark.asyncio
async def test_legacy_setup(hass: HomeAssistant) -> None:
    """Test legacy platform setup."""
    async_add_entities = MagicMock()
    with patch(
        "custom_components.roku_soundbridge.protocol.RcpClient.connect",
        return_value=False,
    ):
        await async_setup_platform(
            hass, {CONF_HOST: "127.0.0.1"}, async_add_entities, None
        )
    assert async_add_entities.called


@pytest.mark.asyncio
async def test_async_draw_unified_service(mock_client) -> None:
    """Test the unified async_draw service."""
    # Ensure client has some dynamic resolution set
    mock_client.display_width = 280
    mock_client.display_height = 16
    
    player = RokuSoundBridgeMediaPlayer(mock_client, "Test", "test_id", "test_uid")

    items = [
        # Native text using center coordinate shortcut
        {"type": "text", "text": "Native", "x": "c", "y": "c", "font_index": 3},
        # Rendered text
        {"type": "text", "text": "Rendered", "font_path": "default", "size": 16},
        # Icon
        {"type": "icon", "icon": "mdi:weather-sunny", "size": 16},
        # Rect
        {"type": "rect", "x": 0, "y": 0, "w": 10, "h": 10},
        # Line
        {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 10},
    ]

    await player.async_draw(items, clear=True)

    # Verify that the draw commands were sent to the client
    assert mock_client.send_sketch_commands.called
    
    # Inspect the commands that were generated
    commands = mock_client.send_sketch_commands.call_args[0][0]
    
    # Check that clear was sent first
    assert commands[0] == "clear"
    
    # Check native text centering logic (280/2=140, 16/2=8)
    assert "font 3" in commands
    assert 'text 140 8 "Native"' in commands
    
    # Check rect and line primitives
    assert "framerect 0 0 10 10" in commands
    assert "line 0 0 10 10" in commands
    
    # Rendered text and icon will output numerous 'line ...' commands
    # Just verify that there are many commands in total
    assert len(commands) > 10


# ---- Media browsing & selection ----

from custom_components.roku_soundbridge.media_player import (
    _ServerNav,
    _parse_server_path,
)


def test_parse_server_path_root():
    nav = _parse_server_path("servers/2")
    assert nav == _ServerNav(server_index=2)


def test_parse_server_path_category():
    nav = _parse_server_path("servers/0/albums")
    assert nav == _ServerNav(server_index=0, category="albums")


def test_parse_server_path_item():
    nav = _parse_server_path("servers/0/albums/Some%20Album")
    assert nav == _ServerNav(
        server_index=0, category="albums", item_name="Some Album"
    )


def test_parse_server_path_track():
    nav = _parse_server_path("servers/0/albums/Some%20Album/4")
    assert nav == _ServerNav(
        server_index=0,
        category="albums",
        item_name="Some Album",
        track_index=4,
    )


def test_parse_server_path_songs_track():
    """For 'songs' category, the third segment IS the track index."""
    nav = _parse_server_path("servers/0/songs/12")
    assert nav == _ServerNav(server_index=0, category="songs", track_index=12)


def test_parse_server_path_invalid():
    assert _parse_server_path("foo/bar") is None
    assert _parse_server_path("servers/notanint") is None
    assert _parse_server_path("servers/0/badcategory") is None


@pytest.mark.asyncio
async def test_browse_root(hass: HomeAssistant, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(
        mock_client, "Mock", "entry_id", "unique_id"
    )
    player.hass = hass
    root = await player.async_browse_media()
    assert root.media_content_id == "root"
    titles = [c.title for c in root.children]
    assert titles == ["Presets", "Servers"]


@pytest.mark.asyncio
async def test_browse_presets(hass, mock_client) -> None:
    mock_client.list_presets = AsyncMock(return_value=["KQED", "KCRW", ""])
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    node = await player.async_browse_media(media_content_id="presets")
    assert node.media_content_id == "presets"
    assert [c.title for c in node.children] == ["KQED", "KCRW", "Preset 3"]
    assert node.children[0].media_content_id == "play_preset:0"
    assert node.children[0].can_play is True
    assert node.children[0].can_expand is False


@pytest.mark.asyncio
async def test_browse_servers_lists_directories(hass, mock_client) -> None:
    mock_client.list_servers = AsyncMock(return_value=["Internet Radio", "MyDAAP"])
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    node = await player.async_browse_media(media_content_id="servers")
    assert [c.title for c in node.children] == ["Internet Radio", "MyDAAP"]
    # Servers are expandable directories now (not directly playable)
    for c in node.children:
        assert c.can_expand is True
        assert c.can_play is False
    assert node.children[0].media_content_id == "servers/0"
    assert node.children[1].media_content_id == "servers/1"


@pytest.mark.asyncio
async def test_browse_server_root_shows_categories(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    node = await player.async_browse_media(media_content_id="servers/0")
    mock_client.connect_server.assert_called_with(0)
    titles = [c.title for c in node.children]
    assert titles == ["Albums", "Artists", "Genres", "Playlists", "All Songs"]


@pytest.mark.asyncio
async def test_browse_albums_list(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    node = await player.async_browse_media(media_content_id="servers/0/albums")
    mock_client.list_albums.assert_called_once()
    assert [c.title for c in node.children] == ["Album A", "Album B"]
    # Album-name segments must be URL-encoded
    assert node.children[0].media_content_id == "servers/0/albums/Album%20A"


@pytest.mark.asyncio
async def test_browse_album_songs(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    node = await player.async_browse_media(
        media_content_id="servers/0/albums/Album%20A"
    )
    mock_client.set_browse_filter_album.assert_called_with("Album A")
    mock_client.list_songs.assert_called_once()
    assert [c.title for c in node.children] == ["Track 1", "Track 2", "Track 3"]
    # Each track should be a playable leaf with a track index in its content_id
    leaf = node.children[2]
    assert leaf.can_play is True
    assert leaf.can_expand is False
    assert leaf.media_content_id == "servers/0/albums/Album%20A/2"


@pytest.mark.asyncio
async def test_browse_playlist_songs_uses_index_lookup(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    node = await player.async_browse_media(
        media_content_id="servers/0/playlists/Workout"
    )
    # Should ListPlaylists first, then ListPlaylistSongs <index of Workout>
    mock_client.list_playlists.assert_called_once()
    mock_client.list_playlist_songs.assert_called_once_with(1)
    assert [c.title for c in node.children] == ["P-Song 1", "P-Song 2"]


@pytest.mark.asyncio
async def test_play_media_album_track(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    await player.async_play_media(MediaType.MUSIC, "servers/0/albums/Album%20A/2")
    mock_client.connect_server.assert_called_with(0)
    mock_client.set_browse_filter_album.assert_called_with("Album A")
    mock_client.list_songs.assert_called_once()
    mock_client.play_index.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_play_media_playlist_track(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    await player.async_play_media(MediaType.MUSIC, "servers/0/playlists/Workout/0")
    mock_client.list_playlists.assert_called_once()
    mock_client.list_playlist_songs.assert_called_once_with(1)
    mock_client.play_index.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_play_media_all_songs_track(hass, mock_client) -> None:
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    await player.async_play_media(MediaType.MUSIC, "servers/0/songs/5")
    mock_client.list_songs.assert_called_once()
    mock_client.play_index.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_play_preset_path_unchanged(hass, mock_client) -> None:
    """Existing play_preset: scheme keeps working."""
    player = RokuSoundBridgeMediaPlayer(mock_client, "M", "e", "u")
    player.hass = hass
    await player.async_play_media(MediaType.MUSIC, "play_preset:3")
    mock_client.play_preset.assert_called_once_with(3)
