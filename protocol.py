"""Roku SoundBridge RCP protocol implementation."""

from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)


class RcpClient:
    """Async client for Roku SoundBridge RCP protocol."""

    def __init__(self, host: str, port: int, update_callback: callable) -> None:
        """Initialize the client."""
        self.host = host
        self.port = port
        self.update_callback = update_callback

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._connecting = False
        self._closing = False
        self._lock = asyncio.Lock()
        self._pending_responses: dict[str, deque[asyncio.Future]] = {}

        self._read_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

        self._sketch_reader: asyncio.StreamReader | None = None
        self._sketch_writer: asyncio.StreamWriter | None = None

        # State data
        self.power_state = "on"
        self.state = "stop"
        self.volume = 0
        self.mute = False
        self.title = ""
        self.artist = ""
        self.album = ""
        self.genre = ""
        self.url = ""
        self.duration = 0
        self.position = 0
        self.position_updated_at = 0.0
        self.shuffle = False
        self.repeat = "off"
        self.display_lines = ["", ""]
        self.mac_address = ""
        self.version = ""
        self.metadata: dict[str, str] = {}
        self.display_data = b""
        self.display_width = 512
        self.display_height = 32
        self._resolution_known = False

        self._list_future: asyncio.Future | None = None
        self._current_list: list[str] = []
        self._expecting_display_data = False

    @property
    def is_connected(self) -> bool:
        """Return True if connected."""
        return self._connected

    async def connect(self) -> bool:
        """Connect to the SoundBridge."""
        if self._connected:
            return True

        if self._connecting:
            # Wait for the other task to finish connecting
            for _ in range(50):
                if self._connected:
                    return True
                if not self._connecting:
                    break
                await asyncio.sleep(0.1)
            if self._connected:
                return True

        self._connecting = True

        try:
            _LOGGER.debug(
                "Connecting to Roku SoundBridge at %s:%d", self.host, self.port
            )
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=5.0
            )
            self._connected = True
            self._closing = False

            # Port 5555 drops us directly into RCP. Wait for the banner.
            # Some firmware versions or network conditions might send leading newlines.
            line = b""
            for _ in range(3):
                line = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
                if line.strip():
                    break

            if not line.startswith(b"roku: ready"):
                _LOGGER.error("Unexpected banner on port %d: %s", self.port, line)
                raise ConnectionError(f"Invalid RCP banner: {line!r}")

            if not self._read_task or self._read_task.done():
                self._read_task = asyncio.create_task(self._read_loop())
            if not self._poll_task or self._poll_task.done():
                self._poll_task = asyncio.create_task(self._poll_loop())
            return True
        except (TimeoutError, ConnectionRefusedError, OSError) as err:
            _LOGGER.debug("Failed to connect to %s:%d: %s", self.host, self.port, err)
            self._handle_disconnect()
            return False
        finally:
            self._connecting = False

    async def disconnect(self) -> None:
        """Disconnect from the SoundBridge."""
        self._closing = True
        self._connected = False

        if self._read_task:
            self._read_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()

        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None

        if self._sketch_writer:
            self._sketch_writer.close()
            with contextlib.suppress(Exception):
                await self._sketch_writer.wait_closed()
            self._sketch_writer = None
            self._sketch_reader = None

    def _handle_disconnect(self) -> None:
        """Handle a disconnection (non-blocking)."""
        if self._closing:
            return

        was_connected = self._connected
        self._connected = False
        self._clear_pending_responses()

        # Perform cleanup in a separate task so we don't block or get cancelled
        # if the current task (read/poll loop) is about to be cancelled.
        asyncio.create_task(self._cleanup_connection(was_connected))
        self._ensure_reconnect()

    async def _cleanup_connection(self, was_connected: bool) -> None:
        """Clean up tasks and connection."""
        current_task = asyncio.current_task()

        if self._read_task and not self._read_task.done() and self._read_task != current_task:
            self._read_task.cancel()
        if self._poll_task and not self._poll_task.done() and self._poll_task != current_task:
            self._poll_task.cancel()

        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None

        # Also close sketch connection if it exists
        if self._sketch_writer:
            self._sketch_writer.close()
            with contextlib.suppress(Exception):
                await self._sketch_writer.wait_closed()
            self._sketch_writer = None
            self._sketch_reader = None

        if was_connected:
            _LOGGER.info("Disconnected from %s:%d", self.host, self.port)
            self.update_callback()

    def _ensure_reconnect(self) -> None:
        """Ensure a reconnect is scheduled."""
        if self._closing or (self._reconnect_task and not self._reconnect_task.done()):
            return

        _LOGGER.debug("Starting reconnection loop")
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnect loop."""
        delay = 5
        _LOGGER.debug("Reconnection loop started")
        while not self._connected and not self._closing:
            _LOGGER.debug("Reconnection attempt in %ds...", delay)
            await asyncio.sleep(delay)
            if await self.connect():
                _LOGGER.info("Reconnected to %s:%d", self.host, self.port)
                self.update_callback()
                break
            delay = min(delay * 2, 60)
        _LOGGER.debug("Reconnection loop finished")

    def _clear_pending_responses(self) -> None:
        """Clear all pending responses."""
        for deq in self._pending_responses.values():
            while deq:
                future = deq.popleft()
                if not future.done():
                    future.set_result(None)
        self._pending_responses.clear()

    async def send_command(self, command: str) -> None:
        """Send a command without waiting for response."""
        await self._send_command(command, wait_for_response=False)

    async def _send_command(
        self,
        command: str,
        wait_for_response: bool = False,
        disconnect_on_error: bool = True,
    ) -> Any | None:
        """Send a command and optionally wait for response."""
        if not self._connected and not self._closing:
            await self.connect()

        if not self._connected:
            return None

        async with self._lock:
            command_name = command.split(None, 1)[0].lower()
            future = None
            if wait_for_response:
                future = asyncio.Future()
                if command_name not in self._pending_responses:
                    self._pending_responses[command_name] = deque()
                self._pending_responses[command_name].append(future)

            try:
                _LOGGER.debug("RCP Sending: %s", command)
                self._writer.write(f"{command}\r\n".encode())
                await self._writer.drain()

                if future:
                    # Give it up to 5 seconds to respond
                    return await asyncio.wait_for(future, timeout=5.0)
            except (TimeoutError, OSError, asyncio.CancelledError) as err:
                _LOGGER.debug("Failed to send command '%s': %s", command, err)
                if future and command_name in self._pending_responses:
                    with contextlib.suppress(ValueError):
                        self._pending_responses[command_name].remove(future)

                # Only disconnect if it's a hard error or explicitly requested
                if disconnect_on_error or isinstance(err, OSError):
                    self._handle_disconnect()

                if isinstance(err, asyncio.CancelledError) and self._closing:
                    raise
                return None
            else:
                return "SENT"

    async def _read_loop(self) -> None:
        """Loop to read lines from the SoundBridge."""
        try:
            while self._connected:
                line_bytes = await self._reader.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").strip()
                if line:
                    self._parse_line(line)
        except asyncio.CancelledError:
            pass
        except OSError as err:
            _LOGGER.debug("Error in read loop: %s", err)
        finally:
            self._handle_disconnect()

    async def _poll_loop(self) -> None:
        """Periodically poll for state."""
        try:
            consecutive_timeouts = 0
            while self._connected:
                # Always check power state
                power_res = await self._send_command(
                    "GetPowerState", wait_for_response=True, disconnect_on_error=False
                )

                if power_res is None:
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= 3:
                        _LOGGER.warning("Multiple polling timeouts, disconnecting.")
                        self._handle_disconnect()
                        break
                else:
                    consecutive_timeouts = 0

                if not self._resolution_known:
                    await self._send_command(
                        "GetDisplayData",
                        wait_for_response=True,
                        disconnect_on_error=False,
                    )

                # If in standby, don't spam the other commands to prevent timeouts
                if self.power_state != "standby":
                    await self._send_command(
                        "GetMACAddress",
                        wait_for_response=True,
                        disconnect_on_error=False,
                    )
                    await self._send_command(
                        "GetTransportState",
                        wait_for_response=True,
                        disconnect_on_error=False,
                    )
                    await self._send_command(
                        "GetVolume", wait_for_response=True, disconnect_on_error=False
                    )
                    await self._send_command(
                        "GetCurrentSongInfo",
                        wait_for_response=True,
                        disconnect_on_error=False,
                    )
                    await self._send_command(
                        "GetElapsedTime",
                        wait_for_response=True,
                        disconnect_on_error=False,
                    )
                    await self._send_command(
                        "GetTotalTime",
                        wait_for_response=True,
                        disconnect_on_error=False,
                    )
                    await self._send_command(
                        "Shuffle", wait_for_response=True, disconnect_on_error=False
                    )
                    await self._send_command(
                        "Repeat", wait_for_response=True, disconnect_on_error=False
                    )

                # Small wait between full poll cycles
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            pass
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("Error in poll loop: %s", err)

    def _parse_time(self, time_str: str) -> int:
        """Parse a time string (H:MM:SS or MM:SS) into seconds."""
        if not time_str:
            return 0
        parts = time_str.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 1:
                return int(parts[0])
        except ValueError:
            return 0

        return 0

    def _parse_line(self, line: str) -> None:
        """Parse a line from the SoundBridge."""
        _LOGGER.debug("RCP Received: %s", line)

        if self._expecting_display_data:
            self._expecting_display_data = False
            try:
                data = bytes.fromhex(line.strip())
                self.display_data = data
                
                # Dynamically sense resolution from byte count
                # M1000: 280 * 16 / 8 = 560 bytes
                # R1000: 280 * 32 / 8 = 1120 bytes
                # M2000: 512 * 32 / 8 = 2048 bytes
                if len(data) == 560:
                    self.display_width = 280
                    self.display_height = 16
                elif len(data) == 1120:
                    self.display_width = 280
                    self.display_height = 32
                elif len(data) == 2048:
                    self.display_width = 512
                    self.display_height = 32
                self._resolution_known = True
            except ValueError:
                _LOGGER.warning("Failed to decode display data hex")
            self._resolve_future("getdisplaydata", "OK")
            self.update_callback()
            return

        if ":" not in line:
            # Lines without a colon may appear during list collection on some
            # firmware variants — treat them as bare list items.
            if self._list_future and not self._list_future.done():
                self._current_list.append(line)
            return

        command_key, value = line.split(":", 1)
        command_key = command_key.strip().lower()
        value = value.strip()

        # List boundaries are signalled in the VALUE half of the response,
        # not the command_key. The wire format is "<Cmd>: ListResultSize <N>"
        # and "<Cmd>: ListResultEnd". Skip transaction markers as well.
        if value.startswith("ListResultSize"):
            self._current_list = []
            return
        if value == "ListResultEnd":
            if self._list_future and not self._list_future.done():
                self._list_future.set_result(self._current_list)
                self._list_future = None
            return
        if value in ("TransactionInitiated", "TransactionComplete", "TransactionCanceled"):
            return

        # If we're mid-list, the value is a list item (e.g. a preset name).
        if self._list_future and not self._list_future.done():
            self._current_list.append(value)
            return

        # Resolve pending futures
        self._resolve_future(command_key, value)

        # Update state
        self._update_state_from_line(command_key, value)

        self.update_callback()

    def _resolve_future(self, command_key: str, value: str) -> None:
        """Resolve a pending future for a command."""
        _LOGGER.debug(
            "_resolve_future called with key=%r, value=%r", command_key, value
        )
        if command_key in self._pending_responses:
            deq = self._pending_responses[command_key]
            if deq:
                # Peek at the first future
                future = deq[0]

                # For GetCurrentSongInfo, it returns multiple lines of metadata.
                # We should only resolve the future when we see the final "OK"
                # OR if the first response is an error (like "GenericError").
                should_resolve = True
                if command_key == "getcurrentsonginfo":
                    is_error = value.lower() in {
                        "genericerror",
                        "error",
                        "invalidcommand",
                    }
                    is_ok = value.lower() == "ok"
                    if not is_error and not is_ok:
                        should_resolve = False
                elif command_key == "getdisplaydata":
                    if value.lower().startswith("data bytes"):
                        should_resolve = False

                _LOGGER.debug("should_resolve=%s for future=%s", should_resolve, future)
                if should_resolve:
                    deq.popleft()
                    if not future.done():
                        future.set_result(value)
        else:
            _LOGGER.debug(
                "Key %r not in pending responses: %s",
                command_key,
                list(self._pending_responses.keys()),
            )

    def _update_state_from_line(self, command_key: str, value: str) -> None:
        """Update internal state from a parsed line."""
        if command_key == "getpowerstate":
            self.power_state = value.lower()
        elif command_key == "gettransportstate":
            self.state = value.lower()
        elif command_key == "getvolume":
            with contextlib.suppress(ValueError):
                self.volume = int(value)
        elif command_key == "getelapsedtime":
            self.position = self._parse_time(value)
            self.position_updated_at = time.time()
        elif command_key == "gettotaltime":
            self.duration = self._parse_time(value)
        elif command_key == "getmacaddress":
            self.mac_address = value
        elif command_key == "getversion":
            self.version = value
        elif command_key == "getdisplaydata":
            if value.lower().startswith("data bytes"):
                self._expecting_display_data = True
            else:
                self._parse_display_data(value)
        elif command_key == "getcurrentsonginfo":
            self._parse_song_info(value)
        elif command_key == "shuffle":
            if value.lower() != "ok":
                self.shuffle = value.lower() == "on"
        elif command_key == "repeat":
            if value.lower() != "ok":
                self.repeat = value.lower()

    def _parse_display_data(self, value: str) -> None:
        """Parse display data string."""
        # It might come as multiple lines or a single string.
        # We'll split by common delimiters and take the first two meaningful lines.
        lines = [l_val.strip() for l_val in value.split("\r") if l_val.strip()]
        if len(lines) >= 2:
            self.display_lines = lines[:2]
        elif len(lines) == 1:
            self.display_lines = [lines[0], ""]

    def _parse_song_info(self, value: str) -> None:
        """Parse song info line."""
        if ":" in value:
            info_key, info_val = value.split(":", 1)
            info_key = info_key.strip().lower()
            info_val = info_val.strip()

            if info_key == "title":
                self.title = info_val
            elif info_key == "artist":
                self.artist = info_val
            elif info_key == "album":
                self.album = info_val
            elif info_key == "genre":
                self.genre = info_val
            elif info_key in {"resource[0] url", "playlisturl"}:
                self.url = info_val
        elif value.lower() == "ok":
            # End of song info
            pass

    # Media player commands
    async def play(self) -> None:
        """Send play command."""
        await self._send_command("Play", wait_for_response=False)
        self.state = "play"
        self.update_callback()

    async def pause(self) -> None:
        """Send pause command."""
        await self._send_command("Pause", wait_for_response=False)
        self.state = "pause"
        self.update_callback()

    async def stop(self) -> None:
        """Send stop command."""
        await self._send_command("Stop", wait_for_response=False)
        self.state = "stop"
        self.update_callback()

    async def next(self) -> None:
        """Send next track command."""
        await self._send_command("Next", wait_for_response=False)

    async def previous(self) -> None:
        """Send previous track command."""
        await self._send_command("Previous", wait_for_response=False)

    async def play_url(self, url: str) -> None:
        """Play a specific URL."""
        await self._send_command(f"PlayStation {url}", wait_for_response=False)
        self.state = "play"
        self.update_callback()

    async def seek(self, position: int) -> None:
        """Seek to a position in seconds."""
        # Seek doesn't seem directly supported in simple way

    async def set_volume(self, volume: int) -> None:
        """Set volume level (0-100)."""
        await self._send_command(f"SetVolume {volume}", wait_for_response=False)
        self.volume = volume
        self.update_callback()

    async def set_mute(self, mute: bool) -> None:
        """Mute or unmute the volume."""
        mode = "on" if mute else "off"
        await self._send_command(f"Mute {mode}", wait_for_response=False)
        self.mute = mute
        self.update_callback()

    async def play_preset(self, preset: int | str) -> None:
        """Play a user preset."""
        await self._send_command(f"PlayPreset {preset}", wait_for_response=False)
        self.power_state = "on"
        self.update_callback()

    async def turn_on(self) -> None:
        """Turn on the SoundBridge."""
        await self._send_command("PlayPreset 0", wait_for_response=False)
        self.power_state = "on"
        self.update_callback()

    async def wait_for_power_on(self, timeout: float = 5.0) -> bool:
        """Poll GetPowerState until the device confirms it is on.

        :param timeout: Maximum seconds to wait.
        :returns: True if the device confirmed on within the timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            result = await self._send_command(
                "GetPowerState", wait_for_response=True, disconnect_on_error=False
            )
            if result and result.lower() != "standby":
                return True
            await asyncio.sleep(0.1)
        return False

    async def turn_off(self) -> None:
        """Turn off the SoundBridge."""
        await self._send_command("SetPowerState standby", wait_for_response=False)
        self.power_state = "standby"
        self.update_callback()

    async def set_shuffle(self, shuffle: bool) -> None:
        """Set shuffle mode."""
        mode = "on" if shuffle else "off"
        await self._send_command(f"Shuffle {mode}", wait_for_response=False)
        self.shuffle = shuffle
        self.update_callback()

    async def set_repeat(self, repeat_mode: str) -> None:
        """Set repeat mode."""
        # Valid: one, all, none
        await self._send_command(f"Repeat {repeat_mode}", wait_for_response=False)
        self.repeat = repeat_mode
        self.update_callback()

    async def send_ir_command(self, command: str) -> None:
        """Send an arbitrary IR command."""
        await self._send_command(
            f"IrDispatchCommand {command}", wait_for_response=False
        )

    async def send_sketch_commands(self, commands: list[str]) -> bool:
        """Send sketch commands via a persistent port 4444 connection.

        The socket is opened once and held for the lifetime of this client so
        that the sketch frame stays on the display indefinitely — the device
        reverts to its native UI as soon as the sketch sub-shell exits. Call
        :meth:`close_sketch` (or ``roku_soundbridge.clear_display``) to release.
        """
        if not self._sketch_writer:
            reader: asyncio.StreamReader | None = None
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, 4444), timeout=5.0
                )
                # The shell on 4444 emits "...SoundBridge> " then accepts
                # `sketch` and emits "sketch> ". On a freshly-woken device the
                # initial banner can take ~2s, so allow a generous total
                # budget rather than a tight per-read timeout (which leaked
                # the socket when the banner was slow).
                async def _await_prompt(needle: bytes, total: float) -> None:
                    buf = b""
                    deadline = asyncio.get_event_loop().time() + total
                    while needle not in buf:
                        remaining = deadline - asyncio.get_event_loop().time()
                        if remaining <= 0:
                            raise TimeoutError(f"never saw {needle!r}")
                        chunk = await asyncio.wait_for(
                            reader.read(1024), timeout=remaining
                        )
                        if not chunk:
                            raise ConnectionError("closed before prompt")
                        buf += chunk

                await _await_prompt(b"SoundBridge> ", total=8.0)
                writer.write(b"sketch\r\n")
                await writer.drain()
                await _await_prompt(b"sketch> ", total=5.0)
            except (TimeoutError, OSError, ConnectionRefusedError, ConnectionError) as err:
                _LOGGER.error("Failed to open sketch connection to %s: %s", self.host, err)
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                return False
            self._sketch_reader = reader
            self._sketch_writer = writer

        try:
            chunk_size = 10
            for i in range(0, len(commands), chunk_size):
                batch = "\r\n".join(commands[i : i + chunk_size]) + "\r\n"
                self._sketch_writer.write(batch.encode())
                await self._sketch_writer.drain()
                await asyncio.sleep(0.05)
            return True
        except (TimeoutError, OSError) as err:
            _LOGGER.error("Failed to write to sketch connection: %s", err)
            await self.close_sketch()
            return False

    async def close_sketch(self) -> None:
        """Close the sketch connection, returning display to native UI."""
        if self._sketch_writer:
            try:
                self._sketch_writer.write(b"quit\r\n")
                await self._sketch_writer.drain()
            except OSError:
                pass
            self._sketch_writer.close()
            with contextlib.suppress(Exception):
                await self._sketch_writer.wait_closed()
            self._sketch_writer = None
            self._sketch_reader = None

    async def get_display_data(self) -> bytes | None:
        """Get raw display data from the SoundBridge."""
        # Wait up to 5 seconds for the response
        if await self._send_command("GetDisplayData", wait_for_response=True):
            return self.display_data
        return None

    async def _get_list(self, command: str) -> list[str]:
        """Execute a command that returns a list and wait for results."""
        if not self._connected:
            return []

        if self._list_future and not self._list_future.done():
            self._list_future.cancel()

        self._list_future = asyncio.Future()
        if await self._send_command(command) is None:
            if not self._list_future.done():
                self._list_future.cancel()
            return []

        try:
            return await asyncio.wait_for(self._list_future, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
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

    async def list_genres(self) -> list[str]:
        """List genres on the active server."""
        return await self._get_list("ListGenres")

    async def list_playlists(self) -> list[str]:
        """List playlists on the active server."""
        return await self._get_list("ListPlaylists")

    async def list_playlist_songs(self, playlist_index: int) -> list[str]:
        """List songs in the playlist at the given index of the last ListPlaylists result."""
        return await self._get_list(f"ListPlaylistSongs {playlist_index}")

    async def list_presets(self) -> list[str]:
        """List user presets."""
        return await self._get_list("ListPresets")

    async def connect_server(self, index: int) -> bool:
        """Connect to a media server by index."""
        resp = await self._send_command(
            f"ServerConnect {index}", wait_for_response=True
        )
        return resp is not None

    async def disconnect_server(self) -> None:
        """Disconnect from the current server."""
        await self._send_command("ServerDisconnect", wait_for_response=False)

    async def set_browse_filter_artist(self, name: str) -> None:
        """Set the artist browse filter (filters subsequent List* commands)."""
        await self._send_command(
            f"SetBrowseFilterArtist {name}", wait_for_response=False
        )

    async def set_browse_filter_album(self, name: str) -> None:
        """Set the album browse filter (filters subsequent List* commands)."""
        await self._send_command(
            f"SetBrowseFilterAlbum {name}", wait_for_response=False
        )

    async def set_browse_filter_genre(self, name: str) -> None:
        """Set the genre browse filter (filters subsequent List* commands)."""
        await self._send_command(
            f"SetBrowseFilterGenre {name}", wait_for_response=False
        )

    async def play_index(self, index: int) -> None:
        """Play the song at index in the last list result."""
        await self._send_command(f"PlayIndex {index}", wait_for_response=False)
        self.state = "play"
        self.update_callback()

    async def queue_and_play(self, index: int) -> None:
        """Replace the now-playing queue with the last list result and start at index."""
        await self._send_command(f"QueueAndPlay {index}", wait_for_response=False)
        self.state = "play"
        self.update_callback()

    async def queue_and_play_one(self, index: int) -> None:
        """Replace the now-playing queue with a single song from the last list result."""
        await self._send_command(f"QueueAndPlayOne {index}", wait_for_response=False)
        self.state = "play"
        self.update_callback()
