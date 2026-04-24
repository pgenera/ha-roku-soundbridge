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

    # Test extra attributes
    mock_client.display_lines = ["Line 1", "Line 2"]
    mock_client.mac_address = "AA:BB:CC:DD:EE:FF"
    mock_client.metadata = {"genre": "Rock", "year": "1970"}
    mock_client.update_callback()
    await hass.async_block_till_done()
    state = hass.states.get("media_player.roku_soundbridge")
    assert state.attributes["display_line1"] == "Line 1"
    assert state.attributes["display_line2"] == "Line 2"
    assert state.attributes["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert state.attributes["genre"] == "Rock"
    assert state.attributes["year"] == "1970"

    # Test standby
    mock_client.power_state = "standby"
    mock_client.update_callback()
    await hass.async_block_till_done()
    state = hass.states.get("media_player.roku_soundbridge")
    assert state.state == MediaPlayerState.OFF
    # Test other transport states
    mock_client.power_state = "on"
    states_to_test = [("pause", MediaPlayerState.PAUSED), ("stop", MediaPlayerState.IDLE), ("unknown", MediaPlayerState.ON)]
    for rcp_state, ha_state in states_to_test:
        mock_client.state = rcp_state
        mock_client.update_callback()
        await hass.async_block_till_done()
        state = hass.states.get("media_player.roku_soundbridge")
        assert state.state == ha_state

    # Test unavailable
    mock_client.is_connected = False
    mock_client.update_callback()
    await hass.async_block_till_done()
    state = hass.states.get("media_player.roku_soundbridge")
    assert state.state == "unavailable"
    mock_client.is_connected = True

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

    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": "media_player.mock_title",
            "media_content_id": "play_preset:5",
            "media_content_type": "music",
        },
        blocking=True,
    )
    mock_client.play_preset.assert_called_with(5)

    # Test browsing
    mock_client.list_presets.return_value = ["Preset 1", "Preset 2"]
    
    # We can use the browse_media service to test browsing
    # or get the entity from the component
    component = hass.data["media_player"]
    entity = component.get_entity("media_player.mock_title")
    
    browse = await entity.async_browse_media()
    assert browse.title == "Roku SoundBridge"
    assert len(browse.children) == 1
    assert browse.children[0].title == "Presets"

    browse_presets = await entity.async_browse_media("presets", "presets")
    assert browse_presets.title == "Presets"
    assert len(browse_presets.children) == 2
    assert browse_presets.children[0].title == "Preset 1"
    assert browse_presets.children[0].media_content_id == "play_preset:0"

    await hass.services.async_call(
        "media_player", "turn_on", {"entity_id": "media_player.mock_title"}, blocking=True
    )
    mock_client.turn_on.assert_called_once()

    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": "media_player.mock_title"}, blocking=True
    )
    mock_client.turn_off.assert_called_once()

    await hass.services.async_call(
        "media_player", "toggle", {"entity_id": "media_player.mock_title"}, blocking=True
    )
    # Since it was "on" (even if turn_off was called, mock client state didn't change unless we did it)
    # Actually mock_client.turn_off didn't change mock_client.power_state
    # Let's set it to standby to test toggle-to-on
    mock_client.power_state = "standby"
    mock_client.update_callback()
    await hass.async_block_till_done()
    
    await hass.services.async_call(
        "media_player", "toggle", {"entity_id": "media_player.mock_title"}, blocking=True
    )
    mock_client.turn_on.assert_called()

    await hass.services.async_call(
        "media_player", "shuffle_set", {"entity_id": "media_player.mock_title", "shuffle": True}, blocking=True
    )
    mock_client.set_shuffle.assert_called_with(True)

    from homeassistant.components.media_player import RepeatMode
    await hass.services.async_call(
        "media_player", "repeat_set", {"entity_id": "media_player.mock_title", "repeat": RepeatMode.ALL}, blocking=True
    )
    mock_client.set_repeat.assert_called_with("all")

    await hass.services.async_call(
        "media_player", "volume_mute", {"entity_id": "media_player.mock_title", "is_volume_muted": True}, blocking=True
    )
    mock_client.set_mute.assert_called_with(True)

    await hass.services.async_call(
        DOMAIN, "send_command", {"entity_id": "media_player.mock_title", "command": "CK_UP"}, blocking=True
    )
    mock_client.send_ir_command.assert_called_with("CK_UP")

async def test_legacy_setup(hass: HomeAssistant) -> None:
    """Test legacy platform setup."""
    from custom_components.roku_soundbridge.media_player import async_setup_platform
    async_add_entities = MagicMock()
    with patch("custom_components.roku_soundbridge.protocol.RcpClient.connect", return_value=False):
        await async_setup_platform(hass, {CONF_HOST: "127.0.0.1"}, async_add_entities)
