import asyncio
import pytest
from custom_components.roku_soundbridge.protocol import RcpClient

@pytest.mark.enable_socket
@pytest.mark.asyncio
async def test_integration_connect_and_state():
    """Test connecting to the live local Roku SoundBridge and checking state."""
    host = "127.0.0.1"
    port = 4444
    
    update_called = asyncio.Event()

    def update_callback():
        update_called.set()

    client = RcpClient(host, port, update_callback)
    
    # Test connection
    connected = await client.connect()
    assert connected is True
    
    try:
        # Ensure it's ON and has a known volume
        await client.turn_on()
        await client.set_volume(75)
        
        # Wait for MAC to be populated
        for _ in range(50):
            if client.mac_address:
                break
            await asyncio.sleep(0.1)
            
        assert client.mac_address is not None
        
        # Test play with a URL
        await client.play_url("http://example.com/test.mp3")
        # Wait a bit for it to actually transition to play internally
        await asyncio.sleep(1.0)
        print(f"State after play_url: {client.state}")
        
        await client.pause()
        await client.stop()
        assert client.state == "stop"
        
        await client.set_volume(75)
        assert client.volume == 75
        
        # Test mute
        await client.set_mute(True)
        assert client.mute is True
        assert client.volume == 0
        
        await client.set_mute(False)
        assert client.mute is False
        print(f"Volume after unmute: {client.volume}")
        
        await client.set_shuffle(True)
        print(f"Shuffle state: {client.shuffle}")
        
        await client.set_repeat("all")
        print(f"Repeat state: {client.repeat}")
        
        # Test server connecting (Internet Radio is usually 0)
        await client.connect_server(0)
        await asyncio.sleep(0.5)
        
        # Test preset playback
        await client.play_preset(1)
        await asyncio.sleep(0.5)
        # We don't strictly assert state here as it might take time to start playing
        # but we've verified the command was sent without error.
        
        # Test arbitrary IR command
        await client.send_ir_command("CK_UP")
        
        # Test power commands
        await client.turn_off()
        assert client.power_state == "standby"
        
        await client.turn_on()
        assert client.power_state == "on"

    finally:
        await client.disconnect()
        # Wait for tasks to clean up to avoid lingering task errors
        await asyncio.sleep(1.0)

    assert client.is_connected is False
