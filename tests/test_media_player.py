"""Test the Roku SoundBridge media player."""

from unittest.mock import MagicMock, patch
import pytest

from homeassistant.components.media_player import MediaPlayerState
from custom_components.roku_soundbridge.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

async def test_media_player_state(hass: HomeAssistant, mock_client) -> None:
    """Test media player state and attributes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: 4444},
        unique_id="00:11:22:33:44:55",
        title="Roku SoundBridge",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.roku_soundbridge.RcpClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("media_player.roku_soundbridge")
    assert state is not None
    assert state.state == MediaPlayerState.IDLE

    # Mock some data from RCP
    mock_client.state = "play"
    mock_client.title = "Test Song"
    mock_client.artist = "Test Artist"
    mock_client.album = "Test Album"
    mock_client.volume = 50
    
    # Trigger callback
    mock_client.update_callback()
    await hass.async_block_till_done()

    state = hass.states.get("media_player.roku_soundbridge")
    assert state.state == MediaPlayerState.PLAYING
    assert state.attributes["media_title"] == "Test Song"
    assert state.attributes["media_artist"] == "Test Artist"
    assert state.attributes["media_album_name"] == "Test Album"
    assert state.attributes["volume_level"] == 0.5

async def test_media_player_commands(hass: HomeAssistant, mock_client) -> None:
    """Test media player commands."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: 4444},
        unique_id="00:11:22:33:44:55",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.roku_soundbridge.RcpClient", return_value=mock_client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    await hass.services.async_call(
        "media_player", "media_play", {"entity_id": "media_player.mock_title"}, blocking=True
    )
    mock_client.play.assert_called_once()

    await hass.services.async_call(
        "media_player", "media_pause", {"entity_id": "media_player.mock_title"}, blocking=True
    )
    mock_client.pause.assert_called_once()

    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": "media_player.mock_title", "volume_level": 0.7}, blocking=True
    )
    mock_client.set_volume.assert_called_with(70)

    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": "media_player.mock_title",
            "media_content_id": "http://example.com/stream.mp3",
            "media_content_type": "music",
        },
        blocking=True,
    )
    mock_client.play_url.assert_called_with("http://example.com/stream.mp3")
