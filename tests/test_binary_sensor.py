"""Test pod_point binary sensors."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory
from podpointclient.connectivity_status import ConnectivityStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.binary_sensor import (
    PodPointCableConnectionSensor,
    PodPointCloudConnectionSensor,
    PodPointSmartChargingSensor,
    async_setup_entry,
)
from custom_components.pod_point.const import ATTR_STATE, DOMAIN

from .const import MOCK_CONFIG
from .fixtures import CONNECTIVITY_STATUS_COMPLETE_FIXTURE
from .test_coordinator import subject_with_data as coordinator_with_data


async def setup_sensors(hass) -> tuple[MockConfigEntry, list[BinarySensorEntity]]:
    """Setup sensors within the test environment"""
    coordinator = await coordinator_with_data(hass)

    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    config_entry.runtime_data = coordinator

    mock = Mock()

    await async_setup_entry(hass, config_entry, mock)

    sensors: list[PodPointCableConnectionSensor | PodPointCloudConnectionSensor] = (
        mock.call_args_list[0][0][0]
    )

    return (config_entry, sensors)


@pytest.mark.asyncio
async def test_sensor_creation(hass, bypass_get_data):
    """Test that the expected number of sensors is created"""

    _, sensors = await setup_sensors(hass)

    assert 2 == len(sensors)


@pytest.mark.asyncio
async def test_cloud_connection_sensor(hass, bypass_get_data):
    """Tests for pod status sensor."""
    _, sensors = await setup_sensors(hass)

    [_, status] = sensors

    assert BinarySensorDeviceClass.CONNECTIVITY == status.device_class
    assert EntityCategory.DIAGNOSTIC == status.entity_category
    assert "pod_point_12234_PSL-123456_cloud_connection" == status.unique_id
    assert "Cloud Connection" == status.name

    status.coordinator.connectivity_v2[status.pod.ppid] = SimpleNamespace(
        connection_state="Online"
    )
    assert status.is_on is True

    status.coordinator.connectivity_v2[status.pod.ppid].connection_state = "Offline"
    assert status.is_on is False

    status.coordinator.connectivity_v2.clear()

    status.pod.connectivity_status = ConnectivityStatus(
        CONNECTIVITY_STATUS_COMPLETE_FIXTURE
    )
    assert status.is_on is True
    assert "mdi:cloud-check-variant" == status.icon

    status.pod.connectivity_status.evses[0].connectivity_state.connectivity_status = (
        "FOO"
    )
    assert status.is_on is False
    assert "mdi:cloud-off" == status.icon


@pytest.mark.asyncio
async def test_cable_connection_sensor(hass, bypass_get_data):
    """Tests for pod status sensor."""
    _, sensors = await setup_sensors(hass)

    [status, _] = sensors

    assert BinarySensorDeviceClass.PLUG == status.device_class
    assert "pod_point_12234_PSL-123456_cable_status" == status.unique_id
    assert "Cable Status" == status.name

    status.extra_attrs[ATTR_STATE] = "charging"
    assert status.is_on is True

    status.extra_attrs[ATTR_STATE] = "available"
    assert status.is_on is False

    status.extra_attrs[ATTR_STATE] = "connected-waiting-for-schedule"
    assert status.is_on is True

    status.extra_attrs[ATTR_STATE] = "suspended-evse"
    assert status.is_on is True

    status.extra_attrs[ATTR_STATE] = "suspended-ev"
    assert status.is_on is True

    status.extra_attrs[ATTR_STATE] = "foo"
    assert status.is_on is False


@pytest.mark.asyncio
async def test_pod_home_smart_charging_sensor_uses_charger_status(
    hass, bypass_get_data
):
    """The Pod Home sensor uses the status embedded in charger metadata."""
    config_entry, _ = await setup_sensors(hass)
    coordinator = config_entry.runtime_data
    pod = coordinator.data[0]
    charger = SimpleNamespace(ppid=pod.ppid, delegated_control_status="ENABLED")
    coordinator.chargers[pod.ppid] = charger
    sensor = PodPointSmartChargingSensor(coordinator, config_entry, 0)

    assert sensor.is_on is True
    charger.delegated_control_status = "inactive"
    assert sensor.is_on is False
