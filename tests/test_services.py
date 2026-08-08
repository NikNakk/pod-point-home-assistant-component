"""Test pod_point services."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from podpointclient.domain import ChargerSchedule
from podpointclient.errors import RequestValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import (
    DOMAIN,
    SERVICE_CHARGE_NOW,
    SERVICE_SET_SCHEDULE,
)
from custom_components.pod_point.services import PodPointServiceException

from .const import MOCK_CONFIG


def schedule_payload() -> list[dict]:
    """Return one valid complete schedule service payload."""
    return [
        {
            "start_day": day,
            "start_time": "00:30:00",
            "end_day": day,
            "end_time": "04:30:00",
            "is_active": day < 6,
        }
        for day in range(1, 8)
    ]


def canonical_schedule(day: int, *, uid: str) -> ChargerSchedule:
    """Return one canonical schedule as supplied by podpointclient."""
    return ChargerSchedule(
        start_day=day,
        start_time="01:00:00",
        end_day=day,
        end_time="02:00:00",
        is_active=True,
        uid=uid,
    )


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_charge_now_service_with_data(hass, bypass_get_data):
    """Test charge_mode service"""
    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Functions/objects can be patched directly in test code as well and can be used to test
    # additional things, like whether a function was called or what arguments it was called with
    with patch("podpointclient.client.PodPointClient.async_start_boost") as title_func:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHARGE_NOW,
            {
                "config_entry_id": "test",
                "hours": 3,
                "minutes": 2,
                "seconds": 1,
            },
            blocking=True,
        )
        assert title_func.called

        charger = title_func.call_args.args[0]
        hours = title_func.call_args.kwargs["hours"]
        minutes = title_func.call_args.kwargs["minutes"]
        seconds = title_func.call_args.kwargs["seconds"]
        assert "PSL-123456" == charger.ppid
        assert 3 == hours
        assert 2 == minutes
        assert 1 == seconds

        title_func.reset_mock()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHARGE_NOW,
            {
                "config_entry_id": "test",
                "seconds": 1,
            },
            blocking=True,
        )
        assert title_func.called

        charger = title_func.call_args.args[0]
        hours = title_func.call_args.kwargs["hours"]
        minutes = title_func.call_args.kwargs["minutes"]
        seconds = title_func.call_args.kwargs["seconds"]
        assert "PSL-123456" == charger.ppid
        assert 0 == hours
        assert 0 == minutes
        assert 1 == seconds

        title_func.reset_mock()

        with pytest.raises(PodPointServiceException):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_CHARGE_NOW,
                {"config_entry_id": "test"},
                blocking=True,
            )


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_charge_now_service_targets_device(hass, bypass_get_data):
    """A device target identifies the charger without a config entry field."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device({(DOMAIN, "PSL-123456")})
    assert device is not None

    with patch(
        "podpointclient.client.PodPointClient.async_start_boost"
    ) as create_override:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHARGE_NOW,
            {"device_id": device.id, "minutes": 30},
            blocking=True,
        )

    assert create_override.call_args.args[0].ppid == "PSL-123456"
    assert create_override.call_args.kwargs["minutes"] == 30


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_legacy_service_requires_device_for_multiple_pods(hass, bypass_get_data):
    """The account-only form remains unambiguous only for one Pod."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    coordinator.data.append(coordinator.data[0])

    with pytest.raises(PodPointServiceException, match="device_id is required"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHARGE_NOW,
            {"config_entry_id": "test", "minutes": 30},
            blocking=True,
        )


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_set_schedule_constructs_canonical_week(hass, bypass_get_data):
    """The service preserves fetched UIDs and constructs canonical entries."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    current = [canonical_schedule(day, uid=f"uid-{day}") for day in range(1, 8)]
    saved = [canonical_schedule(day, uid=f"new-{day}") for day in range(1, 8)]
    coordinator.api.async_get_charger_schedules = AsyncMock(return_value=current)
    coordinator.api.async_replace_charger_schedules = AsyncMock(return_value=saved)
    coordinator.async_request_refresh = AsyncMock()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        {"config_entry_id": "test", "schedules": schedule_payload()},
        blocking=True,
    )

    schedules = coordinator.api.async_replace_charger_schedules.call_args.args[1]
    assert [schedule.start_day for schedule in schedules] == list(range(1, 8))
    assert [schedule.uid for schedule in schedules] == [
        f"uid-{day}" for day in range(1, 8)
    ]
    assert schedules[0].start_time == "00:30:00"
    assert schedules[0].end_time == "04:30:00"
    assert schedules[0].is_active is True
    assert schedules[5].is_active is False
    assert coordinator.schedules[coordinator.data[0].ppid] is saved
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_set_schedule_rejects_active_smart_mode(hass, bypass_get_data):
    """Do not attempt a schedule write while cached smart mode is active."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    ppid = coordinator.data[0].ppid
    coordinator.smart_charging_states[ppid] = SimpleNamespace(status="ACTIVE")
    coordinator.api.async_get_charger_schedules = AsyncMock()

    with pytest.raises(PodPointServiceException, match="smart charging is active"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {"config_entry_id": "test", "schedules": schedule_payload()},
            blocking=True,
        )

    coordinator.api.async_get_charger_schedules.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_set_schedule_surfaces_library_validation(hass, bypass_get_data):
    """Cross-entry schedule constraints remain owned by podpointclient."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    current = [canonical_schedule(day, uid=f"uid-{day}") for day in range(1, 8)]
    coordinator.api.async_get_charger_schedules = AsyncMock(return_value=current)
    coordinator.api.async_replace_charger_schedules = AsyncMock(
        side_effect=RequestValidationError("schedule periods overlap")
    )

    with pytest.raises(PodPointServiceException, match="schedule periods overlap"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {"config_entry_id": "test", "schedules": schedule_payload()},
            blocking=True,
        )
