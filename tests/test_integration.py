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
    
    # Wait for initial data to be polled
    try:
        await asyncio.wait_for(update_called.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass
        
    for _ in range(50):
        if client.mac_address:
            break
        await asyncio.sleep(0.1)
        
    assert client.mac_address is not None
    
    # Fully exercise the functionality
    await client.play()
    await asyncio.sleep(0.1)
    
    await client.pause()
    await asyncio.sleep(0.1)
    
    await client.stop()
    await asyncio.sleep(0.1)
    
    await client.next()
    await asyncio.sleep(0.1)
    
    await client.previous()
    await asyncio.sleep(0.1)
    
    await client.set_volume(50)
    await asyncio.sleep(0.1)
    
    await client.play_url("http://example.com/stream.mp3")
    await asyncio.sleep(0.1)
    
    await client.disconnect()
    assert client.is_connected is False
