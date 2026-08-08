"""Test pod_point switch."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from podpointclient.charge_mode import ChargeMode
from podpointclient.domain import (
    ChargerRef,
    ChargerSchedule,
    ChargerState,
    NormalizedStateValue,
    StateValue,
)
from podpointclient.schedule import ScheduleStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.entity import PodPointEntity

from .const import MOCK_CONFIG
from .test_coordinator import subject_with_data as coordinator_with_data


def schedule(
    start_day: int,
    start_time: str,
    end_day: int,
    end_time: str,
    status: ScheduleStatus,
) -> ChargerSchedule:
    """Build a canonical schedule with the legacy test call shape."""
    return ChargerSchedule(
        start_day=start_day,
        start_time=start_time,
        end_day=end_day,
        end_time=end_time,
        is_active=status.is_active,
    )


async def setup_entity(hass) -> PodPointEntity:
    """Setup sensors within the test environment"""
    coordinator = await coordinator_with_data(hass)

    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    return PodPointEntity(coordinator, config_entry, 0)


@pytest.mark.asyncio
async def test_pod_point_entity(hass, bypass_get_data):
    """Test attributes of a PodPointEntity"""
    entity: PodPointEntity = await setup_entity(hass)

    assert isinstance(entity.charger, ChargerRef)
    assert "pod_point_PSL-123456" == entity.unique_id
    assert True is entity.available

    entity.coordinator.online = False
    assert False is entity.available

    entity.coordinator.online = True

    original_charger = entity.charger
    other_charger = deepcopy(original_charger)
    object.__setattr__(other_charger, "ppid", "PSL-OTHER")
    entity.coordinator.data = [other_charger, original_charger]
    assert entity.charger is original_charger

    entity.coordinator.data = [other_charger]
    assert entity.available is False
    entity.coordinator.data = [original_charger]

    assert {
        "identifiers": {("pod_point", "PSL-123456")},
        "manufacturer": "Pod Point",
        "model": "S7-UC-03-ACA",
        "name": "PSL-123456",
        "sw_version": "A30P-3.1.22-00001",
    } == entity.device_info

    assert entity.extra_state_attributes == {
        "attribution": "Data provided by https://pod-point.com/",
        "charge_mode": ChargeMode.SMART,
        "current_kwh": 0.0,
        "id": 123456,
        "integration": "pod_point",
        "model": "S7-UC-03-ACA",
        "ppid": "PSL-123456",
        "state": "charging",
        "suggested_area": "Outside",
        "timezone": "UTC",
        "total_charge_seconds": 0,
        "total_kwh": 0.0,
        "unit_id": 123456,
    }

    assert True is entity.charging_allowed

    # With no schedules
    schedules = entity.coordinator.schedules[entity.charger.ppid]
    entity.coordinator.schedules[entity.charger.ppid] = []
    assert True is entity.charging_allowed

    # With no schedule for the current day
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(9, "00:00:00", 9, "00:00:01", ScheduleStatus(is_active=True))
    ]
    assert False is entity.charging_allowed

    # With is_active as None
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(1, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        schedule(2, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        schedule(3, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        schedule(4, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        schedule(5, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        schedule(6, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
        schedule(7, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=None)),
    ]
    assert False is entity.charging_allowed

    # With is_active as False
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(1, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        schedule(2, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        schedule(3, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        schedule(4, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        schedule(5, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        schedule(6, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
        schedule(7, "00:00:00", 1, "00:00:01", ScheduleStatus(is_active=False)),
    ]
    assert True is entity.charging_allowed

    # With is_active as True, and within the charge time
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(1, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        schedule(2, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        schedule(3, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        schedule(4, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        schedule(5, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        schedule(6, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
        schedule(7, "00:00:00", 1, "23:59:59", ScheduleStatus(is_active=True)),
    ]
    assert True is entity.charging_allowed

    # With is_active as True, and outside the charge time
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(1, "00:00:00", 1, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(2, "00:00:00", 2, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(3, "00:00:00", 3, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(4, "00:00:00", 4, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(5, "00:00:00", 5, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(6, "00:00:00", 6, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(7, "00:00:00", 7, "00:00:00", ScheduleStatus(is_active=True)),
    ]
    assert False is entity.charging_allowed

    # With end_day wrapping round
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(1, "00:00:00", 0, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(2, "00:00:00", 1, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(3, "00:00:00", 2, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(4, "00:00:00", 3, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(5, "00:00:00", 4, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(6, "00:00:00", 5, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(7, "00:00:00", 6, "00:00:00", ScheduleStatus(is_active=True)),
    ]
    assert True is entity.charging_allowed

    # With end_day rolling forward
    entity.coordinator.schedules[entity.charger.ppid] = [
        schedule(1, "00:00:00", 2, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(2, "00:00:00", 3, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(3, "00:00:00", 4, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(4, "00:00:00", 5, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(5, "00:00:00", 6, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(6, "00:00:00", 7, "00:00:00", ScheduleStatus(is_active=True)),
        schedule(7, "00:00:00", 8, "00:00:00", ScheduleStatus(is_active=True)),
    ]
    assert True is entity.charging_allowed

    # Reset schedules
    entity.coordinator.schedules[entity.charger.ppid] = schedules

    assert 123456 == entity.unit_id
    assert "S7-UC-03-ACA" == entity.model
    assert "/api/pod_point/static/uc-03.png" == entity.image
    assert True is entity.connected

    object.__setattr__(entity.charger, "model_name", "S7-UC-05-ACA")
    assert "S7-UC-05-ACA" == entity.model
    assert "/api/pod_point/static/uc-05.png" == entity.image

    object.__setattr__(entity.charger, "model_name", "S7-1C-05-ACA")
    assert "S7-1C-05-ACA" == entity.model
    assert "/api/pod_point/static/uc-05.png" == entity.image

    object.__setattr__(entity.charger, "model_name", "XX-UP-XX-XX")
    assert "/api/pod_point/static/uc.png" == entity.image

    object.__setattr__(entity.charger, "model_name", None)
    assert entity.image is None

    # Test states for ev and evse suspended
    assert "charging" == entity.state
    entity.coordinator.charger_states[entity.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE),
        charging=NormalizedStateValue(StateValue.SUSPENDED_EV),
    )
    entity._PodPointEntity__update_attrs()
    assert entity.state == "suspended-ev"

    entity.coordinator.charger_states[entity.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE),
        charging=NormalizedStateValue(StateValue.SUSPENDED_EVSE),
    )
    entity._PodPointEntity__update_attrs()
    assert entity.state == "suspended-evse"

    entity.coordinator.charger_states[entity.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE),
        charging=NormalizedStateValue(StateValue.AVAILABLE),
    )
    entity._PodPointEntity__update_attrs()
    assert entity.state == "available"

    entity.coordinator.charger_states[entity.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE),
        charging=NormalizedStateValue(StateValue.OUT_OF_SERVICE),
    )
    entity._PodPointEntity__update_attrs()
    assert entity.state == "out-of-service"

    # Test pending status
    entity.coordinator.pending_request_at[entity.charger.ppid] = datetime.now(tz=UTC)
    entity.coordinator.charger_states[entity.charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE),
        charging=NormalizedStateValue(StateValue.OUT_OF_SERVICE),
        last_seen_at=datetime.now(tz=UTC) - timedelta(minutes=5),
    )
    entity._PodPointEntity__update_attrs()
    assert entity.state == "pending"
