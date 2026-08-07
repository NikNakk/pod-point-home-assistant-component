"""Tests for Pod Home smart-charging number controls."""

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.number import PodPointSmartChargingMaxPriceNumber

from .const import MOCK_CONFIG


@pytest.mark.asyncio
async def test_max_price_unavailable_in_basic_mode(hass, bypass_get_data):
    """Maximum price is only applicable while smart charging is active."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    entity = PodPointSmartChargingMaxPriceNumber(coordinator, entry, 0)
    ppid = entity.pod.ppid

    coordinator.delegated_controls[ppid] = SimpleNamespace(status="INACTIVE")
    assert entity.available is False
    coordinator.delegated_controls[ppid] = SimpleNamespace(status="ACTIVE")
    assert entity.available is True
