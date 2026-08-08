"""
Data coordinator for pod point client
"""

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from podpointclient.client import PodPointClient
from podpointclient.domain import (
    BasicChargingMode,
    BoostState,
    ChargerRef,
    ChargerState,
    ChargeSession,
    reconcile_charge_sessions,
)
from podpointclient.errors import (
    ApiConnectionError,
    APIError,
    AuthError,
    SessionError,
    UnsupportedCapabilityError,
)
from podpointclient.pod import Firmware
from podpointclient.schedule import Schedule
from podpointclient.user import User

from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__package__)


@dataclass
class ChargerMetrics:
    """Mutable charge aggregates derived from canonical sessions."""

    total_kwh: float = 0.0
    total_charge_seconds: int = 0
    current_kwh: float = 0.0
    total_cost: float = 0.0
    last_charge_cost: float | None = None
    charge_currency: str | None = None
    active_started_at: datetime | None = None


class PodPointDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    _hourly_refresh_interval = timedelta(hours=1).total_seconds()
    _remote_lock_refresh_interval = timedelta(minutes=30).total_seconds()
    _firmware_refresh_interval = timedelta(days=1).total_seconds()
    _idle_charge_refresh_interval = timedelta(hours=1).total_seconds()
    _history_refresh_interval = timedelta(hours=1).total_seconds()
    _history_recent_days = 7
    _history_match_tolerance = timedelta(seconds=60)
    _unsupported_retry_interval = timedelta(hours=1).total_seconds()
    _live_charging_states = frozenset({"charging", "suspended-ev", "suspended-evse"})

    async def async_api_call[T](self, awaitable: Awaitable[T]) -> T:
        """Execute a user-initiated API request with an actionable HA error."""
        try:
            return await awaitable
        except APIError as err:
            raise HomeAssistantError("Pod Point rejected the request") from err

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: PodPointClient,
        scan_interval: timedelta | None,
    ) -> None:
        """Initialize."""
        self.api: PodPointClient = client
        self.platforms = []
        self.charges_perpage_update = (
            3  # Fetching an update, unlikely to change from poll to poll by more than 1
        )
        self.online = None
        self.user: User = None
        # Mutable runtime values are keyed by canonical PPID.
        self.charger_states: dict[str, ChargerState] = {}
        self.firmware: dict[str, Firmware] = {}
        self.metrics: dict[str, ChargerMetrics] = {}
        self.tariffs: dict[str, list[Any]] = {}
        # None means the endpoint failed; [] means it succeeded with no overrides.
        self.boost_states: dict[str, BoostState | None] = {}
        self.charge_now_durations: dict[str, int] = {}
        self.smart_charging_preferences: dict[str, Any] = {}
        self.remote_locks: dict[str, Any] = {}
        self.delegated_vehicles: dict[str, Any] = {}
        self.reward_wallet: Any = None
        self.basic_charging_modes: dict[str, BasicChargingMode | None] = {}
        self.smart_charging_states: dict[str, Any] = {}
        self.legacy_schedules: dict[str, list[Schedule]] = {}
        self._last_hourly_refresh: float | None = None
        self._last_remote_lock_refresh: float | None = None
        self._last_firmware_refresh: float | None = None
        self._last_charge_refresh: float | None = None
        self._last_history_refresh: float | None = None
        self._initial_new_history_loaded = False
        self.completed_sessions: dict[str, dict[str, ChargeSession]] = {}
        self.live_sessions: dict[str, list[ChargeSession]] = {}
        self.pending_sessions: dict[str, ChargeSession] = {}
        self._charger_live_states: dict[str, bool] = {}
        self._unsupported_until: dict[str, float] = {}
        self.pending_request_at: dict[str, datetime] = {}

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )

    def mark_charger_pending(self, charger: ChargerRef) -> None:
        """Mark a charger request pending until diagnostics catch up."""
        self.pending_request_at[charger.ppid] = datetime.now(UTC)

    async def _async_update_data(self):
        """Update data via library."""
        try:
            _LOGGER.debug("Updating pods and charges")

            now = monotonic()
            hourly_refresh_due = self.__refresh_due(
                self._last_hourly_refresh, self._hourly_refresh_interval, now
            )
            remote_lock_refresh_due = self.__refresh_due(
                self._last_remote_lock_refresh,
                self._remote_lock_refresh_interval,
                now,
            )
            firmware_refresh_due = self.__refresh_due(
                self._last_firmware_refresh, self._firmware_refresh_interval, now
            )

            if hourly_refresh_due:
                self.user = await self.__async_optional(
                    lambda: self.api.async_get_user(includes=["account"]),
                    None,
                    "legacy user account",
                    now,
                )

            # Discovery and Home-to-legacy fallback are owned by podpointclient.
            previous_charger_count = len(self.data or [])
            charger_refs = await self.api.async_discover_chargers()
            self.__prune_removed_chargers(charger_refs)
            for charger in charger_refs:
                self.metrics.setdefault(charger.ppid, ChargerMetrics())

            # Load charger data before deriving entity state. Optional endpoints
            # are deliberately isolated: not every account has Rewards, a tariff,
            # or delegated smart charging enabled.
            await self.__async_update_charger_data(
                charger_refs,
                now=now,
                hourly_refresh_due=hourly_refresh_due,
                remote_lock_refresh_due=remote_lock_refresh_due,
            )
            if hourly_refresh_due:
                self._last_hourly_refresh = now
            if remote_lock_refresh_due:
                self._last_remote_lock_refresh = now

            _LOGGER.debug(
                "=== POD UPDATE ===\nFound Pods: %s\nPrevious Pods: %s",
                len(charger_refs),
                previous_charger_count,
            )

            # Firmware is loaded on startup, then refreshed on a daily cadence.
            if firmware_refresh_due:
                await self.__async_refresh_firmware(charger_refs)
                self._last_firmware_refresh = now

            # Determine live state per charger so transitions are not hidden when
            # another charger on the same account remains plugged in.
            live_states = {
                charger.ppid: self._charger_is_live(charger.ppid)
                for charger in charger_refs
            }
            became_idle_ppids = {
                ppid
                for ppid, was_live in self._charger_live_states.items()
                if was_live and not live_states.get(ppid, False)
            }
            for ppid in became_idle_ppids:
                if sessions := self.live_sessions.get(ppid):
                    self.pending_sessions[ppid] = sessions[-1]

            any_charger_live = any(live_states.values())
            history_reconciliation_due = self.__refresh_due(
                self._last_history_refresh,
                self._history_refresh_interval,
                now,
            )
            should_refresh_completed = (
                not self._initial_new_history_loaded
                or bool(became_idle_ppids)
                or (not any_charger_live and history_reconciliation_due)
            )
            if should_refresh_completed:
                await self.__async_refresh_completed_sessions(
                    charger_refs,
                    now=now,
                    full_history=not self._initial_new_history_loaded,
                )

            live_refresh_due = self.__refresh_due(
                self._last_charge_refresh,
                self._idle_charge_refresh_interval,
                now,
            )
            should_refresh_live = (
                any_charger_live or bool(became_idle_ppids) or live_refresh_due
            )
            if should_refresh_live:
                await self.__async_refresh_live_sessions(charger_refs)
                self._last_charge_refresh = now

            self.__apply_domain_charge_totals(charger_refs)

            self._charger_live_states = live_states

            if self.online is False:
                _LOGGER.info("Connection to Pod Point re-established.")
            self.online = True

            return charger_refs  # sets coordinator.data

        except ApiConnectionError as exception:
            if self.online is not False:
                _LOGGER.warning("Unable to connect to Pod Point. (%s)", exception)

            self.online = False
            _LOGGER.debug(exception)

            raise UpdateFailed(
                "Unable to connect to Pod Point. Retrying"
            ) from exception

        except (AuthError, SessionError) as exception:
            _LOGGER.debug("Recommending re-auth: %s", exception)

            raise ConfigEntryAuthFailed(
                "There was a problem logging in with your account."
            ) from exception
        except Exception as exception:
            _LOGGER.warning(
                "Recieved an unexpected exception when updating data from Pod Point. \
If this issue persists, please contact the developer."
            )
            _LOGGER.exception("Unexpected Pod Point update failure")
            raise UpdateFailed() from exception

    async def __async_refresh_live_sessions(self, chargers: list[ChargerRef]) -> None:
        """Refresh canonical live sessions without selecting a wire API."""
        groups = await self.__async_domain_optional(
            lambda: self.api.async_get_live_charge_sessions(
                chargers,
                per_page=self.charges_perpage_update,
            ),
            None,
            "live charge sessions",
        )
        if groups is None:
            return

        self.live_sessions = groups

    def _charger_is_live(self, ppid: str) -> bool:
        """Return whether connectivity or delegated state reports a live session."""
        state = self.charger_states.get(ppid)
        if (
            state is not None
            and state.charging.value is not None
            and state.charging.value.value.replace("_", "-")
            in self._live_charging_states
        ):
            return True

        delegated = self.delegated_vehicles.get(ppid)
        return any(
            getattr(vehicle, "is_plugged_in_to_this_charger", False) is True
            for vehicle in getattr(delegated, "vehicles", [])
        )

    def __history_date_range(
        self, chargers: list[ChargerRef], *, full_history: bool
    ) -> tuple[date, date]:
        """Return the inclusive full or overlapping recent history range."""
        today = datetime.now(UTC).date()
        if not full_history:
            return today - timedelta(days=self._history_recent_days), today

        linked_dates = [
            charger.linked_at.date()
            for charger in chargers
            if charger.linked_at is not None
        ]
        if linked_dates:
            return min(linked_dates), today
        return today - timedelta(days=3650), today

    async def __async_refresh_completed_sessions(
        self, chargers: list[ChargerRef], *, now: float, full_history: bool
    ) -> None:
        """Refresh canonical completed sessions with library-owned fallback."""
        from_date, to_date = self.__history_date_range(
            chargers, full_history=full_history
        )
        groups = await self.__async_domain_optional(
            lambda: self.api.async_get_completed_charge_sessions(
                chargers, from_date, to_date
            ),
            None,
            "completed charge sessions",
        )
        if groups is None:
            return

        for ppid, sessions in groups.items():
            cache = self.completed_sessions.setdefault(ppid, {})
            for session in sessions:
                key = session.session_id or session.correlation_key
                cache[f"{session.source.value}:{key}"] = session

        self._last_history_refresh = now
        self._initial_new_history_loaded = True

    def __apply_domain_charge_totals(self, chargers: list[ChargerRef]) -> None:
        """Apply reconciled canonical completed, pending, and live sessions."""
        for charger in chargers:
            metrics = ChargerMetrics()
            completed = list(self.completed_sessions.get(charger.ppid, {}).values())
            provisional = list(self.live_sessions.get(charger.ppid, []))
            if pending := self.pending_sessions.get(charger.ppid):
                provisional.append(pending)
            sessions = reconcile_charge_sessions(
                completed,
                provisional,
                tolerance=self._history_match_tolerance,
            )

            if pending and pending not in sessions:
                self.pending_sessions.pop(charger.ppid, None)

            live_sessions = self.live_sessions.get(charger.ppid, [])
            for session in sessions:
                metrics.total_kwh += session.energy_kwh or 0
                metrics.total_charge_seconds += session.duration_seconds or 0
                metrics.total_cost += session.cost or 0
                if session.active and session in live_sessions:
                    metrics.current_kwh += session.energy_kwh or 0
                    metrics.active_started_at = session.started_at

            completed_sessions = [session for session in sessions if not session.active]
            if completed_sessions:
                newest = max(
                    completed_sessions,
                    key=lambda session: (
                        session.ended_at or datetime.min.replace(tzinfo=UTC)
                    ),
                )
                metrics.last_charge_cost = newest.cost
                metrics.charge_currency = newest.currency
            self.metrics[charger.ppid] = metrics

    def __process_repair_notification(
        self, hass: HomeAssistant, firmware: Firmware, ppid: str
    ):
        issue_id = f"firmware_update_{ppid}"
        if firmware.update_available:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                learn_more_url="https://pod-point.com/electric-car-news",
                severity="other",
                translation_key="firmware_update",
                translation_placeholders={"ppid": ppid},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    async def __async_refresh_firmware(self, chargers: list[ChargerRef]) -> None:
        _LOGGER.debug("=== FIRMWARE STATUS UPDATE ===")

        for charger in chargers:
            pod_firmwares: list[Firmware] | None = await self.__async_domain_optional(
                lambda charger=charger: self.api.async_get_charger_firmware(charger),
                None,
                f"firmware for {charger.ppid}",
            )

            if pod_firmwares is None:
                continue
            if len(pod_firmwares) <= 0:
                _LOGGER.warning(
                    "Unable to retrive firmware information for Pod %s",
                    charger.ppid,
                )
            else:
                for firmware in pod_firmwares:
                    self.__process_repair_notification(
                        hass=self.hass, firmware=firmware, ppid=charger.ppid
                    )
                    self.firmware[charger.ppid] = firmware

    @staticmethod
    def __refresh_due(last_refresh: float | None, interval: float, now: float) -> bool:
        """Return whether a time-based cache is due for refresh."""
        return last_refresh is None or now - last_refresh >= interval

    def __prune_removed_chargers(self, chargers: list[ChargerRef]) -> None:
        """Remove cached data belonging to chargers no longer discovered."""
        active_ppids = {charger.ppid for charger in chargers}
        caches = (
            self.charger_states,
            self.firmware,
            self.metrics,
            self.tariffs,
            self.boost_states,
            self.charge_now_durations,
            self.smart_charging_preferences,
            self.remote_locks,
            self.delegated_vehicles,
            self.basic_charging_modes,
            self.smart_charging_states,
            self.legacy_schedules,
            self.completed_sessions,
            self.live_sessions,
            self.pending_sessions,
            self._charger_live_states,
            self.pending_request_at,
        )
        for cache in caches:
            for ppid in cache.keys() - active_ppids:
                cache.pop(ppid, None)

    @staticmethod
    def __api_error_status(exception: APIError) -> int | None:
        """Return the structured HTTP status supplied by podpointclient."""
        status = getattr(exception, "status", None)
        if isinstance(status, int):
            return status
        # Current podpointclient supplies ``(status, redacted_response)`` as
        # exception arguments. This is structured data, not a parsed message.
        if exception.args and isinstance(exception.args[0], int):
            return exception.args[0]
        return None

    async def __async_optional(
        self, awaitable_factory, default: Any, name: str, now: float
    ):
        """Resolve an optional endpoint, caching confirmed endpoint removal."""
        if self._unsupported_until.get(name, 0) > now:
            return default
        try:
            result = await awaitable_factory()
            self._unsupported_until.pop(name, None)
            return result
        except (AuthError, SessionError):
            raise
        except ApiConnectionError:
            raise
        except APIError as exception:
            status = self.__api_error_status(exception)
            if status not in (404, 410):
                raise
            self._unsupported_until[name] = now + self._unsupported_retry_interval
            _LOGGER.debug(
                "Pod Point endpoint %s is unsupported (HTTP %s)", name, status
            )
            return default

    async def __async_domain_optional(self, awaitable_factory, default: Any, name: str):
        """Resolve an optional domain capability using the library's support state."""
        try:
            return await awaitable_factory()
        except UnsupportedCapabilityError:
            _LOGGER.debug("Pod Point capability %s is unsupported", name)
            return default

    async def __async_update_charger_data(
        self,
        chargers: list[ChargerRef],
        *,
        now: float,
        hourly_refresh_due: bool,
        remote_lock_refresh_due: bool,
    ) -> None:
        """Fetch capability-driven state for canonical chargers."""
        delegated = await self.__async_domain_optional(
            self.api.async_get_domain_delegated_vehicle_groups,
            {},
            "delegated vehicles",
        )
        self.delegated_vehicles = {
            ppid: items[0] for ppid, items in delegated.items() if items
        }
        if hourly_refresh_due:
            self.reward_wallet = await self.__async_domain_optional(
                self.api.async_get_account_reward_wallet,
                None,
                "reward wallet",
            )

        for charger in chargers:
            ppid = charger.ppid
            state, boost, smart_charging, legacy_schedules = await asyncio.gather(
                self.__async_domain_optional(
                    lambda charger=charger: self.api.async_get_charger_state(charger),
                    None,
                    f"state for {ppid}",
                ),
                self.__async_domain_optional(
                    lambda charger=charger: self.api.async_get_active_boost(charger),
                    None,
                    f"active boost for {ppid}",
                ),
                self.__async_domain_optional(
                    lambda charger=charger: self.api.async_get_charger_smart_charging(
                        charger
                    ),
                    None,
                    f"smart charging for {ppid}",
                ),
                self.__async_domain_optional(
                    lambda charger=charger: self.api.async_get_charger_legacy_schedules(
                        charger
                    ),
                    None,
                    f"legacy schedules for {ppid}",
                ),
            )
            if state is None:
                self.charger_states.pop(ppid, None)
            else:
                self.charger_states[ppid] = state
            self.boost_states[ppid] = boost
            basic_mode = (
                await self.__async_domain_optional(
                    lambda charger=charger, boost=boost: (
                        self.api.async_get_basic_charging_mode(
                            charger, boost_state=boost
                        )
                    ),
                    None,
                    f"basic charging mode for {ppid}",
                )
                if boost is not None
                else None
            )
            self.basic_charging_modes[ppid] = basic_mode
            if smart_charging is None:
                self.smart_charging_states.pop(ppid, None)
            else:
                self.smart_charging_states[ppid] = smart_charging
            if legacy_schedules is None:
                self.legacy_schedules.pop(ppid, None)
            else:
                self.legacy_schedules[ppid] = legacy_schedules

            slow_updates = []
            if (
                hourly_refresh_due
                or ppid not in self.tariffs
                or ppid not in self.smart_charging_preferences
            ):
                slow_updates.extend(
                    (
                        (
                            self.tariffs,
                            lambda charger=charger: self.api.async_get_charger_tariffs(
                                charger
                            ),
                            [],
                            f"tariffs for {ppid}",
                        ),
                        (
                            self.smart_charging_preferences,
                            lambda charger=charger: (
                                self.api.async_get_charger_preferences(charger)
                            ),
                            None,
                            f"smart charging preferences for {ppid}",
                        ),
                    )
                )
            if remote_lock_refresh_due or ppid not in self.remote_locks:
                slow_updates.append(
                    (
                        self.remote_locks,
                        lambda charger=charger: self.api.async_get_charger_remote_lock(
                            charger
                        ),
                        None,
                        f"remote lock for {ppid}",
                    )
                )

            if not slow_updates:
                continue
            results = await asyncio.gather(
                *(
                    self.__async_domain_optional(factory, default, name)
                    for _, factory, default, name in slow_updates
                )
            )
            for (cache, _, _, _), result in zip(slow_updates, results, strict=True):
                cache[ppid] = result

    async def async_set_smart_charging_max_price(
        self, charger: ChargerRef, value: float
    ) -> bool:
        """Set and immediately refresh cached smart-charging preferences."""
        succeeded = await self.async_api_call(
            self.api.async_set_charger_max_price(charger, value)
        )
        if not succeeded:
            return False

        preferences = await self.async_api_call(
            self.api.async_get_charger_preferences(charger)
        )
        self.smart_charging_preferences[charger.ppid] = preferences
        self.async_set_updated_data(self.data)
        return True
