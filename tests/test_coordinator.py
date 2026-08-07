"""Test pod_point setup process."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from podpointclient.client import PodPointClient
from podpointclient.errors import ApiConnectionError, AuthError, SessionError
from podpointclient.factories import FirmwareFactory, PodFactory, UserFactory
from podpointclient.pod import Pod
from podpointclient.user import User
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import DOMAIN
from custom_components.pod_point.coordinator import (
    PodPointDataUpdateCoordinator,
    UpdateFailed,
)

from .const import MOCK_CONFIG
from .fixtures import (
    FIRMWARE_COMPLETE_FIXTURE,
    POD_COMPLETE_FIXTURE,
    USER_COMPLETE_FIXTURE,
)


async def subject(hass) -> PodPointDataUpdateCoordinator:
    """Rerturn a setup coordinator"""
    session = async_get_clientsession(hass)
    client = PodPointClient(
        username="test@example.com", password="password", session=session
    )

    # Setup our data coordinator with the desired scan interval
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG)
    return PodPointDataUpdateCoordinator(
        hass,
        config_entry=config_entry,
        client=client,
        # These unit-test coordinators are not owned by a loaded config entry.
        # Disable polling so entity listeners do not schedule refresh timers.
        scan_interval=None,
    )


async def subject_with_data(hass) -> PodPointDataUpdateCoordinator:
    """Return a setup coodrinator with pods"""
    pod_factory = PodFactory()
    pods = pod_factory.build_pods({"pods": [POD_COMPLETE_FIXTURE]})
    firmware_factory = FirmwareFactory()
    pods[0].firmware = firmware_factory.build_firmwares(FIRMWARE_COMPLETE_FIXTURE)[0]
    user_factory = UserFactory()
    user = user_factory.build_user(USER_COMPLETE_FIXTURE)

    coordinator: PodPointDataUpdateCoordinator = await subject(hass)
    coordinator.pods = pods
    coordinator.data = pods
    coordinator.user = user
    coordinator.online = True
    return coordinator


async def subject_with_data_offline(hass) -> PodPointDataUpdateCoordinator:
    """Return an offline marked coordinator"""
    coordinator: PodPointDataUpdateCoordinator = await subject_with_data(hass)
    coordinator.online = False

    return coordinator


# Test that refreshes work as expected and populate pods
@pytest.mark.asyncio
async def test_coordinator_refresh(hass, bypass_get_data):
    """Test entry setup and unload."""
    coordinator: PodPointDataUpdateCoordinator = await subject(hass)
    assert coordinator.online is None

    coordinator.online = False

    await coordinator.async_refresh()

    assert len(coordinator.data) == 1
    assert coordinator.online is True

    pod = coordinator.data[0]
    assert isinstance(pod, Pod)
    assert pod.charging_state == "suspended-evse"
    assert coordinator.connectivity_v2[pod.ppid].connection_state == "Online"
    assert coordinator.delegated_controls[pod.ppid].status == "INACTIVE"
    assert coordinator.charge_overrides[pod.ppid] == []
    assert coordinator.manual_schedules[pod.ppid] == []
    assert len(pod.charges) == 9
    assert pod.last_charge_cost == 116
    assert isinstance(coordinator.user, User) is True


# Test refreshes with connection errors fail as expected
@pytest.mark.asyncio
async def test_coordinator_refresh_connection_error(hass, error_on_get_data):
    """Test entry setup and unload."""
    # coordinator: PodPointDataUpdateCoordinator = await subject_with_data(hass)

    session = async_get_clientsession(hass)
    client = PodPointClient(
        username="test@example.com", password="password", session=session
    )

    client.async_get_all_pods = MagicMock(
        side_effect=ApiConnectionError("CONNECTION_ERROR_MESSAGE")
    )

    # Setup our data coordinator with the desired scan interval
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG)
    coordinator = PodPointDataUpdateCoordinator(
        hass,
        config_entry=config_entry,
        client=client,
        scan_interval=timedelta(seconds=300),
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.online is False


# Test refreshes with auth an session errrors fail as expected
@pytest.mark.asyncio
async def test_coordinator_refresh_auth_session_error(hass, error_on_get_data):
    """Test entry setup and unload."""
    # coordinator: PodPointDataUpdateCoordinator = await subject_with_data(hass)

    session = async_get_clientsession(hass)
    client = PodPointClient(
        username="test@example.com", password="password", session=session
    )

    client.async_get_all_pods = MagicMock(
        side_effect=AuthError(401, "AUTH_ERROR_MESSAGE")
    )

    # Setup our data coordinator with the desired scan interval
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG)
    coordinator = PodPointDataUpdateCoordinator(
        hass,
        config_entry=config_entry,
        client=client,
        scan_interval=timedelta(seconds=300),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    client.async_get_all_pods = MagicMock(
        side_effect=SessionError(401, "AUTH_ERROR_MESSAGE")
    )

    # Setup our data coordinator with the desired scan interval
    coordinator = PodPointDataUpdateCoordinator(
        hass,
        config_entry=config_entry,
        client=client,
        scan_interval=timedelta(seconds=300),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


# Test refreshes with an exception fail as expected
@pytest.mark.asyncio
async def test_coordinator_refresh_unexpected_exception(hass, error_on_get_data):
    """Test entry setup and unload."""
    # coordinator: PodPointDataUpdateCoordinator = await subject_with_data(hass)

    session = async_get_clientsession(hass)
    client = PodPointClient(
        username="test@example.com", password="password", session=session
    )

    client.async_get_all_pods = MagicMock(
        side_effect=KeyError("CONNECTION_ERROR_MESSAGE")
    )

    # Setup our data coordinator with the desired scan interval
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG)
    coordinator = PodPointDataUpdateCoordinator(
        hass,
        config_entry=config_entry,
        client=client,
        scan_interval=timedelta(seconds=300),
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_firmware_repairs_are_scoped_per_pod(hass):
    """Firmware repair issues for one charger must not replace another's."""
    coordinator = await subject_with_data(hass)
    pod = coordinator.pods[0]
    firmware = pod.firmware
    firmware.update_status.is_update_available = True

    with patch(
        "custom_components.pod_point.coordinator.ir.async_create_issue"
    ) as create_issue:
        coordinator._PodPointDataUpdateCoordinator__process_repair_notification(
            hass, firmware, pod
        )

    assert create_issue.call_args.args[2] == f"firmware_update_{pod.ppid}"

    firmware.update_status.is_update_available = False
    with patch(
        "custom_components.pod_point.coordinator.ir.async_delete_issue"
    ) as delete_issue:
        coordinator._PodPointDataUpdateCoordinator__process_repair_notification(
            hass, firmware, pod
        )

    assert delete_issue.call_args.args[2] == f"firmware_update_{pod.ppid}"
