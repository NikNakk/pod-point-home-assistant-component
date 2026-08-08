"""Tests for Pod Home smart-charging preferences."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from podpointclient.domain import BasicChargingMode
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.select import (
    BASIC_MODE_ALWAYS_ON,
    BASIC_MODE_SCHEDULED,
    PodPointBasicChargingModeSelect,
    PodPointSmartChargingPrioritySelect,
    _tariff_prices,
)

from .const import MOCK_CONFIG


async def setup_basic_mode_select(hass):
    """Set up the integration and return its basic-mode select."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    return coordinator, PodPointBasicChargingModeSelect(coordinator, entry, 0)


def test_tariff_prices_uses_all_period_rates():
    """Priority choices use the true minimum and maximum tariff rates."""
    coordinator = SimpleNamespace(
        tariffs={
            "PSL-123456": [
                SimpleNamespace(
                    tariff_info=[
                        SimpleNamespace(price=0.30),
                        SimpleNamespace(price=0.07),
                        SimpleNamespace(price=0.18),
                    ]
                )
            ]
        }
    )

    prices = _tariff_prices(coordinator, "PSL-123456")
    assert min(prices) == 0.07
    assert max(prices) == 0.30


@pytest.mark.asyncio
async def test_basic_mode_state_and_availability(hass, bypass_get_data):
    """Basic mode is unavailable for smart mode and timed boosts."""
    coordinator, entity = await setup_basic_mode_select(hass)
    ppid = entity.charger.ppid

    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="INACTIVE")
    coordinator.basic_charging_modes[ppid] = BasicChargingMode.SCHEDULED
    assert entity.available is True
    assert entity.current_option == BASIC_MODE_SCHEDULED

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.ALWAYS_ON
    assert entity.available is True
    assert entity.current_option == BASIC_MODE_ALWAYS_ON

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.TIMED_BOOST
    assert entity.available is False
    assert entity.current_option is None

    coordinator.basic_charging_modes[ppid] = None
    assert entity.available is False
    assert entity.current_option is None

    coordinator.basic_charging_modes[ppid] = BasicChargingMode.SCHEDULED
    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="ACTIVE")
    assert entity.available is False


@pytest.mark.asyncio
async def test_smart_priority_unavailable_in_basic_mode(hass, bypass_get_data):
    """Smart-only preferences are unavailable while basic mode is active."""
    coordinator, basic_entity = await setup_basic_mode_select(hass)
    entity = PodPointSmartChargingPrioritySelect(
        coordinator, basic_entity.config_entry, 0
    )
    ppid = entity.charger.ppid

    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="INACTIVE")
    assert entity.available is False
    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="ACTIVE")
    assert entity.available is True


@pytest.mark.asyncio
async def test_basic_mode_methods_and_refresh(hass, bypass_get_data):
    """Both options call their charger-centric methods and refresh."""
    coordinator, entity = await setup_basic_mode_select(hass)
    charger = entity.charger
    coordinator.api.async_set_basic_charging_mode = AsyncMock(
        side_effect=lambda _, mode: mode
    )
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_select_option(BASIC_MODE_ALWAYS_ON)
    coordinator.api.async_set_basic_charging_mode.assert_any_await(
        charger, BasicChargingMode.ALWAYS_ON
    )
    await entity.async_select_option(BASIC_MODE_SCHEDULED)
    coordinator.api.async_set_basic_charging_mode.assert_any_await(
        charger, BasicChargingMode.SCHEDULED
    )
    assert coordinator.async_request_refresh.await_count == 2
