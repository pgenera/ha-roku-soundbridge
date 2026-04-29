"""Test the Roku SoundBridge config flow."""

from unittest.mock import patch

import pytest

from custom_components.roku_soundbridge.config_flow import CannotConnect
from custom_components.roku_soundbridge.const import DOMAIN
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


@pytest.mark.asyncio
async def test_user_form(hass: HomeAssistant) -> None:
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    with (
        patch(
            "custom_components.roku_soundbridge.config_flow.validate_input",
            return_value={
                "title": "Roku SoundBridge (127.0.0.1)",
                "unique_id": "00:11:22:33:44:55",
            },
        ),
        patch(
            "custom_components.roku_soundbridge.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "127.0.0.1",
                "port": 5555,
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Roku SoundBridge (127.0.0.1)"
    assert result2["data"] == {
        "host": "127.0.0.1",
        "port": 5555,
    }
    assert result2["result"].unique_id == "00:11:22:33:44:55"
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.asyncio
async def test_user_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test we handle cannot connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.roku_soundbridge.config_flow.validate_input",
        side_effect=CannotConnect,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "127.0.0.1",
                "port": 5555,
            },
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_user_form_unknown_error(hass: HomeAssistant) -> None:
    """Test handling unknown errors in the user form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.roku_soundbridge.config_flow.validate_input",
        side_effect=Exception("Unknown error"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "127.0.0.1",
                "port": 5555,
            },
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}
