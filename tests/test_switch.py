"""Tests for Pod Home smart charging switches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.switch import PodPointChargeModeSwitch

from .const import MOCK_CONFIG


async def setup_smart_switch(hass):
    """Set up the integration and return its smart charging switch."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return coordinator, PodPointChargeModeSwitch(coordinator, entry, 0)


@pytest.mark.asyncio
async def test_smart_switch_state(hass, bypass_get_data):
    """ACTIVE is on and INACTIVE is off."""
    coordinator, entity = await setup_smart_switch(hass)
    coordinator.delegated_controls[entity.pod.ppid] = SimpleNamespace(status="ACTIVE")
    assert entity.is_on is True
    coordinator.delegated_controls[entity.pod.ppid] = SimpleNamespace(status="INACTIVE")
    assert entity.is_on is False
    assert entity.unique_id.endswith("_smart_charge_mode")


@pytest.mark.asyncio
async def test_smart_switch_updates_and_refreshes(hass, bypass_get_data):
    """Both switch directions call the charger API and refresh on success."""
    coordinator, entity = await setup_smart_switch(hass)
    coordinator.api.async_set_charger_smart_charging = AsyncMock(return_value=True)
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()
    coordinator.api.async_set_charger_smart_charging.assert_awaited_with(
        coordinator.chargers[entity.pod.ppid], True
    )
    await entity.async_turn_off()
    coordinator.api.async_set_charger_smart_charging.assert_awaited_with(
        coordinator.chargers[entity.pod.ppid], False
    )
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_failed_smart_switch_update_does_not_refresh(hass, bypass_get_data):
    """A rejected update leaves coordinator state untouched."""
    coordinator, entity = await setup_smart_switch(hass)
    coordinator.api.async_set_charger_smart_charging = AsyncMock(return_value=False)
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()
    coordinator.async_request_refresh.assert_not_awaited()
    assert entity.is_on is False
