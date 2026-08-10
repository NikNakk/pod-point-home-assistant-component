"""Tests for Pod Home smart-charging number controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from podpointclient.domain import BasicChargingMode, BoostState
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
    ppid = entity.charger.ppid

    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="INACTIVE")
    assert entity.available is False
    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="ACTIVE")
    assert entity.available is True


@pytest.mark.asyncio
async def test_max_price_update_refreshes_preferences_immediately(
    hass, bypass_get_data
):
    """A successful maximum-price write immediately replaces its slow cache."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    entity = PodPointSmartChargingMaxPriceNumber(coordinator, entry, 0)
    ppid = entity.charger.ppid
    preferences = SimpleNamespace(max_price=0.2786)
    coordinator.api.async_set_charger_max_price = AsyncMock(return_value=True)
    coordinator.api.async_get_charger_preferences = AsyncMock(return_value=preferences)

    assert entity.native_step == 0.0001
    await entity.async_set_native_value(0.2786)

    charger = entity.charger
    coordinator.api.async_set_charger_max_price.assert_awaited_once_with(
        charger, 0.2786
    )
    coordinator.api.async_get_charger_preferences.assert_awaited_once_with(charger)
    assert coordinator.smart_charging_preferences[ppid] is preferences
    assert entity.native_value == 0.2786


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
    ppid = entity.charger.ppid

    assert entity.native_value == 60
    assert entity.native_step == 1
    coordinator.basic_charging_modes[ppid] = BasicChargingMode.TIMED_BOOST
    coordinator.boost_states[ppid] = BoostState(ppid, active=True, timed=True)
    assert entity.available is True
    await entity.async_set_native_value(90)
    assert entity.native_value == 90
    assert coordinator.charge_now_durations[ppid] == 90
    assert coordinator.boost_states[ppid].timed is True

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.ALWAYS_ON
    assert entity.available is False
