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
        self.power_state: str = "on"
        self.shuffle: bool = False
        self.repeat: str = "off"
        self.display_lines: list[str] = ["", ""]
        self.metadata: dict[str, Any] = {}
        self._pre_mute_volume: int = 50
        self._list_future: asyncio.Future[list[str]] | None = None
        self._current_list: list[str] = []
        self.version: str | None = None

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
        if self._closing:
            return
        self._closing = True
        self._connected = False
        
        tasks = []
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            tasks.append(self._read_task)
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            tasks.append(self._poll_task)
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            tasks.append(self._reconnect_task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
                await self._send_command("GetPowerState")
                await asyncio.sleep(0.1)
                await self._send_command("GetMACAddress")
                await asyncio.sleep(0.1)
                await self._send_command("GetTransportState")
                await asyncio.sleep(0.1)
                await self._send_command("GetVolume")
                await asyncio.sleep(0.1)
                await self._send_command("GetCurrentSongInfo")
                await asyncio.sleep(0.1)
                await self._send_command("GetElapsedTime")
                await asyncio.sleep(0.1)
                await self._send_command("GetTotalTime")
                await asyncio.sleep(0.1)
                await self._send_command("Shuffle")
                await asyncio.sleep(0.1)
                await self._send_command("Repeat")
                await asyncio.sleep(0.1)
                await self._send_command("GetDisplayData")
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
        
        if "version" in line.lower() and self.version is None:
            # Example: Welcome to the SoundBridge Shell version 3.0.44 Release
            parts = line.split("version")
            if len(parts) > 1:
                self.version = parts[1].strip()

        if ":" not in line:
            if self._list_future and not self._list_future.done():
                self._current_list.append(line.strip())
            return

        parts = line.split(":", 1)
        command_key = parts[0].strip().lower()
        value = parts[1].strip()

        if command_key == "getpowerstate":
            self.power_state = value.lower()
        elif command_key == "setpowerstate":
            if value.lower() == "ok":
                # We don't know the new state for sure until poll, 
                # but we can assume success if we just sent standby
                pass
            elif value.lower() == "standby":
                self.power_state = "standby"
            elif value.lower() == "on":
                self.power_state = "on"
        elif command_key == "getmacaddress":
            self.mac_address = value
        elif command_key.endswith("listresultsize"):
            self._current_list = []
        elif command_key.endswith("listresultend"):
            if self._list_future and not self._list_future.done():
                self._list_future.set_result(self._current_list)
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
        elif command_key == "shuffle":
            self.shuffle = value.lower() == "on"
        elif command_key == "repeat":
            self.repeat = value.lower()
        elif command_key == "getdisplaydata":
            # The manual says display data for text mode is 2 lines of 40 chars.
            # It might come as multiple lines or a single string.
            # We'll split by common delimiters and take the first two meaningful lines.
            lines = [l.strip() for l in value.split("\r") if l.strip()]
            if len(lines) >= 2:
                self.display_lines = lines[:2]
            elif lines:
                self.display_lines = [lines[0], ""]
        elif command_key == "playpreset" and value.lower() == "powerstateon":
            self.power_state = "on"
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
                
                self.metadata[info_key] = info_val

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
            elif value.lower() == "ok":
                # End of song info transaction
                pass

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
            await asyncio.sleep(5)

    # Media player commands
    async def play(self) -> None:
        await self._send_command("Play")

    async def pause(self) -> None:
        await self._send_command("Pause")

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
        """Simulate mute by setting volume to 0 or restoring it."""
        if mute:
            if self.volume > 0:
                self._pre_mute_volume = self.volume
            await self.set_volume(0)
            self.mute = True
        else:
            await self.set_volume(self._pre_mute_volume)
            self.mute = False

    async def play_preset(self, index: int) -> None:
        """Play a user preset."""
        await self._send_command(f"PlayPreset {index}")

    async def turn_on(self) -> None:
        """Turn on the SoundBridge."""
        # Some versions might support SetPowerState on, but 
        # based on testing, PlayPreset 0 is a reliable way to wake it up.
        await self._send_command("PlayPreset 0")
        self.power_state = "on"

    async def turn_off(self) -> None:
        """Turn off the SoundBridge."""
        await self._send_command("SetPowerState standby")
        self.power_state = "standby"

    async def set_shuffle(self, shuffle: bool) -> None:
        """Set shuffle mode."""
        mode = "on" if shuffle else "off"
        await self._send_command(f"Shuffle {mode}")

    async def set_repeat(self, repeat_mode: str) -> None:
        """Set repeat mode."""
        # Valid: one, all, none
        await self._send_command(f"Repeat {repeat_mode}")

    async def send_ir_command(self, command: str) -> None:
        """Send an arbitrary IR command."""
        await self._send_command(f"IrDispatchCommand {command}")

    async def _get_list(self, command: str) -> list[str]:
        """Execute a command that returns a list and wait for results."""
        if self._list_future and not self._list_future.done():
            self._list_future.cancel()
        
        self._list_future = asyncio.Future()
        await self._send_command(command)
        try:
            return await asyncio.wait_for(self._list_future, timeout=10)
        except asyncio.TimeoutError:
            return []

    async def list_servers(self) -> list[str]:
        """List available media servers."""
        return await self._get_list("ListServers")

    async def list_songs(self) -> list[str]:
        """List songs on the active server."""
        return await self._get_list("ListSongs")

    async def list_albums(self) -> list[str]:
        """List albums on the active server."""
        return await self._get_list("ListAlbums")

    async def list_artists(self) -> list[str]:
        """List artists on the active server."""
        return await self._get_list("ListArtists")

    async def list_playlists(self) -> list[str]:
        """List playlists on the active server."""
        return await self._get_list("ListPlaylists")

    async def list_presets(self) -> list[str]:
        """List user presets."""
        return await self._get_list("ListPresets")

    async def connect_server(self, index: int) -> bool:
        """Connect to a media server by index."""
        # This is a transacted command but we'll just check for OK for now
        # because the 'Connected' token might come later.
        # But we need to be careful not to trigger it if already connected.
        await self._send_command(f"ServerConnect {index}")
        return True
