"""Test Pod Point switches."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN

from .const import MOCK_CONFIG


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_legacy_switches_not_created_for_pod_home(hass, bypass_get_data):
    """Legacy write endpoints return 403 and are not exposed for Pod Home."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("switch.psl_123456_charging_allowed") is None
    assert hass.states.get("switch.psl_123456_smart_charge_mode") is None
