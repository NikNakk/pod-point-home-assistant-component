"""Test pod_point setup process."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from podpointclient.errors import ApiConnectionError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point import (
    PodPointDataUpdateCoordinator,
    async_reload_entry,
)
from custom_components.pod_point.const import DOMAIN

from .const import MOCK_CONFIG


# We can pass fixtures as defined in conftest.py to tell pytest to use the fixture
# for a given test. We can also leverage fixtures and mocks that are available in
# Home Assistant using the pytest_homeassistant_custom_component plugin.
# Assertions allow you to verify that the return value of whatever is on the left
# side of the assertion matches with the right side.
@pytest.mark.asyncio
async def test_setup_unload_and_reload_entry(hass, bypass_get_data):
    """Test entry setup and unload."""
    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)

    # Set up the entry and assert that the values set during setup are where we expect
    # them to be. Because we have patched the BlueprintDataUpdateCoordinator.async_get_data
    # call, no code from custom_components/integration_blueprint/api.py actually runs.

    await hass.config_entries.async_setup(config_entry.entry_id)
    assert isinstance(config_entry.runtime_data, PodPointDataUpdateCoordinator)

    # Reload the entry and assert that the data from above is still there
    assert await async_reload_entry(hass, config_entry) is None
    assert isinstance(config_entry.runtime_data, PodPointDataUpdateCoordinator)

    # Unload the entry and verify that the data has been removed
    assert await hass.config_entries.async_unload(config_entry.entry_id)


@pytest.mark.asyncio
async def test_cached_optional_data_preserves_entity_discovery(hass, bypass_get_data):
    """Fast refreshes retain entities discovered from the startup slow data."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    preferences = SimpleNamespace(max_price=0.15)
    wallet = SimpleNamespace(rewards={}, allowance={}, payments={})
    with (
        patch(
            "podpointclient.client.PodPointClient.async_get_smart_charging_preferences",
            return_value=preferences,
        ) as get_preferences,
        patch(
            "podpointclient.client.PodPointClient.async_get_reward_wallet",
            return_value=wallet,
        ) as get_wallet,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        before = {
            entry.unique_id
            for entry in registry.entities.values()
            if entry.config_entry_id == config_entry.entry_id
        }

        await config_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        after = {
            entry.unique_id
            for entry in registry.entities.values()
            if entry.config_entry_id == config_entry.entry_id
        }

    assert after == before
    assert any("smart_charging_max_price" in unique_id for unique_id in after)
    assert any("reward_points" in unique_id for unique_id in after)
    get_preferences.assert_awaited_once()
    get_wallet.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_registry_identity_is_migrated_in_place(hass, bypass_get_data):
    """Legacy IDs move to PPID while entity/device registry IDs remain stable."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "123456789")},
    )
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "pod_point_12234_PSL-123456_status",
        suggested_object_id="pod_point_status",
        config_entry=config_entry,
        device_id=device.id,
    )
    account = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "1a756c9b-dfac-4c2a-ba13-9cdcc2399366",
        suggested_object_id="pod_point_balance",
        config_entry=config_entry,
        original_name="Pod Point Balance",
    )
    original_entity_id = existing.entity_id
    original_device_id = existing.device_id

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    migrated = registry.async_get(original_entity_id)
    assert migrated is not None
    assert migrated.unique_id == "pod_point_PSL-123456_status"
    assert migrated.entity_id == original_entity_id
    assert migrated.device_id == original_device_id
    assert (DOMAIN, "PSL-123456") in device_registry.async_get(
        original_device_id
    ).identifiers
    assert registry.async_get(account.entity_id).unique_id == (
        "pod_point_test_account_balance"
    )


@pytest.mark.asyncio
async def test_setup_entry_auth_error(hass, error_on_get_data):
    """Test an authentication failure starts reauthentication."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(hass.config_entries.flow.async_progress()) == 1


@pytest.mark.asyncio
async def test_setup_entry_connection_error(hass, bypass_get_data):
    """Test a transient API connection failure schedules a setup retry."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)

    with patch(
        "podpointclient.client.PodPointClient.async_get_user",
        side_effect=ApiConnectionError("connection failed"),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
