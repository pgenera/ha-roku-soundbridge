import asyncio

import pytest

from custom_components.roku_soundbridge.display import (
    async_send_sketch_commands,
    parse_display_bitmap,
)
from custom_components.roku_soundbridge.protocol import RcpClient


@pytest.mark.asyncio
async def test_display_read_integration(soundbridge_host: str) -> None:
    """Test reading the display from the real device."""
    client = RcpClient(soundbridge_host, 5555, lambda: None)
    connected = await client.connect()
    assert connected

    # Allow initial poll to populate power state
    await asyncio.sleep(1.5)

    was_standby = client.power_state == "standby"
    if was_standby:
        await client.turn_on()
        await client.wait_for_power_on()

    # 1. Clear display
    await async_send_sketch_commands(soundbridge_host, 4444, ["clear"])
    await asyncio.sleep(0.5)

    data_clear = await client.get_display_data()
    parse_display_bitmap(data_clear, client.display_width, client.display_height)
    # Most pixels should be 0, though some devices show a blinking cursor
    # We just ensure it works without crashing

    # 2. Draw text
    await async_send_sketch_commands(
        soundbridge_host, 4444, ["clear", "font 3", 'text c c "TestWeather"']
    )
    await asyncio.sleep(0.5)

    data_text = await client.get_display_data()
    lines_text = parse_display_bitmap(data_text, client.display_width, client.display_height)

    # We should have some active pixels
    has_active_pixels = any("1" in line for line in lines_text)
    assert has_active_pixels, "Display should have active pixels after drawing text"

    if was_standby:
        await client.turn_off()

    await client.disconnect()
