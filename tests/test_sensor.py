"""Test pod_point sensors."""

import asyncio
from datetime import datetime, timedelta
from typing import List, Union
from unittest.mock import Mock, call, patch

import aiohttp
from homeassistant.components import switch
from homeassistant.components.sensor import (
    SensorStateClass,
    SensorDeviceClass,
)
from homeassistant.components.switch import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.const import (
    ATTR_ENTITY_ID,
    UnitOfEnergy,
)
import homeassistant.helpers.aiohttp_client as client
from podpointclient.charge_override import ChargeOverride
from podpointclient.pod import Pod
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point import async_setup_entry
from custom_components.pod_point.const import (
    ATTR_STATE,
    ATTR_STATE_AVAILABLE,
    ATTR_STATE_CHARGING,
    ATTR_STATE_CONNECTED_WAITING,
    ATTR_STATE_IDLE,
    ATTR_STATE_OUT_OF_SERVICE,
    ATTR_STATE_PENDING,
    ATTR_STATE_SUSPENDED_EV,
    ATTR_STATE_SUSPENDED_EVSE,
    ATTR_STATE_UNAVAILABLE,
    ATTR_STATE_WAITING,
    DOMAIN,
    SENSOR,
    SWITCH,
)
from custom_components.pod_point.sensor import (
    PodPointAccountBalanceEntity,
    PodPointChargeOverrideEntity,
    PodPointHomeAppVehicleSensor,
    PodPointRewardWalletEntity,
    PodPointSensor,
    PodPointTotalEnergySensor,
    async_setup_entry,
)

from .const import MOCK_CONFIG
from .fixtures import POD_COMPLETE_FIXTURE
from .test_coordinator import subject_with_data as coordinator_with_data


async def setup_sensors(hass) -> List[PodPointSensor]:
    """Setup sensors within the test environment"""
    coordinator = await coordinator_with_data(hass)

    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    hass.data[DOMAIN] = {}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    mock = Mock()

    await async_setup_entry(hass, config_entry, mock)

    print(mock.call_args_list)
    sensors: List[Union(PodPointSensor, PodPointAccountBalanceEntity)] = (
        mock.call_args_list[0][0][0]
    )

    return (config_entry, sensors)


@pytest.mark.asyncio
async def test_sensor_creation(hass, bypass_get_data):
    """Test that the expected number of sensors is created"""

    (_, sensors) = await setup_sensors(hass)

    assert 18 == len(sensors)


@pytest.mark.asyncio
async def test_status_pod_sensor(hass, bypass_get_data):
    """Tests for pod status sensor."""
    (_, sensors) = await setup_sensors(hass)

    status = sensors[0]

    assert SensorDeviceClass.ENUM == status.device_class
    assert "pod_point_12234_PSL-123456_status" == status.unique_id
    assert "Status" == status.name
    assert "charging" == status.native_value

    assert "mdi:ev-plug-type2" == status.icon
    assert "/api/pod_point/static/uc-03.png" == status.entity_picture

    status.pod.model.name = "XX-UC-XX-XX"
    assert "mdi:ev-plug-type2" == status.icon
    assert "/api/pod_point/static/uc.png" == status.entity_picture

    status.pod.model.name = "XX-2C-XX-XX"
    assert "mdi:ev-plug-type2" == status.icon
    assert "/api/pod_point/static/2c.png" == status.entity_picture

    status.pod.model.name = "XX-1C-XX-XX"
    assert "mdi:ev-plug-type1" == status.icon
    assert "/api/pod_point/static/2c.png" == status.entity_picture

    status.pod.model.name = "XX-XX-XX-XX"
    assert "mdi:ev-plug-type2" == status.icon
    assert "/api/pod_point/static/xx.png" == status.entity_picture

    assert [
        ATTR_STATE_AVAILABLE,
        ATTR_STATE_UNAVAILABLE,
        ATTR_STATE_CHARGING,
        ATTR_STATE_OUT_OF_SERVICE,
        ATTR_STATE_WAITING,
        ATTR_STATE_CONNECTED_WAITING,
        ATTR_STATE_SUSPENDED_EV,
        ATTR_STATE_SUSPENDED_EVSE,
        ATTR_STATE_IDLE,
        ATTR_STATE_PENDING,
    ] == status.options


@pytest.mark.asyncio
async def test_total_energy_pod_sensor(hass, bypass_get_data):
    """Tests for pod total eergy sensor."""
    (_, sensors) = await setup_sensors(hass)

    total_energy: PodPointTotalEnergySensor
    total_energy = sensors[2]

    total_energy.async_write_ha_state = Mock()
    total_energy._handle_coordinator_update()
    assert {
        "attribution": "Data provided by https://pod-point.com/",
        "current_kwh": 0.0,
        "id": 12234,
        "integration": "pod_point",
        "suggested_area": "Outside",
        "total_kwh": 0.0,
        "total_kwh_difference": 0.0,
    } == total_energy.extra_attrs

    assert "pod_point_12234_PSL-123456_status_total_energy" == total_energy.unique_id

    assert "Total Energy" == total_energy.name

    assert SensorDeviceClass.ENERGY == total_energy.device_class
    assert SensorStateClass.TOTAL_INCREASING == total_energy.state_class
    assert 0.0 == total_energy.native_value
    assert UnitOfEnergy.KILO_WATT_HOUR == total_energy.native_unit_of_measurement
    assert "mdi:lightning-bolt-outline" == total_energy.icon
    assert False == total_energy.is_on

    total_energy.extra_attrs[ATTR_STATE] = "charging"
    assert "mdi:lightning-bolt" == total_energy.icon
    assert True == total_energy.is_on

    assert total_energy.options is None


@pytest.mark.asyncio
async def test_current_energy_pod_sensor(hass, bypass_get_data):
    """Tests for pod current energy sensor."""
    (_, sensors) = await setup_sensors(hass)

    current_energy = sensors[3]

    assert (
        "pod_point_12234_PSL-123456_status_total_energy_current_charge_energy"
        == current_energy.unique_id
    )

    assert "Current Energy" == current_energy.name

    assert SensorDeviceClass.ENERGY == current_energy.device_class
    assert SensorStateClass.TOTAL == current_energy.state_class
    assert 0.0 == current_energy.native_value
    assert "mdi:car-electric" == current_energy.icon

    current_energy.extra_state_attributes[ATTR_STATE] = "foo"
    assert "mdi:car" == current_energy.icon

    assert current_energy.options is None


@pytest.mark.asyncio
async def test_total_charge_time_pod_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    charge_time = sensors[1]

    assert "pod_point_12234_PSL-123456_charge_time" == charge_time.unique_id

    assert "Completed Charge Time" == charge_time.name

    assert "duration" == charge_time.device_class
    assert 0 == charge_time.native_value
    assert {
        "formatted": "0:00:00",
        "long": "0s",
        "raw": 0,
    } == charge_time.extra_state_attributes
    assert "mdi:timer" == charge_time.icon

    charge_time.pod.total_charge_seconds = 61
    assert 61 == charge_time.native_value
    assert {
        "formatted": "0:01:01",
        "long": "1 minute",
        "raw": 61,
    } == charge_time.extra_state_attributes

    charge_time.pod.total_charge_seconds = 9945
    assert 9945 == charge_time.native_value
    assert {
        "formatted": "2:45:45",
        "long": "2 hours, 45 minutes, 45 seconds",
        "raw": 9945,
    } == charge_time.extra_state_attributes

    charge_time.pod.total_charge_seconds = 175545
    assert 175545 == charge_time.native_value
    assert {
        "formatted": "2 days, 0:45:45",
        "long": "2 days, 45 minutes, 45 seconds",
        "raw": 175545,
    } == charge_time.extra_state_attributes

    charge_time.pod.total_charge_seconds = 2764800
    assert 2764800 == charge_time.native_value
    assert {
        "formatted": "32 days, 0:00:00",
        "long": "1 month, 2 days",
        "raw": 2764800,
    } == charge_time.extra_state_attributes

    charge_time.pod.total_charge_seconds = 66355200
    assert 66355200 == charge_time.native_value
    assert {
        "formatted": "768 days, 0:00:00",
        "long": "2 years, 1 month, 8 days",
        "raw": 66355200,
    } == charge_time.extra_state_attributes


@pytest.mark.asyncio
async def test_total_cost_pod_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    total_cost = sensors[6]

    assert "pod_point_12234_PSL-123456_total_cost" == total_cost.unique_id

    assert "Total Cost" == total_cost.name

    assert "monetary" == total_cost.device_class
    assert 0 == total_cost.native_value
    assert {
        "amount": 0.0,
        "currency": "GBP",
        "formatted": "0.0 GBP",
        "raw": 0,
    } == total_cost.extra_state_attributes
    assert "mdi:cash-multiple" == total_cost.icon

    total_cost.pod.total_cost = 61
    assert 0.61 == total_cost.native_value
    assert {
        "amount": 0.61,
        "currency": "GBP",
        "formatted": "0.61 GBP",
        "raw": 61,
    } == total_cost.extra_state_attributes

    total_cost.pod.total_cost = 9945
    assert 99.45 == total_cost.native_value
    assert {
        "amount": 99.45,
        "currency": "GBP",
        "formatted": "99.45 GBP",
        "raw": 9945,
    } == total_cost.extra_state_attributes

    total_cost.pod.total_cost = 175545
    assert 1755.45 == total_cost.native_value
    assert {
        "amount": 1755.45,
        "currency": "GBP",
        "formatted": "1755.45 GBP",
        "raw": 175545,
    } == total_cost.extra_state_attributes

    total_cost.pod.total_cost = 2764800
    assert 27648.00 == total_cost.native_value
    assert {
        "amount": 27648.0,
        "currency": "GBP",
        "formatted": "27648.0 GBP",
        "raw": 2764800,
    } == total_cost.extra_state_attributes

    assert total_cost.currency == "GBP"


@pytest.mark.asyncio
async def test_last_charge_cost_pod_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    last_charge = sensors[7]

    assert (
        "pod_point_12234_PSL-123456_last_complete_charge_cost" == last_charge.unique_id
    )

    assert "Last Completed Charge Cost" == last_charge.name

    assert "monetary" == last_charge.device_class
    assert 0 == last_charge.native_value
    assert {
        "amount": 0.0,
        "currency": "GBP",
        "formatted": "0.0 GBP",
        "raw": 0,
    } == last_charge.extra_state_attributes
    assert "mdi:cash" == last_charge.icon

    assert 0.0 == last_charge.native_value
    assert {
        "amount": 0.0,
        "currency": "GBP",
        "formatted": "0.0 GBP",
        "raw": 0,
    } == last_charge.extra_state_attributes

    setattr(last_charge.pod, "last_charge_cost", 9945)
    assert 99.45 == last_charge.native_value
    assert {
        "amount": 99.45,
        "currency": "GBP",
        "formatted": "99.45 GBP",
        "raw": 9945,
    } == last_charge.extra_state_attributes

    assert last_charge.currency == "GBP"


@pytest.mark.asyncio
async def test_balance_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    balance = sensors[12]

    assert "1a756c9b-dfac-4c2a-ba13-9cdcc2399366" == balance.unique_id

    assert "Pod Point Balance" == balance.name

    assert "monetary" == balance.device_class
    assert 1.73 == balance.native_value
    assert balance.extra_state_attributes is None
    assert "mdi:account-cash" == balance.icon

    balance.user.account.balance = 9945
    assert 99.45 == balance.native_value

    assert balance.native_unit_of_measurement == "GBP"


@pytest.mark.asyncio
async def test_charge_mode_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    override: PodPointChargeOverrideEntity
    override = sensors[9]

    assert "pod_point_12234_PSL-123456_override_end_time" == override.unique_id

    assert "Charge Override End Time" == override.name

    assert SensorDeviceClass.TIMESTAMP == override.device_class
    assert override.native_value is None
    assert override.extra_state_attributes == {"charge_override": None}
    assert "mdi:battery-clock" == override.icon

    ends_at = datetime.now().astimezone() + timedelta(hours=3)

    charge_override = ChargeOverride(
        {
            "ppid": "PSL-123456",
            "requested_at": datetime.now().astimezone().isoformat(),
            "received_at": datetime.now().astimezone().isoformat(),
            "ends_at": ends_at.isoformat(),
        }
    )
    override.pod.charge_override = charge_override
    assert override.native_value == ends_at


@pytest.mark.asyncio
async def test_charge_mode_sensor_value(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    mode = sensors[8]

    assert "pod_point_12234_PSL-123456_charge_mode" == mode.unique_id

    assert "Charge Mode" == mode.name

    assert "enum" == mode.device_class
    assert "Smart" == mode.native_value
    assert mode.extra_state_attributes == {"charge_override": None}
    assert "mdi:car-clock" == mode.icon


@pytest.mark.asyncio
async def test_charge_override_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    signal_strength = sensors[4]

    assert "pod_point_12234_PSL-123456_signal_strength" == signal_strength.unique_id

    assert "Signal Strength" == signal_strength.name

    assert SensorDeviceClass.SIGNAL_STRENGTH == signal_strength.device_class
    assert 0 == signal_strength.native_value
    assert signal_strength.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "connection_quality": 0,
        "integration": "pod_point",
        "signal_strength": 0,
    }
    assert "mdi:wifi-strength-1" == signal_strength.icon


@pytest.mark.asyncio
async def test_home_app_vehicle_sensors(hass, bypass_get_data):
    """Tests for Home App vehicle sensors."""
    (_, sensors) = await setup_sensors(hass)

    first_vehicle: PodPointHomeAppVehicleSensor = sensors[10]
    second_vehicle: PodPointHomeAppVehicleSensor = sensors[11]

    assert (
        "pod_point_12234_PSL-123456_vehicle_assignment-1_battery"
        == first_vehicle.unique_id
    )
    assert "Volkswagen ID.4 1st Battery" == first_vehicle.name
    assert SensorDeviceClass.BATTERY == first_vehicle.device_class
    assert 72 == first_vehicle.native_value
    assert "%" == first_vehicle.native_unit_of_measurement
    assert "mdi:car-electric" == first_vehicle.icon
    assert first_vehicle.extra_state_attributes["brand"] == "Volkswagen"
    assert first_vehicle.extra_state_attributes["model"] == "ID.4"
    assert first_vehicle.extra_state_attributes["is_primary"] is True
    assert first_vehicle.extra_state_attributes["range"] == 291
    assert first_vehicle.extra_state_attributes["delegated_controls"] == {
        "enabled": True
    }
    assert first_vehicle.extra_state_attributes["charge_overrides"] == {
        "active": False
    }
    assert first_vehicle.extra_state_attributes["tariffs"] == {"currency": "GBP"}
    assert first_vehicle.extra_state_attributes["remote_lock"] == {"locked": False}
    assert first_vehicle.extra_state_attributes["preferences"] == {
        "smartCharging": True
    }

    assert (
        "pod_point_12234_PSL-123456_vehicle_assignment-2_battery"
        == second_vehicle.unique_id
    )
    assert "School Run Battery" == second_vehicle.name
    assert 41 == second_vehicle.native_value
    assert second_vehicle.extra_state_attributes["is_plugged_in"] is True
    assert second_vehicle.extra_state_attributes["is_charging"] is True


@pytest.mark.asyncio
async def test_reward_wallet_sensors(hass, bypass_get_data):
    """Tests for Home App reward wallet sensors."""
    (_, sensors) = await setup_sensors(hass)

    reward_balance: PodPointRewardWalletEntity = sensors[13]
    reward_year_to_date: PodPointRewardWalletEntity = sensors[14]
    reward_start_to_date: PodPointRewardWalletEntity = sensors[15]
    annual_allowance: PodPointRewardWalletEntity = sensors[16]
    allowance_balance: PodPointRewardWalletEntity = sensors[17]

    assert (
        "1a756c9b-dfac-4c2a-ba13-9cdcc2399366_reward_balance"
        == reward_balance.unique_id
    )
    assert "Reward Balance" == reward_balance.name
    assert 1.9 == reward_balance.native_value
    assert "GBP" == reward_balance.native_unit_of_measurement
    assert reward_balance.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "integration": "pod_point",
        "balanceMiles": 82.6,
        "balancePoints": 190,
    }
    assert reward_balance.available is True

    assert (
        "1a756c9b-dfac-4c2a-ba13-9cdcc2399366_reward_year_to_date"
        == reward_year_to_date.unique_id
    )
    assert "Reward Year To Date" == reward_year_to_date.name
    assert 1.9 == reward_year_to_date.native_value
    assert reward_year_to_date.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "integration": "pod_point",
        "yearToDateMiles": 82.59999999999945,
        "yearToDatePoints": 190,
    }

    assert (
        "1a756c9b-dfac-4c2a-ba13-9cdcc2399366_reward_start_to_date"
        == reward_start_to_date.unique_id
    )
    assert "Reward Start To Date" == reward_start_to_date.name
    assert 1.9 == reward_start_to_date.native_value
    assert reward_start_to_date.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "integration": "pod_point",
        "startToDateMiles": 82.6,
        "startToDatePoints": 190,
    }

    assert (
        "1a756c9b-dfac-4c2a-ba13-9cdcc2399366_annual_allowance"
        == annual_allowance.unique_id
    )
    assert "Annual Allowance" == annual_allowance.name
    assert 103.5 == annual_allowance.native_value
    assert annual_allowance.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "integration": "pod_point",
        "annualAllowanceMiles": 4500,
        "annualAllowancePoints": None,
    }

    assert (
        "1a756c9b-dfac-4c2a-ba13-9cdcc2399366_allowance_balance"
        == allowance_balance.unique_id
    )
    assert "Allowance Balance" == allowance_balance.name
    assert 101.61 == allowance_balance.native_value
    assert allowance_balance.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "integration": "pod_point",
        "balanceMiles": 4417.4,
        "balancePoints": None,
    }


@pytest.mark.asyncio
async def test_last_message_sensor(hass, bypass_get_data):
    """Tests for pod total charge time sensor."""
    (_, sensors) = await setup_sensors(hass)

    last_message = sensors[5]

    assert "pod_point_12234_PSL-123456_last_message_at" == last_message.unique_id

    assert "Last Message Received" == last_message.name

    assert SensorDeviceClass.TIMESTAMP == last_message.device_class
    assert None == last_message.native_value
    assert last_message.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "integration": "pod_point",
        "last_message_received": None,
    }
    assert "mdi:message-text-clock" == last_message.icon
