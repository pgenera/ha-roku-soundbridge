"""Mirror-display controller: drive the SoundBridge VFD from another media_player.

When enabled, this listens to state changes on a configured source
``media_player`` entity (which may be a ``universal`` media_player containing
this SoundBridge as one member). It pushes the source's title/artist to the
SoundBridge display while the source is playing, sends the SoundBridge to
standby after a quiet timeout when the source pauses/idles, and stays out of
the way when the SoundBridge itself is the universal MP's active child.

All RCP operations are best-effort: when the device is unreachable or in
standby (and we're not waking it), the controller logs at debug and moves on.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
)

from .display import render_text_to_commands

if TYPE_CHECKING:
    from .protocol import RcpClient

_LOGGER = logging.getLogger(__name__)

# Source states that mean "actively producing audio" for our purposes.
_ACTIVE_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
# Source states that should immediately put the SB to standby.
_OFF_STATES = {
    MediaPlayerState.OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    None,
}
# Source states that should schedule the idle-standby timer.
_IDLE_STATES = {
    MediaPlayerState.PAUSED,
    MediaPlayerState.IDLE,
}

# How often to re-push the active mirror frame so the SoundBridge firmware's
# native UI repaints don't permanently overwrite us. Short enough to be
# imperceptible after a stomp (<5s of stale native UI) but long enough to
# avoid hammering the sketch socket.
_HEARTBEAT_SECONDS = 4


class MirrorDisplayController:
    """Mirror another media_player entity's now-playing onto the SoundBridge VFD."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: RcpClient,
        own_entity_id_provider,
    ) -> None:
        """Initialize the controller.

        :param hass: HomeAssistant core.
        :param client: The SoundBridge RCP client.
        :param own_entity_id_provider: Zero-arg callable returning our own
            media_player entity_id (so we can detect "I am the universal MP's
            active child"). May return ``None`` before the media_player
            entity has been added to HA.
        """
        self._hass = hass
        self._client = client
        self._own_entity_id_provider = own_entity_id_provider

        self._enabled: bool = False
        self._source_entity_id: str | None = None
        self._idle_seconds: int = 30

        self._unsub_state = None
        self._idle_cancel = None
        # Last frame key (title, artist) successfully pushed.
        self._last_frame: tuple[str, str] | None = None
        # Cached command list for the last frame so the heartbeat can resend it
        # without re-rasterising the text every time.
        self._last_frame_commands: list[str] | None = None
        # We only auto-wake on transitions INTO an active state, not on
        # repeat metadata updates while already active.
        self._was_active: bool = False
        # Set whenever we put the device to standby ourselves, so we know it's
        # OK to wake again on the next play; cleared on manual user activity.
        self._mirror_active: bool = False
        # Periodic re-push handle: the SoundBridge firmware will repaint over
        # the sketch frame whenever its native UI (now-playing, idle splash,
        # menu) decides to. Re-sending the same frame on a short interval keeps
        # our content visible without forcing user-visible flicker.
        self._heartbeat_cancel = None

    # --- Public surface ---

    @property
    def mirror_active(self) -> bool:
        """True if the SB is currently in mirror-only mode (awake to display).

        Used by media_player.state to report OFF while the SB is being driven
        by us rather than by its own playback.
        """
        return self._mirror_active

    @property
    def source_entity_id(self) -> str | None:
        """Configured source entity, or None if not set."""
        return self._source_entity_id

    def configure(
        self,
        source_entity_id: str | None,
        idle_seconds: int,
    ) -> None:
        """Update configuration. Caller is responsible for (re)starting."""
        self._source_entity_id = source_entity_id
        self._idle_seconds = max(5, int(idle_seconds))

    async def set_enabled(self, enabled: bool) -> None:
        """Enable or disable mirroring. Persisting the choice is the caller's job."""
        if enabled == self._enabled:
            return
        if enabled:
            self._enabled = True
            self._start_tracking()
            # Apply current source state right away so we don't wait for the
            # next state change.
            self._reevaluate()
        else:
            self._enabled = False
            self._stop_tracking()
            self._cancel_idle_timer()
            self._cancel_heartbeat()
            # If we engaged mirror mode, leave the device in whatever state
            # it's in now — don't aggressively re-standby on disable.
            self._mirror_active = False
            self._last_frame = None
            self._last_frame_commands = None

    def start(self) -> None:
        """Begin tracking if enabled. Safe to call before HA is fully running."""
        if self._enabled:
            self._start_tracking()
            self._reevaluate()

    async def stop(self) -> None:
        """Tear down all listeners and timers. Idempotent."""
        self._stop_tracking()
        self._cancel_idle_timer()
        self._cancel_heartbeat()
        self._mirror_active = False

    # --- Internals ---

    def _start_tracking(self) -> None:
        self._stop_tracking()
        if not self._source_entity_id:
            return
        self._unsub_state = async_track_state_change_event(
            self._hass,
            [self._source_entity_id],
            self._handle_source_change,
        )

    def _stop_tracking(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None

    @callback
    def _handle_source_change(self, event: Event[EventStateChangedData]) -> None:
        self._reevaluate()

    @callback
    def _reevaluate(self) -> None:
        """Evaluate current source state and react. Schedules async work."""
        if not self._enabled or not self._source_entity_id:
            return
        state_obj = self._hass.states.get(self._source_entity_id)
        if state_obj is None:
            return

        # If source is a universal MP and its active_child is us, skip.
        own_id = self._own_entity_id_provider()
        active_child = state_obj.attributes.get("active_child")
        if own_id and active_child and active_child == own_id:
            _LOGGER.debug(
                "Mirror: skipping — universal active_child is self (%s)", own_id
            )
            self._cancel_idle_timer()
            return

        state = state_obj.state

        if state in _ACTIVE_STATES:
            title = state_obj.attributes.get("media_title") or ""
            artist = state_obj.attributes.get("media_artist") or ""
            transitioning_to_active = not self._was_active
            self._was_active = True
            self._cancel_idle_timer()
            self._mirror_active = True
            self._hass.async_create_task(
                self._handle_active(title, artist, transitioning_to_active)
            )
            return

        # No longer active.
        self._was_active = False

        if state in _OFF_STATES:
            self._cancel_idle_timer()
            self._hass.async_create_task(self._standby_now())
            return

        if state in _IDLE_STATES:
            # Render a "Paused" marker (best-effort), then schedule standby.
            self._hass.async_create_task(self._handle_idle())
            self._schedule_idle_timer()

    async def _handle_active(
        self, title: str, artist: str, transitioning_to_active: bool
    ) -> None:
        """Source is playing/buffering — wake if needed and render."""
        try:
            if self._client.power_state == "standby":
                if not transitioning_to_active:
                    # We're seeing repeat metadata while still "active" but the
                    # device is in standby — that's likely because the user
                    # manually put it to standby. Don't fight them.
                    _LOGGER.debug(
                        "Mirror: source active but SB in standby and "
                        "we didn't just transition; not waking"
                    )
                    return
                if not self._client.is_connected:
                    _LOGGER.debug("Mirror: SB not reachable; skipping wake")
                    return
                _LOGGER.debug("Mirror: waking SB on play transition")
                await self._client.turn_on()
                woke = await self._client.wait_for_power_on()
                if not woke:
                    _LOGGER.debug("Mirror: SB did not confirm wake; skipping draw")
                    return

            await self._render(title, artist)
        except Exception as err:  # noqa: BLE001 — never fail loudly
            _LOGGER.debug("Mirror: error in active path: %s", err)

    async def _handle_idle(self) -> None:
        """Source paused/idle — render a 'Paused' indicator if SB is awake."""
        try:
            if not self._client.is_connected:
                return
            if self._client.power_state == "standby":
                return
            await self._render("Paused", "")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Mirror: error in idle path: %s", err)

    async def _standby_now(self) -> None:
        """Send the device to standby; clear local mirror state."""
        self._mirror_active = False
        self._last_frame = None
        self._last_frame_commands = None
        self._cancel_heartbeat()
        try:
            if not self._client.is_connected:
                return
            if self._client.power_state == "standby":
                return
            _LOGGER.debug("Mirror: putting SB to standby (source off)")
            await self._client.turn_off()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Mirror: error sending standby: %s", err)

    def _schedule_idle_timer(self) -> None:
        self._cancel_idle_timer()
        self._idle_cancel = async_call_later(
            self._hass, self._idle_seconds, self._on_idle_timeout
        )

    def _cancel_idle_timer(self) -> None:
        if self._idle_cancel is not None:
            self._idle_cancel()
            self._idle_cancel = None

    @callback
    def _on_idle_timeout(self, _now) -> None:
        self._idle_cancel = None
        self._hass.async_create_task(self._standby_now())

    def _schedule_heartbeat(self) -> None:
        """Arm the periodic re-push so the firmware can't permanently steal the frame."""
        self._cancel_heartbeat()
        self._heartbeat_cancel = async_call_later(
            self._hass, _HEARTBEAT_SECONDS, self._on_heartbeat
        )

    def _cancel_heartbeat(self) -> None:
        if self._heartbeat_cancel is not None:
            self._heartbeat_cancel()
            self._heartbeat_cancel = None

    @callback
    def _on_heartbeat(self, _now) -> None:
        self._heartbeat_cancel = None
        self._hass.async_create_task(self._do_heartbeat())

    async def _do_heartbeat(self) -> None:
        """Re-send the last frame if we're still in mirror-only mode."""
        try:
            if not self._mirror_active or self._last_frame_commands is None:
                return
            if not self._client.is_connected:
                # Device gone — stop trying until something else wakes us back.
                return
            if self._client.power_state == "standby":
                return
            await self._client.send_sketch_commands(self._last_frame_commands)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Mirror: heartbeat error: %s", err)
        finally:
            # Re-arm if still active. _mirror_active flips to False in the
            # standby path, which is the natural stop signal.
            if self._mirror_active and self._last_frame_commands is not None:
                self._schedule_heartbeat()

    async def _render(self, title: str, artist: str) -> None:
        """Push a two-line frame to the display, skipping redundant redraws."""
        key = (title, artist)
        if key == self._last_frame:
            return
        if not self._client.is_connected:
            return
        width = self._client.display_width
        height = self._client.display_height
        # Two horizontal halves: title on top, artist on bottom.
        # Use a font size that fits one line vertically; render_text_to_commands
        # falls back to the bundled Roboto font when no path is given.
        line_height = max(8, height // 2)
        commands = ["clear"]
        if title:
            commands.extend(
                render_text_to_commands(
                    title,
                    size=line_height,
                    x=0,
                    y=0,
                    anchor="lt",
                    width=width,
                    height=height,
                )
            )
        if artist:
            commands.extend(
                render_text_to_commands(
                    artist,
                    size=line_height,
                    x=0,
                    y=line_height,
                    anchor="lt",
                    width=width,
                    height=height,
                )
            )
        ok = await self._client.send_sketch_commands(commands)
        if ok:
            self._last_frame = key
            self._last_frame_commands = commands
            self._schedule_heartbeat()
