"""Tests for Pod Home smart charging switches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from podpointclient.domain import BasicChargingMode, BoostState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.switch import (
    PodPointChargeModeSwitch,
    PodPointChargeNowSwitch,
)

from .const import MOCK_CONFIG


async def setup_smart_switch(hass):
    """Set up the integration and return its smart charging switch."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    return coordinator, PodPointChargeModeSwitch(coordinator, entry, 0)


@pytest.mark.asyncio
async def test_smart_switch_state(hass, bypass_get_data):
    """ACTIVE is on and INACTIVE is off."""
    coordinator, entity = await setup_smart_switch(hass)
    coordinator.smart_charging_states[entity.charger.ppid] = SimpleNamespace(
        status="ACTIVE"
    )
    assert entity.is_on is True
    coordinator.smart_charging_states[entity.charger.ppid] = SimpleNamespace(
        status="enabled"
    )
    assert entity.is_on is True
    coordinator.smart_charging_states[entity.charger.ppid] = SimpleNamespace(
        status="INACTIVE"
    )
    assert entity.is_on is False
    coordinator.smart_charging_states.pop(entity.charger.ppid)
    assert entity.available is False
    assert entity.unique_id.endswith("_smart_charge_mode")


@pytest.mark.asyncio
async def test_smart_switch_updates_and_refreshes(hass, bypass_get_data):
    """Both switch directions call the charger API and refresh on success."""
    coordinator, entity = await setup_smart_switch(hass)
    coordinator.api.async_set_domain_smart_charging = AsyncMock(return_value=True)
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()
    coordinator.api.async_set_domain_smart_charging.assert_awaited_with(
        entity.charger, True
    )
    await entity.async_turn_off()
    coordinator.api.async_set_domain_smart_charging.assert_awaited_with(
        entity.charger, False
    )
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_failed_smart_switch_update_does_not_refresh(hass, bypass_get_data):
    """A rejected update leaves coordinator state untouched."""
    coordinator, entity = await setup_smart_switch(hass)
    coordinator.api.async_set_domain_smart_charging = AsyncMock(return_value=False)
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()
    coordinator.async_request_refresh.assert_not_awaited()
    assert entity.is_on is False


@pytest.mark.asyncio
async def test_charge_now_switch_state_and_modes(hass, bypass_get_data):
    """Charge now works in smart and scheduled modes but not Always on."""
    coordinator, mode_entity = await setup_smart_switch(hass)
    entity = PodPointChargeNowSwitch(coordinator, mode_entity.config_entry, 0)
    ppid = entity.charger.ppid

    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="ACTIVE")
    coordinator.basic_charging_modes[ppid] = BasicChargingMode.SCHEDULED
    coordinator.boost_states[ppid] = BoostState(ppid, active=False, timed=False)
    assert entity.available is True
    assert entity.is_on is False

    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="INACTIVE")
    assert entity.available is True

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.TIMED_BOOST
    coordinator.boost_states[ppid] = BoostState(ppid, active=True, timed=True)
    assert entity.available is True
    assert entity.is_on is True

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.ALWAYS_ON
    coordinator.boost_states[ppid] = BoostState(ppid, active=True, timed=False)
    assert entity.available is False
    assert entity.is_on is False


@pytest.mark.asyncio
async def test_charge_now_switch_uses_duration_and_stops(hass, bypass_get_data):
    """The switch snapshots the preset and controls its own charger."""
    coordinator, mode_entity = await setup_smart_switch(hass)
    entity = PodPointChargeNowSwitch(coordinator, mode_entity.config_entry, 0)
    ppid = entity.charger.ppid
    coordinator.charge_now_durations[ppid] = 125
    coordinator.api.async_start_boost = AsyncMock()
    coordinator.api.async_stop_boost = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.SCHEDULED
    coordinator.boost_states[ppid] = BoostState(ppid, active=False, timed=False)
    await entity.async_turn_on()
    coordinator.api.async_start_boost.assert_awaited_with(
        entity.charger, hours=2, minutes=5, seconds=0
    )

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.TIMED_BOOST
    coordinator.boost_states[ppid] = BoostState(ppid, active=True, timed=True)
    await entity.async_turn_off()
    coordinator.api.async_stop_boost.assert_awaited_with(entity.charger)
    assert coordinator.async_request_refresh.await_count == 2
