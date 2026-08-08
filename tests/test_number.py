"""Tests for Pod Home smart-charging number controls."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.number import (
    PodPointChargeNowDurationNumber,
    PodPointSmartChargingMaxPriceNumber,
)

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


@pytest.mark.asyncio
async def test_charge_now_duration_is_a_persistent_preset(hass, bypass_get_data):
    """Changing the preset does not mutate an active timed override."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    entity = PodPointChargeNowDurationNumber(coordinator, entry, 0)
    entity.async_write_ha_state = Mock()
    ppid = entity.pod.ppid

    assert entity.native_value == 60
    coordinator.charge_overrides[ppid] = [SimpleNamespace(end_at="timed")]
    assert entity.available is True
    await entity.async_set_native_value(90)
    assert entity.native_value == 90
    assert coordinator.charge_now_durations[ppid] == 90
    assert coordinator.charge_overrides[ppid][0].end_at == "timed"

    coordinator.charge_overrides[ppid] = [SimpleNamespace(end_at=None)]
    assert entity.available is False
