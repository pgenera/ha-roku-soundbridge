"""Protocol handler for Roku SoundBridge RCP."""

import asyncio
import logging
import time
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

class RcpClient:
    """Roku Control Protocol (RCP) client."""

    def __init__(self, host: str, port: int, update_callback: Callable[[], None]) -> None:
        """Initialize the client."""
        self.host = host
        self.port = port
        self.update_callback = update_callback
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self._read_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None

        # State
        self.state: str = "stopped"
        self.title: str | None = None
        self.artist: str | None = None
        self.album: str | None = None
        self.genre: str | None = None
        self.url: str | None = None
        self.duration: int = 0
        self.position: int = 0
        self.position_updated_at: float | None = None
        self.volume: int = 0
        self.mute: bool = False
        self.mac_address: str | None = None

    @property
    def is_connected(self) -> bool:
        """Return if the client is connected."""
        return self._connected

    async def connect(self) -> bool:
        """Connect to the SoundBridge."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=5
            )
            self._connected = True
            self._closing = False
            _LOGGER.debug("Connected to Roku SoundBridge at %s:%d", self.host, self.port)
            
            # Enter RCP mode
            self._writer.write(b"rcp\r\n")
            await self._writer.drain()
            
            if not self._read_task or self._read_task.done():
                self._read_task = asyncio.create_task(self._read_loop())
            if not self._poll_task or self._poll_task.done():
                self._poll_task = asyncio.create_task(self._poll_loop())
            
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as err:
            _LOGGER.debug("Failed to connect to %s:%d: %s", self.host, self.port, err)
            self._ensure_reconnect()
            return False

    def _ensure_reconnect(self) -> None:
        """Ensure the reconnection loop is running."""
        if not self._closing and (not self._reconnect_task or self._reconnect_task.done()):
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def disconnect(self) -> None:
        """Disconnect from the SoundBridge."""
        self._closing = True
        if self._read_task:
            self._read_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # pylint: disable=broad-except
                pass
        self._connected = False

    async def _send_command(self, command: str) -> None:
        """Send a command to the SoundBridge."""
        if not self._writer:
            return
        try:
            _LOGGER.debug("RCP Sending: %s", command)
            self._writer.write(f"{command}\r\n".encode())
            await self._writer.drain()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Failed to send command '%s': %s", command, err)
            await self._handle_disconnect()

    async def _poll_loop(self) -> None:
        """Periodically poll for state."""
        try:
            while self._connected:
                await self._send_command("GetMACAddress")
                await self._send_command("GetTransportState")
                await self._send_command("GetVolume")
                await self._send_command("GetCurrentSongInfo")
                await self._send_command("GetElapsedTime")
                await self._send_command("GetTotalTime")
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    async def _read_loop(self) -> None:
        """Read data from the SoundBridge."""
        try:
            while self._reader:
                line = await self._reader.readline()
                if not line:
                    break
                decoded_line = line.decode().strip()
                if decoded_line:
                    self._parse_line(decoded_line)
        except asyncio.CancelledError:
            pass
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Error in read loop: %s", err)
        finally:
            await self._handle_disconnect()

    def _parse_time(self, time_str: str) -> int:
        """Parse H:MM:SS to seconds."""
        try:
            parts = time_str.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(time_str)
        except (ValueError, IndexError):
            return 0

    def _parse_line(self, line: str) -> None:
        """Parse a line from the SoundBridge."""
        _LOGGER.debug("RCP Received: %s", line)
        
        if ":" not in line:
            return

        parts = line.split(":", 1)
        command_key = parts[0].strip().lower()
        value = parts[1].strip()

        if command_key == "getmacaddress":
            self.mac_address = value
        elif command_key == "gettransportstate":
            self.state = value.lower()
        elif command_key == "getvolume":
            try:
                self.volume = int(value)
            except ValueError:
                pass
        elif command_key == "getelapsedtime":
            self.position = self._parse_time(value)
            self.position_updated_at = time.time()
        elif command_key == "gettotaltime":
            self.duration = self._parse_time(value)
        elif command_key == "play" and value.lower() == "ok":
            self.state = "play"
        elif command_key == "pause" and value.lower() == "ok":
            self.state = "pause"
        elif command_key == "stop" and value.lower() == "ok":
            self.state = "stop"
        elif command_key == "playpause" and value.lower() == "ok":
            # If we don't know the new state, we'll wait for the next poll
            # but we could toggle it here if we're sure
            if self.state == "play":
                self.state = "pause"
            elif self.state == "pause":
                self.state = "play"
        elif command_key == "getcurrentsonginfo":
            if ":" in value:
                info_parts = value.split(":", 1)
                info_key = info_parts[0].strip().lower()
                info_val = info_parts[1].strip()
                
                if info_key == "title":
                    self.title = info_val
                elif info_key == "artist":
                    self.artist = info_val
                elif info_key == "album":
                    self.album = info_val
                elif info_key == "genre":
                    self.genre = info_val
                elif info_key == "resource[0] url" or info_key == "playlisturl":
                    self.url = info_val

        self.update_callback()

    async def _handle_disconnect(self) -> None:
        """Handle a disconnection."""
        if self._closing:
            return
        
        was_connected = self._connected
        self._connected = False
        
        if was_connected:
            self.update_callback()
        
        self._ensure_reconnect()

    async def _reconnect_loop(self) -> None:
        """Periodically attempt to reconnect."""
        while not self._connected and not self._closing:
            _LOGGER.debug("Attempting to reconnect to Roku SoundBridge at %s:%d", self.host, self.port)
            if await self.connect():
                self.update_callback()
                break
            await asyncio.sleep(30)

    # Media player commands
    async def play(self) -> None:
        await self._send_command("Play")

    async def pause(self) -> None:
        await self._send_command("Pause")

    async def play_pause(self) -> None:
        await self._send_command("PlayPause")

    async def stop(self) -> None:
        await self._send_command("Stop")

    async def next(self) -> None:
        await self._send_command("Next")

    async def previous(self) -> None:
        await self._send_command("Previous")

    async def play_url(self, url: str) -> None:
        await self._send_command(f"PlayStation {url}")

    async def seek(self, position: int) -> None:
        # Seek doesn't seem directly supported in simple way, ignoring for now or using SetElapsedTime if available
        pass

    async def set_volume(self, volume: int) -> None:
        await self._send_command(f"SetVolume {volume}")

    async def set_mute(self, mute: bool) -> None:
        # No direct mute command found in help, could simulate by saving volume
        pass
