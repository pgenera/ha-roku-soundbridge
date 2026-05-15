import os
import socket as _socket

import pytest
import pytest_socket
from tests.conftest import *

_SOUNDBRIDGE_HOST = os.environ.get("SOUNDBRIDGE_HOST", "")


def pytest_runtest_setup() -> None:
    """Extend the socket allowlist with the configured SoundBridge device."""
    if not _SOUNDBRIDGE_HOST:
        return
    extra = [_SOUNDBRIDGE_HOST]
    try:
        extra.append(_socket.gethostbyname(_SOUNDBRIDGE_HOST))
    except _socket.gaierror:
        pass
    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1", "localhost"] + extra)


@pytest.fixture
def soundbridge_host() -> str:
    """Return the SoundBridge host, skipping the test if not configured."""
    if not _SOUNDBRIDGE_HOST:
        pytest.skip("SOUNDBRIDGE_HOST env var not set")
    return _SOUNDBRIDGE_HOST


@pytest.fixture(autouse=True)
def expected_lingering_tasks() -> bool:
    return True


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    return True
