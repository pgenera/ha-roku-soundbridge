"""Tests for the SoundBridge mirror-display controller."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roku_soundbridge.mirror import MirrorDisplayController
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

SOURCE_ID = "media_player.source"
OWN_ID = "media_player.soundbridge_test"


@pytest.fixture
def mock_client():
    """Mock RCP client with the surface the controller touches."""
    client = MagicMock()
    client.host = "127.0.0.1"
    client.port = 5555
    client.is_connected = True
    client.power_state = "on"
    client.display_width = 512
    client.display_height = 32
    client.turn_on = AsyncMock()
    client.turn_off = AsyncMock()
    client.wait_for_power_on = AsyncMock(return_value=True)
    client.send_sketch_commands = AsyncMock(return_value=True)
    return client


def _set_source(hass: HomeAssistant, state: str, **attrs) -> None:
    hass.states.async_set(SOURCE_ID, state, attrs)


async def _flush(hass: HomeAssistant) -> None:
    """Yield to the loop so background tasks scheduled by callbacks can run."""
    await hass.async_block_till_done()


@pytest.fixture
def controller(hass: HomeAssistant, mock_client):
    """Build a configured-but-not-enabled controller."""
    ctrl = MirrorDisplayController(hass, mock_client, lambda: OWN_ID)
    ctrl.configure(SOURCE_ID, idle_seconds=30)
    return ctrl


@pytest.mark.asyncio
async def test_disabled_ignores_state_changes(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """When the switch is off, source state changes do nothing."""
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await _flush(hass)
    mock_client.send_sketch_commands.assert_not_called()
    mock_client.turn_on.assert_not_called()


@pytest.mark.asyncio
async def test_playing_renders_title_and_artist(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """Source PLAYING → render two-line frame, no standby."""
    _set_source(hass, MediaPlayerState.PLAYING, media_title="Hello", media_artist="World")
    await controller.set_enabled(True)
    await _flush(hass)

    mock_client.send_sketch_commands.assert_called_once()
    args, _ = mock_client.send_sketch_commands.call_args
    commands = args[0]
    # First command always resets the frame before drawing.
    assert commands[0] == "clear"
    assert controller.mirror_active is True


@pytest.mark.asyncio
async def test_wakes_from_standby_on_play_transition(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """If SB is in standby and source becomes PLAYING, wake then render."""
    mock_client.power_state = "standby"

    # Enable while source is OFF — no wake.
    _set_source(hass, MediaPlayerState.OFF)
    await controller.set_enabled(True)
    await _flush(hass)
    mock_client.turn_on.assert_not_called()

    # Now transition to PLAYING — should wake.
    mock_client.power_state = "standby"  # still standby until we "wake"
    def _fake_turn_on():
        mock_client.power_state = "on"
        return AsyncMock()()
    mock_client.turn_on.side_effect = _fake_turn_on
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await _flush(hass)
    mock_client.turn_on.assert_called()
    mock_client.send_sketch_commands.assert_called()


@pytest.mark.asyncio
async def test_paused_then_idle_timer_sends_standby(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """Source PAUSED → schedule timer → on expiry, send standby."""
    controller.configure(SOURCE_ID, idle_seconds=5)
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await controller.set_enabled(True)
    await _flush(hass)

    _set_source(hass, MediaPlayerState.PAUSED)
    await _flush(hass)
    mock_client.turn_off.assert_not_called()  # not yet, timer pending

    # Fast-forward 6 seconds.
    import datetime as dt

    from tests.common import async_fire_time_changed  # noqa: TID251

    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=6))
    await _flush(hass)
    mock_client.turn_off.assert_called_once()
    assert controller.mirror_active is False


@pytest.mark.asyncio
async def test_source_off_standby_immediately(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """Source OFF → standby with no idle timer."""
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await controller.set_enabled(True)
    await _flush(hass)

    _set_source(hass, MediaPlayerState.OFF)
    await _flush(hass)
    mock_client.turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_universal_self_active_skips(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """If the source is a universal MP and active_child is us, skip mirror."""
    _set_source(
        hass,
        MediaPlayerState.PLAYING,
        media_title="local",
        media_artist="local",
        active_child=OWN_ID,
    )
    await controller.set_enabled(True)
    await _flush(hass)
    mock_client.send_sketch_commands.assert_not_called()
    mock_client.turn_on.assert_not_called()


@pytest.mark.asyncio
async def test_disconnected_silently_skips(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """When the SB is unreachable, no exceptions, no calls."""
    mock_client.is_connected = False
    mock_client.power_state = "standby"
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await controller.set_enabled(True)
    await _flush(hass)
    mock_client.turn_on.assert_not_called()
    mock_client.send_sketch_commands.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_source_treated_as_off(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """Source going UNAVAILABLE drives the SB to standby like OFF."""
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await controller.set_enabled(True)
    await _flush(hass)
    mock_client.send_sketch_commands.reset_mock()

    _set_source(hass, STATE_UNAVAILABLE)
    await _flush(hass)
    mock_client.turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_redundant_frame_not_resent(
    hass: HomeAssistant, mock_client, controller
) -> None:
    """A repeat state event with the same title/artist doesn't re-render."""
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await controller.set_enabled(True)
    await _flush(hass)
    assert mock_client.send_sketch_commands.call_count == 1

    # Re-emit the same state.
    _set_source(hass, MediaPlayerState.PLAYING, media_title="t", media_artist="a")
    await _flush(hass)
    assert mock_client.send_sketch_commands.call_count == 1
