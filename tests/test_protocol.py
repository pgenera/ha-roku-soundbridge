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

async def test_rcp_client_serialization(update_callback) -> None:
    """Test that commands are serialized and wait for response."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    
    # Track writes to verify order
    writes = []
    def mock_write(data):
        writes.append(data.decode().strip())
    client._writer.write.side_effect = mock_write
    client._writer.drain.return_value = asyncio.Future()
    client._writer.drain.return_value.set_result(None)

    # Start two concurrent commands
    task1 = asyncio.create_task(client.set_volume(50))
    task2 = asyncio.create_task(client.set_volume(60))
    
    # Wait a tiny bit for task1 to acquire lock and write
    await asyncio.sleep(0.01)
    
    assert writes == ["SetVolume 50"]
    assert not task1.done()
    assert not task2.done()
    
    # Feed response for task1
    client._parse_line("SetVolume: OK")
    await task1
    assert client.volume == 50
    
    # Now task2 should have proceeded
    await asyncio.sleep(0.01)
    assert writes == ["SetVolume 50", "SetVolume 60"]
    assert not task2.done()
    
    # Feed response for task2
    client._parse_line("SetVolume: OK")
    await task2
    assert client.volume == 60

async def test_rcp_client_lists(update_callback) -> None:
    """Test commands that return lists."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain.return_value = asyncio.Future()
    client._writer.drain.return_value.set_result(None)

    # Test list_presets
    task = asyncio.create_task(client.list_presets())
    await asyncio.sleep(0.01)
    assert client._writer.write.called
    
    # Simulate list response
    client._parse_line("ListPresetsListResultSize: 2")
    client._parse_line("Preset 1")
    client._parse_line("Preset 2")
    client._parse_line("ListPresetsListResultEnd: OK")
    
    presets = await task
    assert presets == ["Preset 1", "Preset 2"]

    # Test other lists
    lists = [
        ("list_servers", "ListServers"),
        ("list_songs", "ListSongs"),
        ("list_albums", "ListAlbums"),
        ("list_artists", "ListArtists"),
        ("list_playlists", "ListPlaylists"),
    ]
    
    for method_name, cmd in lists:
        method = getattr(client, method_name)
        task = asyncio.create_task(method())
        await asyncio.sleep(0.01)
        client._parse_line(f"{cmd}ListResultSize: 1")
        client._parse_line("Item 1")
        client._parse_line(f"{cmd}ListResultEnd: OK")
        result = await task
        assert result == ["Item 1"]

async def test_rcp_client_transport_commands(update_callback) -> None:
    """Test various transport and state commands."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain.return_value = asyncio.Future()
    client._writer.drain.return_value.set_result(None)

    cmds = [
        ("play", "Play", "play"),
        ("pause", "Pause", "pause"),
        ("stop", "Stop", "stop"),
        ("next", "Next", None),
        ("previous", "Previous", None),
        ("turn_on", "PlayPreset 0", "on"),
        ("turn_off", "SetPowerState standby", "standby"),
    ]

    for method_name, rcp_cmd, expected_state in cmds:
        method = getattr(client, method_name)
        task = asyncio.create_task(method())
        await asyncio.sleep(0.01)
        client._parse_line(f"{rcp_cmd.split()[0]}: OK")
        await task
        if expected_state:
            if method_name in ("turn_on", "turn_off"):
                assert client.power_state == expected_state
            else:
                assert client.state == expected_state

    # Test shuffle and repeat
    task = asyncio.create_task(client.set_shuffle(True))
    await asyncio.sleep(0.01)
    client._parse_line("Shuffle: OK")
    await task
    assert client.shuffle is True

    task = asyncio.create_task(client.set_repeat("all"))
    await asyncio.sleep(0.01)
    client._parse_line("Repeat: OK")
    await task
    assert client.repeat == "all"

async def test_rcp_client_timeout(update_callback) -> None:
    """Test that a timeout releases the lock and cleans up."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain.return_value = asyncio.Future()
    client._writer.drain.return_value.set_result(None)
    
    # Mock _ensure_reconnect to avoid socket errors on disconnect
    client._ensure_reconnect = MagicMock()

    # Patch timeout to be very short for the test
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        # This will fail due to TimeoutError
        await client.set_volume(50)
    
    # Verify we can still send commands (lock was released)
    # Reset mock and side effect
    client._writer.write.reset_mock()
    client._connected = True # Reset connected state as TimeoutError calls _handle_disconnect
    
    # This should not hang
    task = asyncio.create_task(client.set_volume(70))
    await asyncio.sleep(0.01)
    assert client._writer.write.called
    client._parse_line("SetVolume: OK")
    await task
    assert client.volume == 70

def test_rcp_client_parse_time() -> None:
    """Test time parsing edge cases."""
    client = RcpClient("127.0.0.1", 4444, None)
    assert client._parse_time("0:01:00") == 60
    assert client._parse_time("1:00") == 60
    assert client._parse_time("30") == 30
    assert client._parse_time("") == 0
    assert client._parse_time("abc") == 0
    assert client._parse_time("1:2:3:4") == 0

async def test_rcp_client_send_error(update_callback) -> None:
    """Test send command error handling."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    
    # Simulate drain error
    client._writer.drain.side_effect = Exception("Drain error")
    client._ensure_reconnect = MagicMock()
    
    await client._send_command("Test")
    assert client._connected is False
    client._ensure_reconnect.assert_called_once()

async def test_rcp_client_song_info_transaction(update_callback) -> None:
    """Test that GetCurrentSongInfo waits for OK."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain.return_value = asyncio.Future()
    client._writer.drain.return_value.set_result(None)

    task = asyncio.create_task(client._send_command("GetCurrentSongInfo", wait_for_response=True))
    await asyncio.sleep(0.01)
    
    # Send metadata line - should NOT resolve future
    client._parse_line("GetCurrentSongInfo: title: Test")
    assert not task.done()
    assert client.title == "Test"
    
    client._parse_line("GetCurrentSongInfo: OK")
    await task
    assert task.done()

async def test_rcp_client_offline_fail_fast(update_callback) -> None:
    """Test that commands fail fast when disconnected."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._connected = False
    client._writer = MagicMock()
    
    # Should return None immediately
    result = await client._send_command("Test", wait_for_response=True)
    assert result is None
    assert not client._writer.write.called

async def test_rcp_client_disconnection_during_command(update_callback) -> None:
    """Test that disconnection during a command cancels it."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain.return_value = asyncio.Future()
    client._writer.drain.return_value.set_result(None)
    client._ensure_reconnect = MagicMock()

    task = asyncio.create_task(client._send_command("Test", wait_for_response=True))
    await asyncio.sleep(0.01)
    
    # Trigger disconnection
    await client._handle_disconnect()
    
    # Task should raise/fail (or return None if we caught it)
    # Our implementation catches Exception and returns None
    result = await task
    assert result is None
    assert client.is_connected is False
