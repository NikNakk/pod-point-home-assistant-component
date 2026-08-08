"""Test pod_point switch."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from podpointclient.charge_mode import ChargeMode
from podpointclient.pod import Pod
from podpointclient.schedule import Schedule, ScheduleStatus
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import (
    ATTR_STATE_AVAILABLE,
    ATTR_STATE_OUT_OF_SERVICE,
    ATTR_STATE_UNAVAILABLE,
    DOMAIN,
)
from custom_components.pod_point.entity import PodPointEntity

from .const import MOCK_CONFIG
from .test_coordinator import subject_with_data as coordinator_with_data


async def setup_entity(hass) -> Pod:
    """Setup sensors within the test environment"""
    coordinator = await coordinator_with_data(hass)

    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    return PodPointEntity(coordinator, config_entry, 0)


@pytest.mark.asyncio
async def test_pod_point_entity(hass, bypass_get_data):
    """Test attributes of a PodPointEntity"""
    entity: PodPointEntity = await setup_entity(hass)

    assert 0 == entity.pod_id

    assert type(entity.pod) is Pod
    assert "pod_point_PSL-123456" == entity.unique_id
    assert True is entity.available

    entity.coordinator.online = False
    assert False is entity.available

    entity.coordinator.online = True

    original_pod = entity.pod
    other_pod = deepcopy(original_pod)
    other_pod.id = 99999
    other_pod.ppid = "PSL-OTHER"
    entity.coordinator.data = [other_pod, original_pod]
    assert entity.pod is original_pod

    entity.coordinator.data = [other_pod]
    assert entity.available is False
    entity.coordinator.data = [original_pod]

    assert {
        "identifiers": {("pod_point", "PSL-123456")},
        "manufacturer": "Pod Point",
        "model": "S7-UC-03-ACA",
        "name": "PSL-123456",
        "sw_version": "A30P-3.1.22-00001",
    } == entity.device_info

    assert {
        "address_id": 1234,
        "attribution": "Data provided by https://pod-point.com/",
        "charge_schedules": [
            {
                "end_day": 2,
                "end_time": "00:00:00",
                "start_day": 1,
                "start_time": "06:00:00",
                "status": {"is_active": False},
                "uid": "24b342c8-8cbb-11ec-1111-0a9268fc07a0",
            },
            {
                "end_day": 2,
                "end_time": "07:00:00",
                "start_day": 2,
                "start_time": "02:00:00",
                "status": {"is_active": False},
                "uid": "24b8aeac-8cbb-11ec-2222-0a9268fc07a0",
            },
            {
                "end_day": 3,
                "end_time": "07:00:00",
                "start_day": 3,
                "start_time": "02:00:00",
                "status": {"is_active": False},
                "uid": "24bb6606-8cbb-11ec-3333-0a9268fc07a0",
            },
            {
                "end_day": 4,
                "end_time": "07:00:00",
                "start_day": 4,
                "start_time": "02:00:00",
                "status": {"is_active": False},
                "uid": "24be0168-8cbb-11ec-4444-0a9268fc07a0",
            },
            {
                "end_day": 5,
                "end_time": "07:00:00",
                "start_day": 5,
                "start_time": "02:00:00",
                "status": {"is_active": False},
                "uid": "24c089b0-8cbb-11ec-5555-0a9268fc07a0",
            },
            {
                "end_day": 6,
                "end_time": "07:00:00",
                "start_day": 6,
                "start_time": "02:00:00",
                "status": {"is_active": False},
                "uid": "24c346aa-8cbb-11ec-6666-0a9268fc07a0",
            },
            {
                "end_day": 7,
                "end_time": "07:00:00",
                "start_day": 7,
                "start_time": "02:00:00",
                "status": {"is_active": False},
                "uid": "24c6bf10-8cbb-11ec-7777-0a9268fc07a0",
            },
        ],
        "commissioned_at": "2022-01-25T09:00:00+00:00",
        "contactless_enabled": False,
        "created_at": "2022-02-13T10:39:05+00:00",
        "current_kwh": 0.0,
        "description": "",
        "evZone": False,
        "home": True,
        "id": 12234,
        "integration": "pod_point",
        "last_contact_at": "2022-02-15T11:18:56+00:00",
        "model": {
            "id": 123,
            "image_url": None,
            "name": "S7-UC-03-ACA",
            "supports_contactless": False,
            "supports_ocpp": False,
            "supports_payg": False,
            "vendor": "Pod Point",
        },
        "name": None,
        "payg": False,
        "ppid": "PSL-123456",
        "price": None,
        "public": False,
        "state": "charging",
        "statuses": [
            {
                "door": "A",
                "door_id": 1,
                "id": 2,
                "key_name": "charging",
                "label": "Charging",
                "name": "Charging",
            }
        ],
        "suggested_area": "Outside",
        "timezone": "UTC",
        "total_kwh": 0.0,
        "total_charge_seconds": 0,
        "unit_connectors": [
            {
                "connector": {
                    "charge_method": "Single Phase AC",
                    "current": 32,
                    "door": "A",
                    "door_id": 1,
                    "has_cable": False,
                    "id": 123,
                    "power": 7,
                    "socket": {
                        "description": "Type 2 socket",
                        "ocpp_code": 3,
                        "ocpp_name": "sType2",
                        "type": "IEC 62196-2 Type 2",
                    },
                    "voltage": 230,
                }
            }
        ],
        "unit_id": 123456,
        "total_cost": 0,
        "firmware": {
            "serial_number": "123456789",
            "update_status": {"is_update_available": False},
            "version_info": {"manifest_id": "A30P-3.1.22-00001"},
        },
        "charge_mode": ChargeMode.SMART,
        "charge_override": None,
        "charging_state": "Unknown",
        "connectivity_status": None,
        "last_message_at": None,
        "offering_energy": False,
    } == entity.extra_state_attributes

    assert True is entity.charging_allowed

    # With no schedules
    schedules = entity.pod.charge_schedules
    entity.pod.charge_schedules = []
    assert True is entity.charging_allowed

    # With no schedule for the current day
    entity.pod.charge_schedules = [
        Schedule(9, "00:00:00", 9, "00:00:01", ScheduleStatus(is_active=True))
    ]
    assert False is entity.charging_allowed

    # With is_active as None
    entity.pod.charge_schedules = [
        Schedule(1, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        Schedule(2, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        Schedule(3, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        Schedule(4, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        Schedule(5, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        Schedule(6, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        Schedule(7, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
    ]
    assert False is entity.charging_allowed

    # With is_active as False
    entity.pod.charge_schedules = [
        Schedule(1, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        Schedule(2, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        Schedule(3, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        Schedule(4, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        Schedule(5, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        Schedule(6, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        Schedule(7, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
    ]
    assert True is entity.charging_allowed

    # With is_active as True, and within the charge time
    entity.pod.charge_schedules = [
        Schedule(1, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        Schedule(2, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        Schedule(3, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        Schedule(4, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        Schedule(5, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        Schedule(6, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        Schedule(7, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
    ]
    assert True is entity.charging_allowed

    # With is_active as True, and outside the charge time
    entity.pod.charge_schedules = [
        Schedule(1, "00:00:00", 1, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(2, "00:00:00", 2, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(3, "00:00:00", 3, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(4, "00:00:00", 4, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(5, "00:00:00", 5, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(6, "00:00:00", 6, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(7, "00:00:00", 7, "00:00:00", ScheduleStatus(is_active=True)),
    ]
    assert False is entity.charging_allowed

    # With end_day wrapping round
    entity.pod.charge_schedules = [
        Schedule(1, "00:00:00", 0, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(2, "00:00:00", 1, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(3, "00:00:00", 2, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(4, "00:00:00", 3, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(5, "00:00:00", 4, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(6, "00:00:00", 5, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(7, "00:00:00", 6, "00:00:00", ScheduleStatus(is_active=True)),
    ]
    assert True is entity.charging_allowed

    # With end_day rolling forward
    entity.pod.charge_schedules = [
        Schedule(1, "00:00:00", 2, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(2, "00:00:00", 3, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(3, "00:00:00", 4, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(4, "00:00:00", 5, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(5, "00:00:00", 6, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(6, "00:00:00", 7, "00:00:00", ScheduleStatus(is_active=True)),
        Schedule(7, "00:00:00", 8, "00:00:00", ScheduleStatus(is_active=True)),
    ]
    assert True is entity.charging_allowed

    # Reset schedules
    entity.pod.charge_schedules = schedules

    assert 123456 == entity.unit_id
    assert "PSL-123456" == entity.psl
    assert "S7-UC-03-ACA" == entity.model
    assert "/api/pod_point/static/uc-03.png" == entity.image
    assert True is entity.connected

    entity.pod.model.name = "S7-UC-05-ACA"
    assert "S7-UC-05-ACA" == entity.model
    assert "/api/pod_point/static/uc-05.png" == entity.image

    entity.pod.model.name = "S7-1C-05-ACA"
    assert "S7-1C-05-ACA" == entity.model
    assert "/api/pod_point/static/uc-05.png" == entity.image

    entity.pod.model.name = "XX-UP-XX-XX"
    assert "/api/pod_point/static/uc.png" == entity.image

    entity.pod.model.name = None
    assert entity.image is None

    # Test states for ev and evse suspended
    assert "charging" == entity.state
    entity.pod.charging_state = "suspended-ev"
    entity._PodPointEntity__update_attrs()
    assert entity.state == "suspended-ev"

    entity.pod.charging_state = "suspended-evse"
    entity._PodPointEntity__update_attrs()
    assert entity.state == "suspended-evse"

    entity.pod.statuses[0].key_name = "available"
    entity.pod.charging_state = "suspended-evse"
    entity._PodPointEntity__update_attrs()
    assert entity.state == "available"

    entity.pod.statuses[0].key_name = "out-of-service"
    entity.pod.charging_state = "suspended-evse"
    entity._PodPointEntity__update_attrs()
    assert entity.state == "out-of-service"

    # Test pending status
    entity.coordinator.last_message_at = datetime.now(tz=UTC)
    entity.pod.last_message_at = datetime.now(tz=UTC) - timedelta(minutes=5)
    entity._PodPointEntity__update_attrs()
    assert entity.state == "pending"


@pytest.mark.asyncio
async def test_compare_state(hass, bypass_get_data):
    """Test compare_state of a PodPointEntity object"""
    entity: PodPointEntity = await setup_entity(hass)

    # When both states are None
    assert entity.compare_state(None, None) is None

    # When a state only is passed
    assert "foo" == entity.compare_state("foo", None)

    # When a state is unknown, it wins
    assert "foo" == entity.compare_state("foo", ATTR_STATE_OUT_OF_SERVICE)
    assert "foo" == entity.compare_state(ATTR_STATE_OUT_OF_SERVICE, "foo")

    # Test winning example
    assert ATTR_STATE_UNAVAILABLE == entity.compare_state(
        ATTR_STATE_UNAVAILABLE, ATTR_STATE_AVAILABLE
    )
    assert ATTR_STATE_UNAVAILABLE == entity.compare_state(
        ATTR_STATE_AVAILABLE, ATTR_STATE_UNAVAILABLE
    )
    assert ATTR_STATE_OUT_OF_SERVICE == entity.compare_state(
        ATTR_STATE_OUT_OF_SERVICE, ATTR_STATE_AVAILABLE
    )
