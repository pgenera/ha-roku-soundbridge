"""Tests for the Roku SoundBridge RCP protocol."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roku_soundbridge.protocol import RcpClient


@pytest.fixture
def update_callback():
    """Mock update callback."""
    return MagicMock()


@pytest.mark.asyncio
async def test_rcp_client_parse_line(update_callback) -> None:
    """Test parsing various lines from the SoundBridge."""
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


@pytest.mark.asyncio
async def test_rcp_client_transaction_serialization(update_callback) -> None:
    """Test that commands are serialized and wait for response."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True

    # Track writes to verify order
    writes = []

    def mock_write(data):
        writes.append(data.decode().strip())

    client._writer.write.side_effect = mock_write
    client._writer.drain = AsyncMock()

    # Start two concurrent commands
    task1 = asyncio.create_task(client._send_command("Test1", wait_for_response=True))
    task2 = asyncio.create_task(client._send_command("Test2", wait_for_response=True))

    await asyncio.sleep(0.01)

    # Only first should be written
    assert writes == ["Test1"]

    # Feed response for task1
    client._parse_line("Test1: OK")
    await task1

    # Now second should be written
    await asyncio.sleep(0.01)
    assert writes == ["Test1", "Test2"]

    # Feed response for task2
    client._parse_line("Test2: OK")
    await task2


@pytest.mark.asyncio
async def test_rcp_client_list_commands(update_callback) -> None:
    """Test commands that return lists."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain = AsyncMock()

    # Test list_presets — uses the real wire format observed from the device:
    # "<Cmd>: ListResultSize N", "<Cmd>: <item>", "<Cmd>: ListResultEnd".
    task = asyncio.create_task(client.list_presets())
    await asyncio.sleep(0.01)
    assert client._writer.write.called

    client._parse_line("ListPresets: ListResultSize 3")
    client._parse_line("ListPresets: KQED 88.5 FM")
    client._parse_line("ListPresets: WBUR 90.9 FM")
    client._parse_line("ListPresets: ")
    client._parse_line("ListPresets: ListResultEnd")

    presets = await task
    assert presets == ["KQED 88.5 FM", "WBUR 90.9 FM", ""]

    # Verify other list methods exist and follow same pattern
    list_methods = [
        (client.list_servers, "ListServers"),
        (client.list_songs, "ListSongs"),
        (client.list_albums, "ListAlbums"),
        (client.list_artists, "ListArtists"),
        (client.list_playlists, "ListPlaylists"),
    ]

    for method, cmd in list_methods:
        task = asyncio.create_task(method())
        await asyncio.sleep(0.01)
        client._parse_line(f"{cmd}: ListResultSize 1")
        client._parse_line(f"{cmd}: Item 1")
        client._parse_line(f"{cmd}: ListResultEnd")
        result = await task
        assert result == ["Item 1"]


@pytest.mark.asyncio
async def test_rcp_client_transport_commands(update_callback) -> None:
    """Test various transport and state commands."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain = AsyncMock()

    cmds = [
        (client.play, "Play", "play"),
        (client.pause, "Pause", "pause"),
        (client.stop, "Stop", "stop"),
        (client.turn_off, "SetPowerState standby", "standby"),
    ]

    for method, rcp_cmd, expected_state in cmds:
        task = asyncio.create_task(method())
        await asyncio.sleep(0.01)
        client._parse_line(f"{rcp_cmd.split()[0]}: OK")
        await task
        if expected_state:
            assert expected_state in {client.power_state, client.state}

    # Test shuffle/repeat
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


@pytest.mark.asyncio
async def test_rcp_client_timeout_handling(update_callback) -> None:
    """Test that a timeout releases the lock and cleans up."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain = AsyncMock()

    # Mock _ensure_reconnect to avoid socket errors on disconnect
    client._ensure_reconnect = AsyncMock()

    # Patch timeout to be very short for the test
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await client.set_volume(50)
        assert result is None

    # Verify we can still send commands (lock was released)
    # Reset mock and side effect
    client._writer.write.reset_mock()
    client._connected = True

    # This should not hang
    client._writer.drain = AsyncMock()

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


@pytest.mark.asyncio
async def test_rcp_client_send_error(update_callback) -> None:
    """Test send command error handling."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True

    # Simulate drain error
    client._writer.drain = AsyncMock(side_effect=OSError("Drain error"))
    client._ensure_reconnect = AsyncMock()

    await client._send_command("Test")
    assert client._connected is False
    client._ensure_reconnect.assert_called_once()


@pytest.mark.asyncio
async def test_rcp_client_song_info_transaction(update_callback) -> None:
    """Test that GetCurrentSongInfo waits for OK."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain = AsyncMock()

    task = asyncio.create_task(
        client._send_command("GetCurrentSongInfo", wait_for_response=True)
    )
    await asyncio.sleep(0.01)

    # Send metadata line - should NOT resolve future
    client._parse_line("GetCurrentSongInfo: title: Test")
    assert not task.done()
    assert client.title == "Test"

    client._parse_line("GetCurrentSongInfo: OK")
    await task
    assert task.done()


@pytest.mark.asyncio
async def test_rcp_client_send_disconnected(update_callback) -> None:
    """Test that commands fail fast when disconnected."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._connected = False
    client._writer = MagicMock()

    # Mock open_connection to fail to avoid real sockets
    with patch("asyncio.open_connection", side_effect=OSError):
        result = await client._send_command("Test", wait_for_response=True)
        assert result is None
        assert not client._writer.write.called


@pytest.mark.asyncio
async def test_rcp_client_disconnection_during_command(update_callback) -> None:
    """Test that disconnection during a command cancels it."""
    client = RcpClient("127.0.0.1", 4444, update_callback)
    client._writer = MagicMock()
    client._connected = True
    client._writer.drain = AsyncMock()
    client._ensure_reconnect = AsyncMock()

    task = asyncio.create_task(client._send_command("Test", wait_for_response=True))
    await asyncio.sleep(0.01)

    # Trigger disconnection
    client._handle_disconnect()

    # Task should raise/fail (or return None if we caught it)
    result = await task
    assert result is None
    assert client._connected is False

def test_rcp_client_display_resolution_detection() -> None:
    """Test dynamic display resolution detection from GetDisplayData byte length."""
    client = RcpClient("127.0.0.1", 4444, lambda: None)
    
    # Simulate M2000 (2048 bytes)
    client._expecting_display_data = True
    m2000_data = "ff" * 2048
    client._parse_line(m2000_data)
    assert client.display_width == 512
    assert client.display_height == 32
    
    # Simulate M1000 (560 bytes)
    client._expecting_display_data = True
    m1000_data = "ff" * 560
    client._parse_line(m1000_data)
    assert client.display_width == 280
    assert client.display_height == 16
    
    # Simulate R1000 (1120 bytes)
    client._expecting_display_data = True
    r1000_data = "ff" * 1120
    client._parse_line(r1000_data)
    assert client.display_width == 280
    assert client.display_height == 32
