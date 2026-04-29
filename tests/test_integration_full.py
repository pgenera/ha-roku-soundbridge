"""Full integration journey tests for Roku SoundBridge."""

import asyncio
import logging
from pathlib import Path

from PIL import Image
import pytest

from custom_components.roku_soundbridge.const import DOMAIN
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_MEDIA_STOP,
    SERVICE_PLAY_MEDIA,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from tests.common import MockConfigEntry  # noqa: TID251

_LOGGER = logging.getLogger(__name__)


@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_integration_full_user_journey(hass: HomeAssistant) -> None:
    """Test a full user journey using the real RCP protocol against localhost:5555."""
    host = "127.0.0.1"
    port = 5555
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
        if state and state.state != MediaPlayerState.ON:  # Wait for first real state
            break
        await asyncio.sleep(0.1)

    # Journey Step 1: Turn On (if not already)
    state = hass.states.get(entity_id)
    if state.state == MediaPlayerState.OFF:
        _LOGGER.info("Turning on SoundBridge...")
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_TURN_ON,
            {"entity_id": entity_id},
            blocking=True,
        )
        # Wait for state update
        for _ in range(50):
            state = hass.states.get(entity_id)
            if state.state != MediaPlayerState.OFF:
                break
            await asyncio.sleep(0.1)
        assert state.state != MediaPlayerState.OFF
    # Journey Step 2: Connect to 'Internet Radio' (Pseudo-server 0)
    # Using our custom connect_server via play_media with prefix
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            "entity_id": entity_id,
            "media_content_id": "connect_server:0",
            "media_content_type": "music",
        },
        blocking=True,
    )
    await asyncio.sleep(1.0)

    # Journey Step 3: Select and Play Preset 1
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            "entity_id": entity_id,
            "media_content_id": "play_preset:1",
            "media_content_type": "music",
        },
        blocking=True,
    )

    # Wait for playing state
    for _ in range(50):
        state = hass.states.get(entity_id)
        if state.state == MediaPlayerState.PLAYING:
            break
        await asyncio.sleep(0.1)

    state = hass.states.get(entity_id)
    _LOGGER.debug("State after play_preset: %s", state.state)

    # Journey Step 4: Test Queuing (Fire multiple volume commands)
    await asyncio.gather(
        hass.services.async_call(
            "media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.1}
        ),
        hass.services.async_call(
            "media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.2}
        ),
        hass.services.async_call(
            "media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.3}
        ),
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

    # Journey Step 7: Test Display Services
    _LOGGER.info("Testing display services...")

    await hass.services.async_call(
        DOMAIN,
        "draw_text",
        {"entity_id": entity_id, "text": "Integration Test", "font": 3},
        blocking=True,
    )

    await hass.services.async_call(
        DOMAIN,
        "draw_marquee",
        {"entity_id": entity_id, "text": "Scrolling Test", "speed": 20},
        blocking=True,
    )

    await hass.services.async_call(
        DOMAIN, "clear_display", {"entity_id": entity_id}, blocking=True
    )

    # Journey Step 8: Test Draw Image
    _LOGGER.info("Testing draw_image service...")

    img_path = Path(hass.config.config_dir) / "test_image.png"
    img = Image.new("1", (10, 10), color=1)
    img.save(img_path)

    try:
        await hass.services.async_call(
            DOMAIN,
            "draw_image",
            {"entity_id": entity_id, "image_path": str(img_path)},
            blocking=True,
        )
    finally:
        if img_path.exists():
            img_path.unlink()

    # Teardown
    await hass.config_entries.async_unload(entry.entry_id)
