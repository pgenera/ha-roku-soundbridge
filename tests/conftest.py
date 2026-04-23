"""Conftest for Roku SoundBridge tests."""

pytest_plugins = ["tests.conftest"]

from unittest.mock import MagicMock, patch
import pytest

from custom_components.roku_soundbridge.const import DOMAIN

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield

@pytest.fixture
def mock_setup_entry():
    """Mock setting up a config entry."""
    with patch(
        "custom_components.roku_soundbridge.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup

@pytest.fixture
def mock_client():
    """Mock the RcpClient."""
    with patch("custom_components.roku_soundbridge.protocol.RcpClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.connect.return_value = True
        instance.mac_address = "00:11:22:33:44:55"
        instance.is_connected = True
        instance.state = "stopped"
        instance.title = None
        instance.artist = None
        instance.album = None
        instance.volume = 0
        instance.mute = False
        instance.duration = 0
        instance.position = 0
        instance.position_updated_at = None
        instance.url = None
        yield instance
