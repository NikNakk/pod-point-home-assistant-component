"""Tests for Pod Home smart-charging preferences."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.select import (
    BASIC_MODE_ALWAYS_ON,
    BASIC_MODE_SCHEDULED,
    PodPointBasicChargingModeSelect,
    _tariff_prices,
)

from .const import MOCK_CONFIG


async def setup_basic_mode_select(hass):
    """Set up the integration and return its basic-mode select."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
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
    ppid = entity.pod.ppid

    coordinator.delegated_controls[ppid] = SimpleNamespace(status="INACTIVE")
    coordinator.charge_overrides[ppid] = []
    assert entity.available is True
    assert entity.current_option == BASIC_MODE_SCHEDULED

    coordinator.charge_overrides[ppid] = [SimpleNamespace(end_at=None)]
    assert entity.available is True
    assert entity.current_option == BASIC_MODE_ALWAYS_ON

    coordinator.charge_overrides[ppid] = [SimpleNamespace(end_at="timed")]
    assert entity.available is False
    assert entity.current_option is None

    coordinator.charge_overrides[ppid] = None
    assert entity.available is False
    assert entity.current_option is None

    coordinator.charge_overrides[ppid] = []
    coordinator.delegated_controls[ppid] = SimpleNamespace(status="ACTIVE")
    assert entity.available is False


@pytest.mark.asyncio
async def test_basic_mode_methods_and_refresh(hass, bypass_get_data):
    """Both options call their charger-centric methods and refresh."""
    coordinator, entity = await setup_basic_mode_select(hass)
    charger = coordinator.chargers[entity.pod.ppid]
    coordinator.api.async_set_charger_charge_mode_always_on = AsyncMock(
        return_value=[SimpleNamespace(end_at=None)]
    )
    coordinator.api.async_set_charger_charge_mode_scheduled = AsyncMock(
        return_value=True
    )
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_select_option(BASIC_MODE_ALWAYS_ON)
    coordinator.api.async_set_charger_charge_mode_always_on.assert_awaited_with(charger)
    await entity.async_select_option(BASIC_MODE_SCHEDULED)
    coordinator.api.async_set_charger_charge_mode_scheduled.assert_awaited_with(charger)
    assert coordinator.async_request_refresh.await_count == 2
