"""Test pod_point binary sensors."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory
from podpointclient.domain import ChargerState, NormalizedStateValue, StateValue
from podpointclient.remote_lock import RemoteLock
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.binary_sensor import (
    PodPointCableConnectionSensor,
    PodPointCloudConnectionSensor,
    PodPointRemoteLockSensor,
    PodPointSmartChargingSensor,
    async_setup_entry,
)
from custom_components.pod_point.const import ATTR_STATE, DOMAIN

from .const import MOCK_CONFIG
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
    assert "pod_point_PSL-123456_cloud_connection" == status.unique_id
    assert "Cloud Connection" == status.name

    status.coordinator.charger_states[status.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE),
        charging=NormalizedStateValue(StateValue.AVAILABLE),
    )
    assert status.is_on is True

    status.coordinator.charger_states[status.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.OFFLINE),
        charging=NormalizedStateValue(StateValue.AVAILABLE),
    )
    assert status.is_on is False

    assert status.is_on is False
    assert "mdi:cloud-off" == status.icon


@pytest.mark.asyncio
async def test_cable_connection_sensor(hass, bypass_get_data):
    """Tests for pod status sensor."""
    _, sensors = await setup_sensors(hass)

    [status, _] = sensors

    assert BinarySensorDeviceClass.PLUG == status.device_class
    assert "pod_point_PSL-123456_cable_status" == status.unique_id
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
    smart_charging = SimpleNamespace(status="ENABLED")
    coordinator.smart_charging_states[pod.ppid] = smart_charging
    sensor = PodPointSmartChargingSensor(coordinator, config_entry, 0)

    assert sensor.is_on is True
    smart_charging.status = "inactive"
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_remote_lock_requires_a_meaningful_off_mode_state(hass, bypass_get_data):
    """An empty remote-lock response does not imply charger support."""
    config_entry, _ = await setup_sensors(hass)
    coordinator = config_entry.runtime_data
    charger = coordinator.data[0]

    coordinator.remote_locks[charger.ppid] = RemoteLock({})
    empty_response_entities = Mock()
    await async_setup_entry(hass, config_entry, empty_response_entities)
    assert len(empty_response_entities.call_args_list[0][0][0]) == 2

    coordinator.remote_locks[charger.ppid] = RemoteLock({"offMode": False})
    supported_entities = Mock()
    await async_setup_entry(hass, config_entry, supported_entities)
    entities = supported_entities.call_args_list[0][0][0]
    remote_lock = next(
        entity for entity in entities if isinstance(entity, PodPointRemoteLockSensor)
    )
    assert remote_lock.name == "Remote lock"
    assert remote_lock.unique_id == "pod_point_PSL-123456_off_mode"
    assert remote_lock.is_on is False
