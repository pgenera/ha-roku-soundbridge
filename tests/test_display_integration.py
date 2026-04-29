import asyncio

import pytest

from custom_components.roku_soundbridge.display import (
    async_send_sketch_commands,
    parse_display_bitmap,
)
from custom_components.roku_soundbridge.protocol import RcpClient


@pytest.mark.asyncio
async def test_display_read_integration() -> None:
    """Test reading the display from the real device on localhost."""
    client = RcpClient("localhost", 5555, lambda: None)
    connected = await client.connect()
    assert connected

    # 1. Clear display
    await async_send_sketch_commands("localhost", 4444, ["clear"])
    await asyncio.sleep(0.5)

    data_clear = await client.get_display_data()
    lines_clear = parse_display_bitmap(data_clear)
    # Most pixels should be 0, though some devices show a blinking cursor
    # We just ensure it works without crashing

    # 2. Draw text
    await async_send_sketch_commands(
        "localhost", 4444, ["clear", "font 3", 'text c c "TestWeather"']
    )
    await asyncio.sleep(0.5)

    data_text = await client.get_display_data()
    lines_text = parse_display_bitmap(data_text)

    # We should have some active pixels
    has_active_pixels = any("1" in line for line in lines_text)
    assert has_active_pixels, "Display should have active pixels after drawing text"

    await client.disconnect()
