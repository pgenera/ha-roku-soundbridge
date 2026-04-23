"""Test the Roku SoundBridge protocol."""

import asyncio
from unittest.mock import MagicMock, patch
import pytest

from custom_components.roku_soundbridge.protocol import RcpClient

@pytest.fixture
def update_callback():
    """Mock update callback."""
    return MagicMock()

async def test_rcp_client_parsing(update_callback) -> None:
    """Test parsing RCP lines."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    
    client._parse_line("GetTransportState: Play")
    assert client.state == "play"
    update_callback.assert_called()
    update_callback.reset_mock()

    client._parse_line("GetCurrentSongInfo: title: Imagine")
    assert client.title == "Imagine"
    update_callback.assert_called()
    update_callback.reset_mock()

    client._parse_line("GetCurrentSongInfo: artist: John Lennon")
    assert client.artist == "John Lennon"
    update_callback.assert_called()
    update_callback.reset_mock()

    client._parse_line("GetVolume: 75")
    assert client.volume == 75
    update_callback.assert_called()
    update_callback.reset_mock()

    client._parse_line("GetTotalTime: 0:03:00")
    assert client.duration == 180
    update_callback.assert_called()
    update_callback.reset_mock()

    client._parse_line("GetElapsedTime: 0:00:30")
    assert client.position == 30
    update_callback.assert_called()
    update_callback.reset_mock()

    client._parse_line("GetMACAddress: 00:01:02:03:04:05")
    assert client.mac_address == "00:01:02:03:04:05"
    update_callback.assert_called()
