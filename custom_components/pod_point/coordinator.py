"""
Data coordinator for pod point client
"""

import asyncio
import logging
import re
from collections.abc import Awaitable
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from podpointclient.charge import Charge
from podpointclient.client import PodPointClient
from podpointclient.errors import ApiConnectionError, APIError, AuthError, SessionError
from podpointclient.pod import Firmware, Pod
from podpointclient.user import User

from .const import DOMAIN, LIMITED_POD_INCLUDES

_LOGGER: logging.Logger = logging.getLogger(__package__)


class PodPointDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    _hourly_refresh_interval = timedelta(hours=1).total_seconds()
    _remote_lock_refresh_interval = timedelta(minutes=30).total_seconds()
    _firmware_refresh_interval = timedelta(days=1).total_seconds()
    _idle_charge_refresh_interval = timedelta(hours=1).total_seconds()
    _history_refresh_interval = timedelta(hours=1).total_seconds()
    _history_recent_days = 7
    _history_match_tolerance = timedelta(seconds=60)
    _history_retry_interval = timedelta(minutes=5).total_seconds()
    _history_retry_limit = 3
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
        self.pods: list[Pod] = []
        self.home_charges: list[Charge] = []
        self.charges_perpage_all = (
            50  # When we are fetching all charges (new pod, or first launch)
        )
        self.charges_perpage_update = (
            3  # Fetching an update, unlikely to change from poll to poll by more than 1
        )
        self.pod_dict = None
        self.online = None
        self.user: User = None
        # Data exposed by the charger-centric Pod Home API.  These maps are keyed
        # by PPID so existing Pod based entities keep their stable identifiers.
        self.chargers: dict[str, Any] = {}
        self.connectivity_v2: dict[str, Any] = {}
        self.tariffs: dict[str, list[Any]] = {}
        # None means the endpoint failed; [] means it succeeded with no overrides.
        self.charge_overrides: dict[str, list[Any] | None] = {}
        self.charge_now_durations: dict[str, int] = {}
        self.smart_charging_preferences: dict[str, Any] = {}
        self.remote_locks: dict[str, Any] = {}
        self.delegated_vehicles: dict[str, Any] = {}
        self.reward_wallet: Any = None
        self._last_hourly_refresh: float | None = None
        self._last_remote_lock_refresh: float | None = None
        self._last_firmware_refresh: float | None = None
        self._last_charge_refresh: float | None = None
        self._last_history_refresh: float | None = None
        self._initial_charge_history_loaded = False
        self._legacy_full_history_loaded = False
        self._initial_new_history_loaded = False
        self._new_history_supported: bool | None = None
        self.completed_charges: dict[str, dict[Any, Any]] = {}
        self.provisional_charges: dict[str, Charge] = {}
        self.pending_finalisations: dict[str, Charge] = {}
        self._finalisation_retry_count: dict[str, int] = {}
        self._finalisation_retry_after: dict[str, float] = {}
        self._charger_live_states: dict[str, bool] = {}
        self._unsupported_until: dict[str, float] = {}
        self.last_message_at = datetime(1970, 1, 1, tzinfo=UTC)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )

    async def _async_update_data(self):
        """Update data via library."""
        try:
            _LOGGER.debug("Updating pods and charges")
            new_pods: list[Pod] = []
            self.pod_dict: dict[int, Pod] | None = None

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
                self.user = await self.api.async_get_user(includes=["account"])

            new_pods = await self.__async_update_pods()

            # Load Pod Home data before deriving entity state. Optional endpoints
            # are deliberately isolated: not every account has Rewards, a tariff,
            # or delegated smart charging enabled.
            await self.__async_update_pod_home_data(
                new_pods,
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
                len(new_pods),
                len(self.pods),
            )

            # Group Pods by ID so that we can organise our charges into the pods
            # they were performed on
            new_pods_by_id = self.__group_pods_by_unit_id(pods=new_pods)

            new_pods, new_pods_by_id = await self.__async_group_pods(
                new_pods, new_pods_by_id
            )

            new_pods_by_id = self.__group_pods_by_unit_id(pods=new_pods)

            # Firmware is loaded on startup, then refreshed on a daily cadence.
            if firmware_refresh_due:
                new_pods_by_id = await self.__async_refresh_firmware(
                    new_pods, new_pods_by_id
                )
                self._last_firmware_refresh = now

            # Fetch connection status data for pods
            new_pods_by_id = await self.__async_update_pod_connection_status(
                new_pods_by_id
            )

            # Determine live state per charger so transitions are not hidden when
            # another charger on the same account remains plugged in.
            should_fetch_all_charges = self.__should_fetch_all_charges(
                new_pods=new_pods
            )
            live_states = {
                pod.ppid: self._charger_is_live(pod.ppid) for pod in new_pods
            }
            became_idle_ppids = {
                ppid
                for ppid, was_live in self._charger_live_states.items()
                if was_live and not live_states.get(ppid, False)
            }
            for ppid in became_idle_ppids:
                if provisional := self.provisional_charges.get(ppid):
                    self.pending_finalisations[ppid] = provisional
                    self._finalisation_retry_count[ppid] = 0
                    self._finalisation_retry_after[ppid] = now

            any_charger_live = any(live_states.values())
            history_reconciliation_due = self.__refresh_due(
                self._last_history_refresh,
                self._history_refresh_interval,
                now,
            )
            finalisation_retry_due = any(
                self._finalisation_retry_count.get(ppid, 0) < self._history_retry_limit
                and self._finalisation_retry_after.get(ppid, 0) <= now
                for ppid in self.pending_finalisations
            )
            should_refresh_new_history = (
                not self._initial_new_history_loaded
                or bool(became_idle_ppids)
                or finalisation_retry_due
                or (not any_charger_live and history_reconciliation_due)
            )
            if should_refresh_new_history:
                await self.__async_refresh_new_charge_history(
                    new_pods,
                    now=now,
                    full_history=not self._initial_new_history_loaded,
                )

            idle_reconciliation_due = self.__refresh_due(
                self._last_charge_refresh,
                self._idle_charge_refresh_interval,
                now,
            )
            legacy_full_history_required = (
                self._new_history_supported is False
                and not self._legacy_full_history_loaded
            )
            should_refresh_legacy_charges = (
                not self._initial_charge_history_loaded
                or should_fetch_all_charges
                or any_charger_live
                or bool(became_idle_ppids)
                or legacy_full_history_required
                or (self._new_history_supported is False and idle_reconciliation_due)
            )

            new_charges: list[Charge] = []
            if should_refresh_legacy_charges:
                fetch_all_legacy_charges = legacy_full_history_required or (
                    should_fetch_all_charges and self._new_history_supported is not True
                )
                new_charges = await self.__fetch_home_charges(
                    all_charges=fetch_all_legacy_charges
                )
                self._last_charge_refresh = now
                self._initial_charge_history_loaded = True
                if fetch_all_legacy_charges:
                    self._legacy_full_history_loaded = True

            # We will filter out any of the new charges from the existing list. This will
            # ensure any overlap is not duplicated.
            new_charge_ids: set[int] = {charge.id for charge in new_charges}
            combined_home_charges: list[Charge] = new_charges + [
                charge
                for charge in self.home_charges
                if charge.id not in new_charge_ids
            ]

            _LOGGER.debug(
                "=== CHARGE UPDATE ===\nShould get all charges: %s\nPrevious Charges: %s\n\
Updated Charges: %s\nCombined Charges: %s",
                should_fetch_all_charges,
                len(self.home_charges),
                len(new_charges),
                len(combined_home_charges),
            )

            # Store charges for next refresh
            self.home_charges = combined_home_charges
            if should_refresh_legacy_charges:
                self.__update_provisional_charges(new_pods, new_charges, live_states)

            if self._new_history_supported is True:
                self.__apply_hybrid_charge_totals(new_pods_by_id)
            else:
                self.__apply_legacy_charge_totals(new_pods_by_id, combined_home_charges)

            self._charger_live_states = live_states

            self.pods = list(new_pods_by_id.values())

            if self.online is False:
                _LOGGER.info("Connection to Pod Point re-established.")
            self.online = True

            return self.pods  # sets coordinator.data

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
            _LOGGER.exception(exception)
            raise UpdateFailed() from exception

    def __group_pods_by_unit_id(self, pods: list[Pod] | None = None) -> dict[int, Pod]:
        """Given a list of pods, will return a dictionary { pod.unit_id: pod, *** }.
        If no pods are passed, will perfom on self.pods"""
        pod_dict: dict[int, Pod] = {}

        if pods is None:
            pods = self.pods

        for pod in pods:
            pod_dict[pod.unit_id] = pod

        self.pod_dict = pod_dict
        return self.pod_dict

    async def __fetch_home_charges(self, all_charges: bool = True) -> list[Charge]:
        """Fetch either all charges for a user, or progressively paginate until you have the latest
        set of charges. Filtered to only include 'home' charges"""
        charges: list[Charge] = []

        if all_charges:
            charges = await self.api.async_get_all_charges(
                perpage=self.charges_perpage_all
            )
        else:
            # Fetch charges until we have the most recent ones found, should reduce load
            # on the Pod Point servers
            last_charge_ids: list[int] = [
                pod.charges[0].id
                for pod in self.pods
                if (len(pod.charges) > 0) and pod.charges[0].id is not None
            ]
            charges = []

            page = 1
            while True:
                page_charges = await self.api.async_get_charges(
                    perpage=self.charges_perpage_update, page=page
                )

                # We should not get to a page with no charges before finding all the charges in our
                # list. If we do then go boom. Will cause HA to show an error updating data.
                if len(page_charges) == 0 and len(last_charge_ids) > 0:
                    raise Exception(
                        f"Attempting to update charges and recieved a 0 page when we were \
expecting more charges. Page {page}, looking for : {last_charge_ids}"
                    )

                if len(page_charges) == 0:
                    break

                # Process charges left to right, adding them to the 'back' of our charges
                # list until we hit one of the charges we are looking for.
                for charge in page_charges:
                    if charge.id is None:
                        continue

                    if charge.id not in last_charge_ids:
                        charges.append(charge)
                        continue

                    last_charge_ids.remove(charge.id)
                    charges.append(charge)

                    if len(last_charge_ids) == 0:
                        break

                if len(last_charge_ids) == 0:
                    break

                page += 1

        home_charges: list[Charge] = list(
            filter(lambda charge: charge.location.home is True, charges)
        )

        return home_charges

    def _charger_is_live(self, ppid: str) -> bool:
        """Return whether connectivity or delegated state reports a live session."""
        connectivity = self.connectivity_v2.get(ppid)
        state = self.__normalise_state(getattr(connectivity, "charging_state", None))
        if state in self._live_charging_states:
            return True

        delegated = self.delegated_vehicles.get(ppid)
        return any(
            getattr(vehicle, "is_plugged_in_to_this_charger", False) is True
            for vehicle in getattr(delegated, "vehicles", [])
        )

    def __history_date_range(
        self, pods: list[Pod], *, full_history: bool
    ) -> tuple[date, date]:
        """Return the inclusive full or overlapping recent history range."""
        today = datetime.now(UTC).date()
        if not full_history:
            return today - timedelta(days=self._history_recent_days), today

        linked_dates = [
            charger.linked_at.date()
            for charger in self.chargers.values()
            if getattr(charger, "linked_at", None) is not None
        ]
        pod_dates = [
            timestamp.date()
            for pod in pods
            if (timestamp := pod.commissioned_at or pod.created_at) is not None
        ]
        if len(linked_dates) < len(self.chargers):
            linked_dates.extend(pod_dates)
        if linked_dates:
            return min(linked_dates), today
        return (
            min(pod_dates) if pod_dates else today - timedelta(days=3650),
            today,
        )

    async def __async_refresh_new_charge_history(
        self, pods: list[Pod], *, now: float, full_history: bool
    ) -> None:
        """Refresh and merge canonical completed Pod Home history."""
        from_date, to_date = self.__history_date_range(pods, full_history=full_history)
        history = await self.__async_optional(
            lambda: self.api.async_get_charge_history(from_date, to_date),
            None,
            "charge history",
            now,
        )
        if history is None:
            self._new_history_supported = False
            return

        self._new_history_supported = True
        known_ppids = {pod.ppid for pod in pods}
        for charge in history.charges:
            if (
                charge.id is None
                or charge.ended_at is None
                or charge.charger_id not in known_ppids
            ):
                continue
            self.completed_charges.setdefault(charge.charger_id, {})[charge.id] = charge

        self._last_history_refresh = now
        self._initial_new_history_loaded = True
        self.__match_pending_finalisations()
        for ppid in self.pending_finalisations:
            retries = self._finalisation_retry_count.get(ppid, 0)
            self._finalisation_retry_count[ppid] = min(
                retries + 1, self._history_retry_limit
            )
            self._finalisation_retry_after[ppid] = now + self._history_retry_interval

    def __match_pending_finalisations(self) -> None:
        """Replace provisional sessions with canonical completed records."""
        matched_ppids = []
        for ppid, provisional in self.pending_finalisations.items():
            if any(
                self._completed_matches_provisional(ppid, completed, provisional)
                for completed in self.completed_charges.get(ppid, {}).values()
            ):
                matched_ppids.append(ppid)

        for ppid in matched_ppids:
            self.pending_finalisations.pop(ppid, None)
            self._finalisation_retry_count.pop(ppid, None)
            self._finalisation_retry_after.pop(ppid, None)

    def _completed_matches_provisional(
        self, ppid: str, completed: Any, provisional: Charge
    ) -> bool:
        """Correlate unrelated API IDs by charger and plug-in timestamp."""
        if (
            completed.charger_id != ppid
            or completed.plugged_in_at is None
            or provisional.starts_at is None
        ):
            return False
        return (
            abs(completed.plugged_in_at - provisional.starts_at)
            <= self._history_match_tolerance
        )

    def __update_provisional_charges(
        self,
        pods: list[Pod],
        charges: list[Charge],
        live_states: dict[str, bool],
    ) -> None:
        """Update active legacy records without clearing known live state on gaps."""
        for pod in pods:
            active = next(
                (
                    charge
                    for charge in charges
                    if charge.location.home is True
                    and charge.pod.id == pod.unit_id
                    and charge.ends_at is None
                ),
                None,
            )
            if active is not None and live_states.get(pod.ppid, False):
                self.provisional_charges[pod.ppid] = active
            elif not live_states.get(pod.ppid, False):
                self.provisional_charges.pop(pod.ppid, None)

    @staticmethod
    def __reset_pod_charge_totals(pod: Pod) -> None:
        """Reset derived values before replaying cached charge history."""
        pod.charges = []
        pod.total_kwh = 0.0
        pod.total_charge_seconds = 0
        pod.current_kwh = 0.0
        pod.total_cost = 0
        if hasattr(pod, "last_charge_cost"):
            delattr(pod, "last_charge_cost")

    def __apply_hybrid_charge_totals(self, pods_by_id: dict[int, Pod]) -> None:
        """Apply canonical completed history plus provisional legacy sessions."""
        for pod in pods_by_id.values():
            self.__reset_pod_charge_totals(pod)
            completed = list(self.completed_charges.get(pod.ppid, {}).values())
            for charge in completed:
                pod.total_kwh += charge.energy_total or 0
                pod.total_charge_seconds += charge.duration or 0
                pod.total_cost += charge.cost.amount or 0

            if completed:
                newest = max(
                    completed,
                    key=lambda charge: charge.ended_at
                    or datetime.min.replace(tzinfo=UTC),
                )
                pod.last_charge_cost = newest.cost.amount
                pod.charge_currency = newest.cost.currency

            contributions = []
            if pending := self.pending_finalisations.get(pod.ppid):
                contributions.append(pending)
            if provisional := self.provisional_charges.get(pod.ppid):
                contributions.append(provisional)
                pod.charges.append(provisional)
                pod.current_kwh = provisional.kwh_used

            for provisional in contributions:
                pod.total_kwh += provisional.kwh_used or 0
                pod.total_charge_seconds += provisional.charging_duration.raw or 0
                pod.total_cost += provisional.energy_cost or 0

    def __apply_legacy_charge_totals(
        self, pods_by_id: dict[int, Pod], charges: list[Charge]
    ) -> None:
        """Retain established legacy-only calculations when history is unsupported."""
        last_completed: dict[int, Charge] = {}
        for pod in pods_by_id.values():
            self.__reset_pod_charge_totals(pod)

        for charge in charges:
            pod = pods_by_id.get(charge.pod.id)
            if pod is None:
                continue
            pod.charges.append(charge)
            pod.total_kwh += charge.kwh_used
            pod.total_charge_seconds += charge.duration
            pod.total_cost += charge.energy_cost or 0
            if charge.ends_at is None:
                pod.current_kwh = charge.kwh_used
            elif (
                charge.pod.id not in last_completed
                or charge.ends_at > last_completed[charge.pod.id].ends_at
            ):
                last_completed[charge.pod.id] = charge
                pod.last_charge_cost = charge.energy_cost

    def __should_fetch_all_charges(self, new_pods: list[Pod]) -> bool:
        """Given a list of new pods, should we query for all charges on a users account,
        or just the most recent"""
        fetch_all_charges = False
        if len(new_pods) == len(self.pods):  # There are the same number of pods
            fetch_all_charges = not self.__pods_match(new_pods=new_pods)
        else:  # There are more (or less) pods than we previously had
            fetch_all_charges = True

        return fetch_all_charges

    def __combine_pods(self, new_pods_by_id: dict[str, Pod]) -> list[Pod]:
        """Given a new set of pods, combine them with the existing pod data to create a new list"""
        new_pods: list[Pod] = []

        for previous_pod in self.pods:
            new_pod = new_pods_by_id[previous_pod.unit_id]
            new_pod.price = previous_pod.price
            new_pod.model = previous_pod.model
            new_pod.unit_connectors = previous_pod.unit_connectors
            new_pod.firmware = previous_pod.firmware

            new_pods.append(new_pod)

        return new_pods

    def __pods_match(self, new_pods: list[Pod]) -> bool:
        set1 = set((pod.id) for pod in self.pods)
        difference = [pod for pod in new_pods if (pod.id) not in set1]

        # Is there a difference in the pod IDs?
        return len(difference) == 0

    def __process_repair_notification(
        self, hass: HomeAssistant, firmware: Firmware, pod: Pod
    ):
        issue_id = f"firmware_update_{pod.ppid}"
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
                translation_placeholders={"ppid": pod.ppid},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    async def __async_update_pods(self) -> list[Pod]:
        # Should we get a limited set of data (subsiquent refreshes)
        if len(self.pods) > 0:
            _LOGGER.debug("Existing pods found, performing a limited data pull")
            return await self.api.async_get_all_pods(includes=LIMITED_POD_INCLUDES)
        else:
            _LOGGER.debug("No existing pods found, performing a full data pull")
            return await self.api.async_get_all_pods()

    async def __async_group_pods(
        self, new_pods, new_pods_by_id
    ) -> tuple[list[Pod], dict[str, Pod]]:
        # Attempt to update our new pods with additional data from the existing pods.
        # This allows us to query less data each refresh, kinder on the Pod Point APIs.
        if self.__pods_match(new_pods=new_pods):
            # Created an updated list of pods combining old and new data
            _LOGGER.debug("Combining new and old pods")
            new_pods = self.__combine_pods(new_pods_by_id=new_pods_by_id)
            new_pods_by_id = self.__group_pods_by_unit_id(pods=new_pods)
        elif (
            len(self.pods) > 0
        ):  # Ensure that we are not re-querying if this is he first run
            _LOGGER.debug(
                "New pods from Pod Point do not match those saved. Performing a full data pull."
            )
            new_pods = await self.api.async_get_all_pods()

        return (new_pods, new_pods_by_id)

    async def __async_refresh_firmware(
        self, new_pods: list[Pod], new_pods_by_id: dict[str, Pod]
    ) -> dict[str, Pod]:
        _LOGGER.debug("=== FIRMWARE STATUS UPDATE ===")

        for pod in new_pods:
            pod_firmwares: list[Firmware] = await self.api.async_get_firmware(pod=pod)

            if len(pod_firmwares) <= 0:
                _LOGGER.warning(
                    "Unable to retrive firmware information for Pod %s",
                    pod.ppid,
                )
            else:
                for firmware in pod_firmwares:
                    self.__process_repair_notification(
                        hass=self.hass, firmware=firmware, pod=pod
                    )

                    # Populate the firmware of the pod
                    pod.firmware = firmware
                    new_pods_by_id[pod.unit_id] = pod

        return new_pods_by_id

    async def __async_update_pod_connection_status(
        self, new_pods_by_id: dict[str, Pod]
    ) -> dict[str, Pod]:
        _LOGGER.debug("=== POD CONNECTION STATUS UPDATE ===")

        # Fetch connection status for each pod
        for pod in new_pods_by_id.values():
            connectivity_status_v2 = self.connectivity_v2.get(pod.ppid)
            if connectivity_status_v2 is not None:
                pod.connectivity_status_v2 = connectivity_status_v2
                pod.last_message_at = connectivity_status_v2.last_seen_at
                pod.charging_state = self.__normalise_state(
                    connectivity_status_v2.charging_state
                )
                new_pods_by_id[pod.unit_id] = pod
                continue

            # Retain the legacy endpoint as a fallback for chargers which have
            # not yet been migrated to the Pod Home API.
            connectivity_status = await self.api.async_get_connectivity_status(pod=pod)

            if connectivity_status is not None:
                pod.connectivity_status = connectivity_status
                pod.last_message_at = connectivity_status.last_message_at
                pod.charging_state = connectivity_status.charging_state

                if pod.charging_state is not None:
                    pod.charging_state = pod.charging_state.lower().replace("_", "-")

                new_pods_by_id[pod.unit_id] = pod

        return new_pods_by_id

    @staticmethod
    def __refresh_due(last_refresh: float | None, interval: float, now: float) -> bool:
        """Return whether a time-based cache is due for refresh."""
        return last_refresh is None or now - last_refresh >= interval

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
        """Resolve an optional endpoint, caching only confirmed unsupported 404s."""
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
            if self.__api_error_status(exception) != 404:
                raise
            self._unsupported_until[name] = now + self._unsupported_retry_interval
            _LOGGER.debug("Pod Home endpoint %s is unsupported (HTTP 404)", name)
            return default

    async def __async_update_pod_home_data(
        self,
        pods: list[Pod],
        *,
        now: float,
        hourly_refresh_due: bool,
        remote_lock_refresh_due: bool,
    ) -> None:
        """Fetch charger-centric Pod Home state and associate it with legacy pods."""
        if not hasattr(self.api, "async_get_chargers"):
            return

        chargers = await self.api.async_get_chargers()
        self.chargers = {charger.ppid: charger for charger in chargers}

        delegated = await self.__async_optional(
            self.api.async_get_delegated_vehicles,
            [],
            "delegated vehicles",
            now,
        )
        self.delegated_vehicles = {item.ppid: item for item in delegated}
        if hourly_refresh_due:
            self.reward_wallet = await self.__async_optional(
                self.api.async_get_reward_wallet,
                None,
                "reward wallet",
                now,
            )

        for pod in pods:
            charger = self.chargers.get(pod.ppid)
            if charger is None:
                continue

            (
                self.connectivity_v2[pod.ppid],
                self.charge_overrides[pod.ppid],
            ) = await asyncio.gather(
                self.__async_optional(
                    lambda charger=charger: self.api.async_get_connectivity_status_v2(
                        charger
                    ),
                    None,
                    f"connectivity for {pod.ppid}",
                    now,
                ),
                self.__async_optional(
                    lambda charger=charger: self.api.async_get_charger_charge_overrides(
                        charger, active_only=True
                    ),
                    None,
                    f"charge overrides for {pod.ppid}",
                    now,
                ),
            )

            slow_updates = []
            if (
                hourly_refresh_due
                or pod.ppid not in self.tariffs
                or pod.ppid not in self.smart_charging_preferences
            ):
                slow_updates.extend(
                    (
                        (
                            self.tariffs,
                            lambda charger=charger: self.api.async_get_tariffs(charger),
                            [],
                            f"tariffs for {pod.ppid}",
                        ),
                        (
                            self.smart_charging_preferences,
                            lambda charger=charger: (
                                self.api.async_get_smart_charging_preferences(charger)
                            ),
                            None,
                            f"smart charging preferences for {pod.ppid}",
                        ),
                    )
                )
            if remote_lock_refresh_due or pod.ppid not in self.remote_locks:
                slow_updates.append(
                    (
                        self.remote_locks,
                        lambda charger=charger: self.api.async_get_remote_lock(charger),
                        None,
                        f"remote lock for {pod.ppid}",
                    )
                )

            if not slow_updates:
                continue
            results = await asyncio.gather(
                *(
                    self.__async_optional(factory, default, name, now)
                    for _, factory, default, name in slow_updates
                )
            )
            for (cache, _, _, _), result in zip(slow_updates, results, strict=True):
                cache[pod.ppid] = result

    async def async_set_smart_charging_max_price(
        self, charger: Any, value: float
    ) -> bool:
        """Set and immediately refresh cached smart-charging preferences."""
        succeeded = await self.async_api_call(
            self.api.async_set_smart_charging_max_price(charger, value)
        )
        if not succeeded:
            return False

        preferences = await self.async_api_call(
            self.api.async_get_smart_charging_preferences(charger)
        )
        self.smart_charging_preferences[charger.ppid] = preferences
        self.async_set_updated_data(self.data)
        return True

    @staticmethod
    def __normalise_state(state: str | None) -> str | None:
        """Convert Pod Home title/camel/snake case states to HA enum values."""
        if state is None:
            return None

        # Examples returned by the API include ``SuspendedEVSE`` and
        # ``OutOfService`` as well as older upper snake-case variants.
        value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", state.strip())
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
        return value.lower().replace("_", "-")
