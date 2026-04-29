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
    )
    entry.add_to_hass(hass)
    # We need to manually set the runtime data because async_setup will create a new one
    # unless we patch the RcpClient constructor.
    with patch(
        "custom_components.roku_soundbridge.protocol.RcpClient",
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
