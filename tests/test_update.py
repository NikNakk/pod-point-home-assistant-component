"""Test pod_point sensors."""

from unittest.mock import Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# from custom_components.pod_point import async_setup_entry
from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.update import PodUpdateEntity, async_setup_entry

from .const import MOCK_CONFIG
from .test_coordinator import subject_with_data as coordinator_with_data


async def setup_updates(hass) -> tuple[MockConfigEntry, list[PodUpdateEntity]]:
    """Setup updates within the test environment"""
    coordinator = await coordinator_with_data(hass)

    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    config_entry.runtime_data = coordinator

    mock = Mock()

    await async_setup_entry(hass, config_entry, mock)

    updates: list[PodUpdateEntity] = mock.call_args_list[0][0][0]

    return (config_entry, updates)


@pytest.mark.asyncio
async def test_update_sensor(hass, bypass_get_data):
    """Tests for pod updates sensor."""
    (_, updates) = await setup_updates(hass)

    [update] = updates

    assert "pod_point_12234_PSL-123456_update" == update.unique_id

    assert "A30P-3.1.22-00001" == update.installed_version
    assert "A30P-3.1.22-00001" == update.latest_version
    assert "PSL-123456 is up to date!" == update.release_notes()

    update.pod.firmware.update_status.is_update_available = True
    assert "A30P-3.1.22-00001_UPDATE_AVAILABLE" == update.latest_version
    assert (
        "A firmware update is available for PSL-123456.\n\nExternal updating is not supported by the PodPoint APIs, please check the PodPoint mobile app for next steps."
        == update.release_notes()
    )
