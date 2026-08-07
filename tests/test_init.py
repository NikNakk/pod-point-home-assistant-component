"""Test pod_point setup process."""

from homeassistant.config_entries import ConfigEntryState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point import (
    PodPointDataUpdateCoordinator,
    async_reload_entry,
    async_unload_entry,
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
    assert DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]
    assert (
        type(hass.data[DOMAIN][config_entry.entry_id]) == PodPointDataUpdateCoordinator
    )

    # Reload the entry and assert that the data from above is still there
    assert await async_reload_entry(hass, config_entry) is None
    assert DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]
    assert (
        type(hass.data[DOMAIN][config_entry.entry_id]) == PodPointDataUpdateCoordinator
    )

    # Unload the entry and verify that the data has been removed
    assert await async_unload_entry(hass, config_entry)
    assert DOMAIN not in hass.data


# Test that setup is retried when the config entry is not ready
@pytest.mark.asyncio
async def test_setup_entry_exception(hass, error_on_get_data):
    """Test ConfigEntryNotReady when API raises an exception during entry setup."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)

    # The config entry manager converts ConfigEntryNotReady into a setup retry.
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
