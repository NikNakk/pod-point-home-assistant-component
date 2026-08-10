"""Test pod_point setup process."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from podpointclient.charge_history import ChargeHistory
from podpointclient.charger import Charger
from podpointclient.client import PodPointClient
from podpointclient.domain import (
    BasicChargingMode,
    BoostState,
    ChargerRef,
    ChargerSource,
    ChargerState,
    NormalizedStateValue,
    StateValue,
    charger_ref_from_pod,
    charger_schedule_from_legacy,
)
from podpointclient.errors import ApiConnectionError, APIError, AuthError, SessionError
from podpointclient.factories import (
    ChargeFactory,
    FirmwareFactory,
    PodFactory,
    UserFactory,
)
from podpointclient.user import User
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pod_point.const import CONF_USE_LEGACY_API, DOMAIN
from custom_components.pod_point.coordinator import (
    ChargerMetrics,
    PodPointDataUpdateCoordinator,
    UpdateFailed,
)

from .const import MOCK_CONFIG
from .fixtures import (
    CHARGES_COMPLETE_FIXTURE,
    FIRMWARE_COMPLETE_FIXTURE,
    POD_COMPLETE_FIXTURE,
    USER_COMPLETE_FIXTURE,
)


async def subject(hass, options=None, data=None) -> PodPointDataUpdateCoordinator:
    """Rerturn a setup coordinator"""
    session = async_get_clientsession(hass)
    client = PodPointClient(
        username="test@example.com", password="password", session=session
    )

    # Setup our data coordinator with the desired scan interval
    config_entry = MockConfigEntry(
        domain=DOMAIN, data=data or MOCK_CONFIG, options=options
    )
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
    charger = charger_ref_from_pod(pods[0])
    coordinator.data = [charger]
    coordinator.schedules[charger.ppid] = [
        charger_schedule_from_legacy(schedule) for schedule in pods[0].charge_schedules
    ]
    coordinator.boost_states[charger.ppid] = BoostState(
        ppid=charger.ppid, active=False, timed=False
    )
    coordinator.basic_charging_modes[charger.ppid] = BasicChargingMode.SCHEDULED
    coordinator.charger_states[charger.ppid] = ChargerState(
        connection=NormalizedStateValue(StateValue.ONLINE, "Online"),
        charging=NormalizedStateValue(StateValue.CHARGING, "Charging"),
        last_seen_at=pods[0].last_contact_at,
        signal_strength_dbm=-72,
    )
    coordinator.firmware[charger.ppid] = pods[0].firmware
    coordinator.metrics[charger.ppid] = ChargerMetrics(
        total_kwh=pods[0].total_kwh,
        total_charge_seconds=pods[0].total_charge_seconds,
        current_kwh=pods[0].current_kwh,
        total_cost=pods[0].total_cost,
        last_charge_cost=getattr(pods[0], "last_charge_cost", None),
        charge_currency=getattr(pods[0], "charge_currency", None),
    )
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
    discover = coordinator.api.async_discover_chargers
    coordinator.api.async_discover_chargers = AsyncMock(wraps=discover)
    get_basic_mode = coordinator.api.async_get_basic_charging_mode
    coordinator.api.async_get_basic_charging_mode = AsyncMock(wraps=get_basic_mode)
    assert coordinator.online is None

    coordinator.online = False

    await coordinator.async_refresh()

    assert len(coordinator.data) == 1
    assert coordinator.online is True

    charger = coordinator.data[0]
    assert isinstance(charger, ChargerRef)
    assert (
        coordinator.charger_states[charger.ppid].charging.value
        is StateValue.SUSPENDED_EVSE
    )
    assert (
        coordinator.charger_states[charger.ppid].connection.value is StateValue.ONLINE
    )
    assert coordinator.charger_states[charger.ppid].signal_strength_dbm is None
    assert coordinator.charger_states[charger.ppid].connection_quality.raw == 4
    assert (
        coordinator.charger_states[charger.ppid].connection_quality.source
        is ChargerSource.HOME
    )
    assert coordinator.smart_charging_states[charger.ppid].status == "INACTIVE"
    assert coordinator.boost_states[charger.ppid].active is False
    assert coordinator.basic_charging_modes[charger.ppid] is BasicChargingMode.SCHEDULED
    coordinator.api.async_get_basic_charging_mode.assert_awaited_once_with(
        charger, boost_state=coordinator.boost_states[charger.ppid]
    )
    assert len(coordinator.live_sessions[charger.ppid]) == 1
    assert len(coordinator.completed_sessions[charger.ppid]) == 8
    assert coordinator.metrics[charger.ppid].current_kwh == 3.2
    assert coordinator.metrics[charger.ppid].active_started_at is not None
    assert coordinator.metrics[charger.ppid].last_charge_cost == 116
    assert isinstance(coordinator.user, User) is True
    coordinator.api.async_discover_chargers.assert_awaited_once_with(ChargerSource.HOME)
    coordinator.api.async_get_user.assert_awaited_once_with(includes=["account"])
    coordinator.api.async_get_delegated_control.assert_awaited_once()
    coordinator.api.async_get_manual_schedules.assert_awaited_once()
    coordinator.api.async_get_charge_history.assert_awaited_once_with(
        charger.linked_at.date(), datetime.now(UTC).date()
    )
    coordinator.api.async_get_all_charges.assert_not_awaited()
    coordinator.api.async_get_charges.assert_awaited_once_with(perpage=3, page=1)


@pytest.mark.asyncio
async def test_discovery_uses_configured_legacy_api(hass, bypass_get_data):
    """The account-level preference selects legacy discovery."""
    coordinator = await subject(hass, options={CONF_USE_LEGACY_API: True})
    coordinator.api.async_discover_chargers = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    coordinator.api.async_discover_chargers.assert_awaited_once_with(
        ChargerSource.LEGACY
    )


@pytest.mark.asyncio
async def test_legacy_discovery_keeps_original_pod_without_synthetic_view(
    hass, bypass_get_data
):
    """Legacy fallback wraps, but never copies or synthesizes, its wire object."""
    coordinator = await subject(hass)
    legacy_pod = coordinator.api.async_get_all_pods.return_value[0]
    coordinator.api.async_get_chargers.side_effect = APIError(404, "unsupported")
    coordinator.api.async_get_charge_override = AsyncMock(
        return_value=legacy_pod.charge_override
    )

    await coordinator.async_refresh()

    [charger] = coordinator.data
    assert isinstance(charger, ChargerRef)
    assert charger.source is ChargerSource.LEGACY
    assert charger.raw is legacy_pod
    assert coordinator.charger_states[charger.ppid].signal_strength_dbm == -68
    assert coordinator.charger_states[charger.ppid].connection_quality.raw == 3
    assert (
        coordinator.charger_states[charger.ppid].connection_quality.source
        is ChargerSource.LEGACY
    )
    assert coordinator.boost_states[charger.ppid] is not None
    coordinator.api.async_get_pod.assert_not_awaited()


def reset_api_mocks(coordinator: PodPointDataUpdateCoordinator) -> None:
    """Reset the API call counters used by cadence tests."""
    for name in (
        "async_get_user",
        "async_get_all_pods",
        "async_get_all_charges",
        "async_get_charges",
        "async_get_firmware",
        "async_get_chargers",
        "async_get_connectivity_status_v2",
        "async_get_tariffs",
        "async_get_charger_charge_overrides",
        "async_get_smart_charging_preferences",
        "async_get_remote_lock",
        "async_get_delegated_vehicles",
        "async_get_reward_wallet",
        "async_get_charge_history",
    ):
        getattr(coordinator.api, name).reset_mock()


def completed_session_ids(
    coordinator: PodPointDataUpdateCoordinator, ppid: str
) -> list[str | None]:
    """Return canonical completed-session IDs for one charger."""
    return [
        session.session_id
        for session in coordinator.completed_sessions.get(ppid, {}).values()
    ]


def live_charge_ids(
    coordinator: PodPointDataUpdateCoordinator, ppid: str
) -> list[int | None]:
    """Return raw IDs retained by canonical live sessions."""
    return [session.raw.id for session in coordinator.live_sessions.get(ppid, [])]


def build_charge(
    charge_id: int,
    kwh_used: float,
    *,
    unit_id: int = 123456,
    starts_at: str = "2022-03-12T10:00:00+00:00",
    ends_at: str | None = None,
    duration: int = 0,
    charging_duration: int | None = None,
    energy_cost: int = 0,
):
    """Build one legacy charge from the standard home-charge fixture."""
    data = deepcopy(CHARGES_COMPLETE_FIXTURE["charges"][0])
    data.update(
        {
            "id": charge_id,
            "kwh_used": kwh_used,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "duration": duration,
            "charging_duration": {
                "raw": charging_duration,
                "formatted": [],
            },
            "energy_cost": energy_cost,
            "pod": {"id": unit_id},
        }
    )
    return ChargeFactory().build_charges({"charges": [data]})[0]


def build_new_history(*charges: dict) -> ChargeHistory:
    """Build an account-wide new-API history response."""
    return ChargeHistory({"data": {"count": len(charges), "charges": list(charges)}})


def new_history_charge(
    charge_id: str,
    ppid: str = "PSL-123456",
    *,
    plugged_in_at: str = "2026-08-08T11:37:28+01:00",
    started_at: str = "2026-08-08T11:37:28+01:00",
    ended_at: str | None = "2026-08-08T11:48:31+01:00",
    unplugged_at: str = "2026-08-08T12:01:50+01:00",
    duration: int = 643,
    energy_total: float = 0.6,
    cost_amount: int = 18,
) -> dict:
    """Return a realistic completed Pod Home history item."""
    return {
        "id": charge_id,
        "startedAt": started_at,
        "endedAt": ended_at,
        "duration": duration,
        "energyTotal": energy_total,
        "cost": {"amount": cost_amount, "currency": "GBP"},
        "charger": {
            "id": ppid,
            "pluggedInAt": plugged_in_at,
            "unpluggedAt": unplugged_at,
            "pluggedInDuration": 1462,
        },
    }


def set_connectivity_state(
    coordinator: PodPointDataUpdateCoordinator, state: str
) -> None:
    """Set the connectivity-v2 state returned on subsequent refreshes."""
    coordinator.api.async_get_connectivity_status_v2.return_value.charging_state = state


@pytest.mark.asyncio
async def test_ordinary_refresh_only_calls_fast_endpoints(hass, bypass_get_data):
    """An ordinary refresh retains caches and only polls fast state."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    cached_user = coordinator.user
    cached_preferences = coordinator.smart_charging_preferences.copy()
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()

    coordinator.api.async_get_all_pods.assert_not_awaited()
    coordinator.api.async_get_chargers.assert_awaited_once()
    coordinator.api.async_get_connectivity_status_v2.assert_awaited_once()
    coordinator.api.async_get_charger_charge_overrides.assert_awaited_once()
    coordinator.api.async_get_delegated_vehicles.assert_awaited_once()
    coordinator.api.async_get_charges.assert_awaited()
    coordinator.api.async_get_user.assert_not_awaited()
    coordinator.api.async_get_tariffs.assert_not_awaited()
    coordinator.api.async_get_smart_charging_preferences.assert_not_awaited()
    coordinator.api.async_get_reward_wallet.assert_not_awaited()
    coordinator.api.async_get_remote_lock.assert_not_awaited()
    coordinator.api.async_get_firmware.assert_not_awaited()
    coordinator.api.async_get_charge_history.assert_not_awaited()
    assert (
        sum(
            getattr(coordinator.api, name).await_count
            for name in (
                "async_get_all_pods",
                "async_get_chargers",
                "async_get_connectivity_status_v2",
                "async_get_charger_charge_overrides",
                "async_get_delegated_vehicles",
                "async_get_charges",
            )
        )
        == 5
    )
    assert coordinator.user is cached_user
    assert coordinator.smart_charging_preferences == cached_preferences


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["Charging", "SuspendedEV", "SuspendedEVSE"])
async def test_live_connectivity_states_poll_recent_charges(
    hass, bypass_get_data, state
):
    """Charging and both suspended states keep live energy polling active."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, state)
    await coordinator.async_refresh()
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()

    coordinator.api.async_get_charges.assert_awaited_once_with(perpage=3, page=1)


@pytest.mark.asyncio
async def test_delegated_plug_state_polls_recent_charges(hass, bypass_get_data):
    """Delegated vehicle plug state triggers polling even if connectivity is idle."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, "Available")
    ppid = coordinator.api.async_get_chargers.return_value[0].ppid
    coordinator.api.async_get_delegated_vehicles.return_value = [
        SimpleNamespace(
            ppid=ppid,
            vehicles=[SimpleNamespace(is_plugged_in_to_this_charger=True)],
        )
    ]
    await coordinator.async_refresh()
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()

    coordinator.api.async_get_charges.assert_awaited_once_with(perpage=3, page=1)


@pytest.mark.asyncio
async def test_live_to_idle_transition_gets_one_final_charge_refresh(
    hass, bypass_get_data
):
    """Unplugging causes one final refresh, followed by skipped idle cycles."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    set_connectivity_state(coordinator, "Available")
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()
    coordinator.api.async_get_charges.assert_awaited_once()
    coordinator.api.async_get_charge_history.assert_awaited_once()

    coordinator.api.async_get_charges.reset_mock()
    coordinator.api.async_get_charge_history.reset_mock()
    await coordinator.async_refresh()
    coordinator.api.async_get_charges.assert_not_awaited()
    coordinator.api.async_get_charge_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_idle_cycles_skip_legacy_charges(hass, bypass_get_data):
    """Continuously idle refreshes retain history without polling legacy charges."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, "Available")
    await coordinator.async_refresh()
    cached_ids = live_charge_ids(coordinator, "PSL-123456")
    cached_refresh = coordinator._last_charge_refresh
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    coordinator.api.async_get_charges.assert_not_awaited()
    coordinator.api.async_get_charge_history.assert_not_awaited()
    assert live_charge_ids(coordinator, "PSL-123456") == cached_ids
    assert coordinator._last_charge_refresh == cached_refresh


@pytest.mark.asyncio
async def test_hourly_idle_reconciliation_polls_recent_charges(hass, bypass_get_data):
    """Idle canonical history is reconciled hourly without legacy polling."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, "Available")
    await coordinator.async_refresh()
    reset_api_mocks(coordinator)
    coordinator._last_history_refresh -= coordinator._history_refresh_interval + 1

    await coordinator.async_refresh()

    coordinator.api.async_get_charge_history.assert_awaited_once_with(
        datetime.now(UTC).date() - timedelta(days=7), datetime.now(UTC).date()
    )
    coordinator.api.async_get_charges.assert_not_awaited()


@pytest.mark.asyncio
async def test_idle_reconciliation_merges_short_session_without_duplicates(
    hass, bypass_get_data
):
    """A session completed between polls is added exactly once."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, "Available")
    await coordinator.async_refresh()
    new_charge = new_history_charge("new-short", energy_total=1.7)
    coordinator.api.async_get_charge_history.return_value = build_new_history(
        new_charge
    )
    coordinator._last_history_refresh -= coordinator._history_refresh_interval + 1

    await coordinator.async_refresh()
    coordinator._last_history_refresh -= coordinator._history_refresh_interval + 1
    await coordinator.async_refresh()

    charge_ids = completed_session_ids(coordinator, "PSL-123456")
    assert charge_ids.count("new-short") == 1
    assert len(charge_ids) == len(set(charge_ids))


@pytest.mark.asyncio
async def test_recent_live_charge_refresh_uses_one_bounded_page(hass, bypass_get_data):
    """Live polling is bounded because completed history is cached separately."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    known_charge = coordinator.live_sessions["PSL-123456"][0].raw
    first_page = [
        build_charge(
            charge_id,
            0.5,
            ends_at=f"2022-03-12T10:{charge_id}:00+00:00",
        )
        for charge_id in (11, 12, 13)
    ]
    coordinator.api.async_get_charges.side_effect = [first_page, [known_charge]]
    coordinator.api.async_get_charges.reset_mock()

    await coordinator.async_refresh()

    coordinator.api.async_get_charges.assert_awaited_once_with(perpage=3, page=1)


@pytest.mark.asyncio
async def test_live_charge_energy_updates_without_duplication(hass, bypass_get_data):
    """An updated unfinished legacy charge replaces its cached version."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    updated_charge = build_charge(1, 4.8)
    coordinator.api.async_get_charges.return_value = [updated_charge]

    await coordinator.async_refresh()

    assert coordinator.metrics["PSL-123456"].current_kwh == 4.8
    assert live_charge_ids(coordinator, "PSL-123456").count(1) == 1


@pytest.mark.asyncio
async def test_final_charge_refresh_resets_current_energy(hass, bypass_get_data):
    """The unplug refresh replaces the active record and resets current energy."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    set_connectivity_state(coordinator, "Available")
    final_charge = build_charge(1, 5.1, ends_at="2022-03-12T11:00:00+00:00")
    coordinator.api.async_get_charges.return_value = [final_charge]

    await coordinator.async_refresh()

    assert coordinator.metrics["PSL-123456"].current_kwh == 0.0
    assert coordinator.live_sessions["PSL-123456"] == []


@pytest.mark.asyncio
async def test_hybrid_session_transitions_to_canonical_completed_values(
    hass, bypass_get_data
):
    """A legacy live record is replaced by the matched canonical new record."""
    coordinator = await subject(hass)
    active = build_charge(
        701,
        0.6,
        starts_at="2026-08-08T11:37:28+01:00",
        duration=24,
        charging_duration=643,
        energy_cost=15,
    )
    coordinator.api.async_get_charge_history.return_value = build_new_history()
    coordinator.api.async_get_charges.return_value = [active]

    await coordinator.async_refresh()

    charger = coordinator.data[0]
    metrics = coordinator.metrics[charger.ppid]
    assert metrics.current_kwh == 0.6
    assert metrics.total_kwh == 0.6
    assert metrics.total_charge_seconds == 643
    assert metrics.total_cost == 15
    assert coordinator.live_sessions[charger.ppid][0].started_at == active.starts_at
    assert coordinator.completed_sessions.get(charger.ppid, {}) == {}

    set_connectivity_state(coordinator, "Available")
    completed = new_history_charge("new-991")
    coordinator.api.async_get_charge_history.return_value = build_new_history(completed)
    coordinator.api.async_get_charges.return_value = [
        build_charge(
            701,
            0.6,
            starts_at="2026-08-08T11:37:28+01:00",
            ends_at="2026-08-08T12:01:50+01:00",
            duration=24,
            charging_duration=643,
            energy_cost=15,
        )
    ]
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()

    charger = coordinator.data[0]
    metrics = coordinator.metrics[charger.ppid]
    assert coordinator.api.async_get_charge_history.await_count == 1
    assert coordinator.api.async_get_charges.await_count == 1
    assert completed_session_ids(coordinator, charger.ppid) == ["new-991"]
    assert metrics.current_kwh == 0.0
    assert metrics.total_kwh == 0.6
    assert metrics.total_charge_seconds == 643
    assert metrics.total_cost == 18
    assert metrics.last_charge_cost == 18
    assert metrics.charge_currency == "GBP"


@pytest.mark.asyncio
async def test_new_history_deduplicates_and_ignores_unfinished_records(
    hass, bypass_get_data
):
    """Only completed records with unique new IDs enter canonical history."""
    coordinator = await subject(hass)
    completed = new_history_charge("new-1")
    duplicate = new_history_charge("new-1", energy_total=0.7)
    unfinished = new_history_charge("new-active", ended_at=None)
    coordinator.api.async_get_charge_history.return_value = build_new_history(
        completed, duplicate, unfinished
    )

    await coordinator.async_refresh()

    cached = coordinator.completed_sessions["PSL-123456"]
    assert completed_session_ids(coordinator, "PSL-123456") == ["new-1"]
    assert next(iter(cached.values())).energy_kwh == 0.7


@pytest.mark.asyncio
async def test_unsupported_new_history_falls_back_to_complete_legacy_history(
    hass, bypass_get_data
):
    """The domain API owns fallback when Home history is unsupported."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, "Available")
    coordinator.api.async_get_charge_history.side_effect = APIError(
        404, "response omitted"
    )

    await coordinator.async_refresh()

    coordinator.api.async_get_all_charges.assert_awaited_once_with()
    assert len(coordinator.completed_sessions["PSL-123456"]) == 9
    assert len(coordinator.live_sessions["PSL-123456"]) == 1
    assert coordinator.metrics["PSL-123456"].last_charge_cost == 116


@pytest.mark.asyncio
async def test_failed_new_history_refresh_preserves_completed_cache(
    hass, bypass_get_data
):
    """A transient canonical-history failure retains the last good cache."""
    coordinator = await subject(hass)
    set_connectivity_state(coordinator, "Available")
    await coordinator.async_refresh()
    cached = coordinator.completed_sessions["PSL-123456"].copy()
    coordinator._last_history_refresh -= coordinator._history_refresh_interval + 1
    coordinator.api.async_get_charge_history.side_effect = APIError(
        503, "response omitted"
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.completed_sessions["PSL-123456"] == cached


@pytest.mark.asyncio
async def test_pending_finalisation_reconciles_on_history_cadence(
    hass, bypass_get_data
):
    """An unpublished completion is retained until the next reconciliation."""
    coordinator = await subject(hass)
    active = build_charge(
        701,
        0.6,
        starts_at="2026-08-08T11:37:28+01:00",
        charging_duration=643,
    )
    coordinator.api.async_get_charge_history.return_value = build_new_history()
    coordinator.api.async_get_charges.return_value = [active]
    await coordinator.async_refresh()
    set_connectivity_state(coordinator, "Available")
    reset_api_mocks(coordinator)

    await coordinator.async_refresh()
    coordinator.api.async_get_charge_history.assert_awaited_once()
    assert "PSL-123456" in coordinator.pending_sessions

    coordinator.api.async_get_charge_history.reset_mock()
    await coordinator.async_refresh()
    coordinator.api.async_get_charge_history.assert_not_awaited()

    coordinator._last_history_refresh -= coordinator._history_refresh_interval + 1
    await coordinator.async_refresh()
    coordinator.api.async_get_charge_history.assert_awaited_once()


@pytest.mark.asyncio
async def test_multiple_chargers_keep_charge_attribution(hass, bypass_get_data):
    """Account-level history is still grouped by each legacy Pod unit ID."""
    second_pod_data = deepcopy(POD_COMPLETE_FIXTURE)
    second_pod_data.update({"id": 22334, "ppid": "PSL-654321", "unit_id": 654321})
    pods = PodFactory().build_pods(
        {"pods": [deepcopy(POD_COMPLETE_FIXTURE), second_pod_data]}
    )
    coordinator = await subject(hass)
    coordinator.api.async_get_all_pods.return_value = pods
    coordinator.api.async_get_charges.return_value = []
    coordinator.api.async_get_chargers.return_value = [
        Charger(
            {
                "ppid": pod.ppid,
                "unitId": pod.unit_id,
                "linkedAt": pod.commissioned_at.isoformat(),
                "timezone": pod.timezone,
                "delegatedControl": {"status": "INACTIVE"},
                "modelInfo": {"style": pod.model.name},
            }
        )
        for pod in pods
    ]
    coordinator.api.async_get_charge_history.return_value = build_new_history(
        new_history_charge("new-101", energy_total=1.5),
        new_history_charge("new-102", ppid="PSL-654321", energy_total=2.5),
    )
    coordinator.api.async_get_connectivity_status_v2.return_value.charging_state = (
        "Available"
    )

    await coordinator.async_refresh()

    assert completed_session_ids(coordinator, "PSL-123456") == ["new-101"]
    assert completed_session_ids(coordinator, "PSL-654321") == ["new-102"]
    assert coordinator.live_sessions["PSL-123456"] == []
    assert coordinator.live_sessions["PSL-654321"] == []
    assert coordinator.metrics["PSL-123456"].total_kwh == 1.5
    assert coordinator.metrics["PSL-654321"].total_kwh == 2.5


@pytest.mark.asyncio
async def test_failed_live_charge_refresh_preserves_cached_history(
    hass, bypass_get_data
):
    """A failed live refresh leaves the last good history and cadence untouched."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    cached_ids = live_charge_ids(coordinator, "PSL-123456")
    cached_refresh = coordinator._last_charge_refresh
    coordinator.api.async_get_charges.side_effect = ApiConnectionError("temporary")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert live_charge_ids(coordinator, "PSL-123456") == cached_ids
    assert coordinator._last_charge_refresh == cached_refresh


@pytest.mark.asyncio
async def test_due_slow_refresh_calls_hourly_and_remote_endpoints(
    hass, bypass_get_data
):
    """Hourly and remote-lock data use elapsed time rather than poll counts."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    reset_api_mocks(coordinator)
    coordinator._last_hourly_refresh -= coordinator._hourly_refresh_interval + 1
    coordinator._last_remote_lock_refresh -= (
        coordinator._remote_lock_refresh_interval + 1
    )

    await coordinator.async_refresh()

    coordinator.api.async_get_user.assert_awaited_once_with(includes=["account"])
    coordinator.api.async_get_tariffs.assert_awaited_once()
    coordinator.api.async_get_smart_charging_preferences.assert_awaited_once()
    coordinator.api.async_get_reward_wallet.assert_awaited_once()
    coordinator.api.async_get_remote_lock.assert_awaited_once()
    coordinator.api.async_get_firmware.assert_not_awaited()


@pytest.mark.asyncio
async def test_firmware_refresh_is_daily(hass, bypass_get_data):
    """Firmware refreshes after a day without coupling to scan interval."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    reset_api_mocks(coordinator)
    coordinator._last_firmware_refresh -= coordinator._firmware_refresh_interval + 1

    await coordinator.async_refresh()

    coordinator.api.async_get_firmware.assert_awaited_once()
    coordinator.api.async_get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_fast_endpoint_is_negatively_cached(hass, bypass_get_data):
    """A confirmed 404 is not retried during every fast cycle."""
    coordinator = await subject(hass)
    coordinator.api.async_get_delegated_vehicles = AsyncMock(
        side_effect=APIError(404, "response omitted")
    )

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    coordinator.api.async_get_delegated_vehicles.assert_awaited_once()
    assert coordinator.delegated_vehicles == {}


@pytest.mark.asyncio
async def test_home_charger_uses_domain_history_identity_resolution(
    hass, bypass_get_data
):
    """Only domain history identity resolution performs the legacy Pod read."""
    coordinator = await subject(hass)

    await coordinator.async_refresh()

    coordinator.api.async_get_all_pods.assert_awaited_once_with()
    coordinator.api.async_get_connectivity_status.assert_not_awaited()
    assert coordinator.data[0].ppid == "PSL-123456"
    assert coordinator.data[0].unit_id == 123456


@pytest.mark.asyncio
async def test_removed_legacy_account_and_live_history_do_not_fail_home_charger(
    hass, bypass_get_data
):
    """Confirmed removal only disables the legacy-derived capabilities."""
    coordinator = await subject(hass)
    coordinator.api.async_get_user = AsyncMock(
        side_effect=APIError(410, "response omitted")
    )
    coordinator.api.async_get_charges = AsyncMock(
        side_effect=APIError(410, "response omitted")
    )

    pods = await coordinator._async_update_data()

    assert [pod.ppid for pod in pods] == ["PSL-123456"]
    assert coordinator.user is None
    assert coordinator.live_sessions == {}
    assert len(coordinator.completed_sessions["PSL-123456"]) == 8


@pytest.mark.asyncio
async def test_removed_home_connectivity_does_not_fall_back_to_legacy(
    hass, bypass_get_data
):
    """A Home capability removal is isolated from the legacy API."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    assert "PSL-123456" in coordinator.charger_states

    coordinator.api.async_get_connectivity_status_v2 = AsyncMock(
        side_effect=APIError(410, "response omitted")
    )

    await coordinator.async_refresh()

    coordinator.api.async_get_connectivity_status.assert_not_awaited()
    assert "PSL-123456" not in coordinator.charger_states


@pytest.mark.asyncio
async def test_removed_charger_prunes_per_charger_caches(hass, bypass_get_data):
    """Rediscovery removes runtime data for chargers no longer on the account."""
    coordinator = await subject(hass)
    await coordinator.async_refresh()
    ppid = coordinator.data[0].ppid
    coordinator.pending_request_at[ppid] = datetime.now(UTC)
    coordinator.api.async_get_chargers.return_value = []

    await coordinator.async_refresh()

    assert coordinator.data == []
    for cache in (
        coordinator.charger_states,
        coordinator.metrics,
        coordinator.boost_states,
        coordinator.basic_charging_modes,
        coordinator.smart_charging_states,
        coordinator.completed_sessions,
        coordinator.live_sessions,
        coordinator.pending_request_at,
    ):
        assert ppid not in cache


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [APIError(503, "response omitted"), ApiConnectionError("temporary")],
)
async def test_optional_endpoint_transient_failure_fails_update(
    hass, bypass_get_data, error
):
    """5xx and connection errors are neither suppressed nor negatively cached."""
    coordinator = await subject(hass)
    coordinator.api.async_get_reward_wallet = AsyncMock(side_effect=error)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    coordinator.api.async_get_reward_wallet.assert_awaited_once()
    assert "reward wallet" not in coordinator._unsupported_until


# Test refreshes with connection errors fail as expected
@pytest.mark.asyncio
async def test_coordinator_refresh_connection_error(hass, error_on_get_data):
    """Test entry setup and unload."""
    # coordinator: PodPointDataUpdateCoordinator = await subject_with_data(hass)

    session = async_get_clientsession(hass)
    client = PodPointClient(
        username="test@example.com", password="password", session=session
    )

    client.async_get_chargers = AsyncMock(
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

    client.async_get_chargers = AsyncMock(
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

    client.async_get_chargers = AsyncMock(
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

    client.async_get_chargers = AsyncMock(
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
    charger = coordinator.data[0]
    firmware = coordinator.firmware[charger.ppid]
    firmware.update_status.is_update_available = True

    with patch(
        "custom_components.pod_point.coordinator.ir.async_create_issue"
    ) as create_issue:
        coordinator._PodPointDataUpdateCoordinator__process_repair_notification(
            hass, firmware, charger.ppid
        )

    assert create_issue.call_args.args[2] == f"firmware_update_{charger.ppid}"

    firmware.update_status.is_update_available = False
    with patch(
        "custom_components.pod_point.coordinator.ir.async_delete_issue"
    ) as delete_issue:
        coordinator._PodPointDataUpdateCoordinator__process_repair_notification(
            hass, firmware, charger.ppid
        )

    assert delete_issue.call_args.args[2] == f"firmware_update_{charger.ppid}"
