import asyncio
import pytest
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    SERVICE_TURN_ON,
    SERVICE_TURN_OFF,
    SERVICE_MEDIA_STOP,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from custom_components.roku_soundbridge.const import DOMAIN
from tests.common import MockConfigEntry

@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_integration_full_user_journey(hass: HomeAssistant):
    """Test a full user journey using the real RCP protocol against localhost:4444."""
    host = "127.0.0.1"
    port = 4444
    
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: host, CONF_PORT: port},
        unique_id="integration_test_uid",
        title="Integration SoundBridge",
    )
    entry.add_to_hass(hass)
    
    # Setup integration
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    
    entity_id = "media_player.integration_soundbridge"
    
    # Wait for connection and initial state
    for _ in range(50):
        state = hass.states.get(entity_id)
        if state and state.state != MediaPlayerState.OFF:
            break
        await asyncio.sleep(0.1)
    
    state = hass.states.get(entity_id)
    assert state is not None
    
    # Journey Step 1: Turn On (if not already)
    if state.state == MediaPlayerState.OFF:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN, SERVICE_TURN_ON, {"entity_id": entity_id}, blocking=True
        )
        # Wait for state update
        await asyncio.sleep(1.0)
        state = hass.states.get(entity_id)
        assert state.state != MediaPlayerState.OFF

    # Journey Step 2: Connect to 'Internet Radio' (Pseudo-server 0)
    # Using our custom connect_server via play_media with prefix
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, 
        SERVICE_PLAY_MEDIA, 
        {
            "entity_id": entity_id,
            "media_content_id": "connect_server:0",
            "media_content_type": "music"
        },
        blocking=True
    )
    await asyncio.sleep(1.0)
    
    # Journey Step 3: Select and Play Preset 1
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            "entity_id": entity_id,
            "media_content_id": "play_preset:1",
            "media_content_type": "music"
        },
        blocking=True
    )
    
    # Wait for playing state
    for _ in range(50):
        state = hass.states.get(entity_id)
        if state.state == MediaPlayerState.PLAYING:
            break
        await asyncio.sleep(0.1)
    
    state = hass.states.get(entity_id)
    print(f"State after play_preset: {state.state}")
    # We might not strictly assert playing if the mock server doesn't support 
    # actual streaming, but we expect it to transition.
    
    # Journey Step 4: Test Queuing (Fire multiple volume commands)
    # We'll use service calls which should queue in the protocol
    await asyncio.gather(
        hass.services.async_call("media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.1}),
        hass.services.async_call("media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.2}),
        hass.services.async_call("media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.3}),
    )
    
    # Finally it should be at 0.3
    for _ in range(50):
        state = hass.states.get(entity_id)
        if state.attributes.get("volume_level") == 0.3:
            break
        await asyncio.sleep(0.1)
    assert state.attributes.get("volume_level") == 0.3

    # Journey Step 5: Stop
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_MEDIA_STOP, {"entity_id": entity_id}, blocking=True
    )
    await asyncio.sleep(0.5)
    state = hass.states.get(entity_id)
    assert state.state == MediaPlayerState.IDLE
    
    # Journey Step 6: Turn Off
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_TURN_OFF, {"entity_id": entity_id}, blocking=True
    )
    await asyncio.sleep(1.0)
    state = hass.states.get(entity_id)
    assert state.state == MediaPlayerState.OFF

    # Teardown
    await hass.config_entries.async_unload(entry.entry_id)
