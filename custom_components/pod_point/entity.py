"""PodPointEntity class"""

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from podpointclient.charge_mode import ChargeMode
from podpointclient.domain import (
    BasicChargingMode,
    CapabilitySupport,
    ChargerCapability,
    ChargerRef,
    ChargerSchedule,
)

from .const import (
    APP_IMAGE_URL_BASE,
    ATTR_STATE,
    ATTR_STATE_AVAILABLE,
    ATTR_STATE_CHARGING,
    ATTR_STATE_CONNECTED_WAITING,
    ATTR_STATE_IDLE,
    ATTR_STATE_PENDING,
    ATTR_STATE_SUSPENDED_EV,
    ATTR_STATE_SUSPENDED_EVSE,
    ATTR_STATE_WAITING,
    ATTRIBUTION,
    CHARGING_FLAG,
    DOMAIN,
    NAME,
)
from .coordinator import ChargerMetrics, PodPointDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


class PodPointEntity(CoordinatorEntity):
    """Pod Point Entity"""

    def __init__(
        self,
        coordinator: PodPointDataUpdateCoordinator,
        config_entry: ConfigEntry,
        idx: int,
    ):
        super().__init__(coordinator)
        self._charger = coordinator.data[idx]
        self._charger_ppid = self._charger.ppid
        self.config_entry = config_entry
        self.extra_attrs = {}

        self.__update_attrs()

    def __update_attrs(self):
        charger = self.charger
        metrics = self.metrics
        attrs = {
            "attribution": ATTRIBUTION,
            "id": charger.unit_id,
            "integration": DOMAIN,
            "suggested_area": "Outside",
            "ppid": charger.ppid,
            "unit_id": charger.unit_id,
            "timezone": charger.timezone,
            "model": charger.model_name,
            "total_kwh": metrics.total_kwh,
            "total_charge_seconds": metrics.total_charge_seconds,
            "current_kwh": metrics.current_kwh,
            "charge_mode": self.charge_mode,
        }

        charger_state = self.coordinator.charger_states.get(charger.ppid)
        state = (
            charger_state.charging.value.value.replace("_", "-")
            if charger_state is not None and charger_state.charging.value is not None
            else None
        )
        is_available_state = (state == ATTR_STATE_AVAILABLE) or (
            state == ATTR_STATE_IDLE
        )
        is_charging_state = state == ATTR_STATE_CHARGING
        is_override_charge_mode = self.charge_mode == ChargeMode.OVERRIDE
        is_manual_charge_mode = self.charge_mode == ChargeMode.MANUAL
        charging_not_allowed = self.charging_allowed is False
        should_be_waiting_state = is_available_state and charging_not_allowed
        should_be_connected_waiting_state = is_charging_state and charging_not_allowed
        should_be_available = is_available_state and (
            is_override_charge_mode or is_manual_charge_mode
        )
        should_be_charging = is_charging_state and (
            is_override_charge_mode or is_manual_charge_mode
        )
        should_be_suspended_ev = is_charging_state and (
            state == ATTR_STATE_SUSPENDED_EV
        )
        should_be_suspended_evse = is_charging_state and (
            state == ATTR_STATE_SUSPENDED_EVSE
        )
        pending_at = self.coordinator.pending_request_at.get(charger.ppid)
        should_be_pending = pending_at is not None and (
            self.last_message_at is None or pending_at > self.last_message_at
        )
        if pending_at is not None and not should_be_pending:
            self.coordinator.pending_request_at.pop(charger.ppid, None)

        if should_be_waiting_state:
            state = ATTR_STATE_WAITING

        if should_be_connected_waiting_state:
            state = ATTR_STATE_CONNECTED_WAITING

        # A charger in override or manual mode should remain available.
        if should_be_available:
            state = ATTR_STATE_AVAILABLE

        # A charger in override or manual mode should remain charging.
        if should_be_charging:
            state = ATTR_STATE_CHARGING

        # Preserve suspended EVSE connectivity state.
        if should_be_suspended_evse:
            state = ATTR_STATE_SUSPENDED_EVSE

        # Preserve suspended EV connectivity state.
        if should_be_suspended_ev:
            state = ATTR_STATE_SUSPENDED_EV

        # Show a pending request until a newer charger message arrives.
        if should_be_pending:
            state = ATTR_STATE_PENDING

        attrs[ATTR_STATE] = state

        self._attr_state = state

        self.extra_attrs = attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    @property
    def charger(self) -> ChargerRef:
        """Return the canonical charger that drives this entity."""
        return next(
            (
                charger
                for charger in self.coordinator.data
                if charger.ppid == self._charger_ppid
            ),
            self._charger,
        )

    @property
    def metrics(self):
        """Return mutable charge aggregates for this charger."""
        return self.coordinator.metrics.get(self.charger.ppid, ChargerMetrics())

    @property
    def last_message_at(self) -> datetime | None:
        """Return the latest charger message timestamp."""
        state = self.coordinator.charger_states.get(self.charger.ppid)
        return state.last_seen_at if state is not None else None

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{DOMAIN}_{self.charger.ppid}"

    @property
    def available(self) -> bool:
        typed_coordinator: PodPointDataUpdateCoordinator = self.coordinator
        return (
            super().available
            and typed_coordinator.online is True
            and any(
                charger.ppid == self._charger_ppid for charger in typed_coordinator.data
            )
        )

    @property
    def device_info(self) -> dict[str, Any]:
        name = NAME
        if self.charger.ppid:
            name = self.charger.ppid

        dictionary = {
            "identifiers": {(DOMAIN, self.charger.ppid)},
            "name": name,
            "model": self.model,
            "manufacturer": NAME,
        }

        if self.firmware_version:
            dictionary["sw_version"] = self.firmware_version

        return dictionary

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return self.extra_attrs

    @property
    def charging_allowed(self) -> bool:
        """Is charging allowed by schedule?"""
        charger = self.charger
        if (
            charger is not None
            and charger.capability(ChargerCapability.LEGACY_SCHEDULING)
            is CapabilitySupport.UNSUPPORTED
        ):
            # Pod Home connectivity is authoritative. Legacy schedules are not
            # available to, or used by, the current app.
            return True
        schedules: list[ChargerSchedule] = self.coordinator.schedules.get(
            charger.ppid, []
        )

        # Are we in 'manual' mode?
        if self.charge_mode == ChargeMode.MANUAL:
            return True

        # No schedules are found, we will assume we can charge
        if len(schedules) <= 0:
            return True

        # If there is a charge override in place, we can charge
        boost = self.coordinator.boost_states.get(charger.ppid)
        if boost is not None and boost.active:
            return True

        try:
            timezone = ZoneInfo(
                charger.timezone or self.coordinator.hass.config.time_zone
            )
        except ZoneInfoNotFoundError:
            timezone = ZoneInfo("UTC")
        now = datetime.now(timezone)
        weekday = now.weekday() + 1
        schedule_for_day: ChargerSchedule = next(
            (schedule for schedule in schedules if schedule.start_day == weekday),
            None,
        )

        # If no schedule is set for our day, return False early, there should always be a
        # schedule for each day, even if it is inactive
        if schedule_for_day is None:
            return False

        schedule_active = schedule_for_day.is_active

        # If schedule_active is None, there was a problem. we will return False
        if schedule_active is None:
            return False

        # If the schedule for this day is not active, we can charge
        if schedule_active is False:
            return True

        def to_int(stringy_int):
            return int(stringy_int)

        start_time = list(map(to_int, schedule_for_day.start_time.split(":")))
        start_date = now.replace(
            hour=start_time[0], minute=start_time[1], second=start_time[2]
        )

        end_time = list(map(to_int, schedule_for_day.end_time.split(":")))
        end_day = schedule_for_day.end_day
        end_date = None
        if end_day < weekday:
            # roll into next week
            end_time = end_date = now.replace(
                hour=end_time[0], minute=end_time[1], second=end_time[2]
            )

            # How many days do we add to the current date to get to the desired end day?
            day_offset = (7 - weekday) + (end_day - 1)
            end_date = end_time + timedelta(days=day_offset)
        elif end_day > weekday:
            day_offset = end_day - weekday

            end_time = end_date = now.replace(
                hour=end_time[0], minute=end_time[1], second=end_time[2]
            )
            end_date = end_time + timedelta(days=day_offset)
        else:
            end_date = now.replace(
                hour=end_time[0], minute=end_time[1], second=end_time[2]
            )

        # Problem creating the end_date, so we will exit with False
        if end_date is None:
            return False

        in_range = start_date <= now <= end_date

        # Are we within the range for today?
        return in_range

    @property
    def unit_id(self) -> int:
        """Return the unit id - used for schedule updates"""
        return self.charger.unit_id

    @property
    def model(self) -> str:
        """Return the charger model."""
        return self.charger.model_name or NAME

    @property
    def firmware_version(self) -> str:
        """Return the charger's firmware version."""
        firmware = self.coordinator.firmware.get(self.charger.ppid)
        if firmware is not None and firmware.version_info is not None:
            return firmware.version_info.manifest_id
        return None

    @property
    def serial_number(self) -> str:
        """Return the serial number, or ppid"""
        firmware = self.coordinator.firmware.get(self.charger.ppid)
        if firmware is not None and firmware.serial_number:
            return firmware.serial_number
        return self.charger.ppid

    @property
    def image(self) -> str:
        """Return the image url for this model"""
        return self.__pod_image(self.model)

    @property
    def connected(self) -> bool:
        """Return whether the charger is connected to a vehicle."""
        status = self.extra_state_attributes.get(ATTR_STATE, "")
        return status in (
            CHARGING_FLAG,
            ATTR_STATE_CONNECTED_WAITING,
            ATTR_STATE_SUSPENDED_EV,
            ATTR_STATE_SUSPENDED_EVSE,
        )

    @property
    def smart_charging_active(self) -> bool:
        """Return whether delegated smart charging is active for this charger."""
        smart_charging = self.coordinator.smart_charging_states.get(self.charger.ppid)
        status = getattr(smart_charging, "status", None)
        return isinstance(status, str) and status.upper() in {"ACTIVE", "ENABLED"}

    @property
    def timed_charge_override_active(self) -> bool:
        """Return whether this charger has an active timed override."""
        boost = self.coordinator.boost_states.get(self.charger.ppid)
        return bool(boost and boost.active and boost.timed)

    @property
    def charge_now_available(self) -> bool:
        """Return whether a timed override is meaningful and observable."""
        mode = self.coordinator.basic_charging_modes.get(self.charger.ppid)
        if mode is None:
            return False
        return mode is not BasicChargingMode.ALWAYS_ON

    @property
    def charge_mode(self) -> ChargeMode | None:
        """Return the integration's established mode over canonical state."""
        if self.smart_charging_active:
            return ChargeMode.SMART
        mode = self.coordinator.basic_charging_modes.get(self.charger.ppid)
        if mode is BasicChargingMode.TIMED_BOOST:
            return ChargeMode.OVERRIDE
        if mode is BasicChargingMode.ALWAYS_ON:
            return ChargeMode.MANUAL
        if mode is BasicChargingMode.SCHEDULED:
            return ChargeMode.SMART
        return None

    def __pod_image(self, model: str) -> str:
        if model is None:
            return None

        model_slug = self.__model_slug()
        if len(model_slug) < 2:
            return None
        model_type = model_slug[0]
        model_id = model_slug[1]

        if model_type == "UP":
            model_type = "UC"

        if model_type == "1C":
            model_type = "2C"

        img = model_type

        if model_id == "03":
            img = f"{model_type}-{model_id}"

        if model_id == "05":
            img = "UC-05"

        return f"{APP_IMAGE_URL_BASE}/{img.lower()}.png"

    def __model_slug(self) -> list[str]:
        return self.model.upper()[3:8].split("-")

    @staticmethod
    def _td_format(td_object):
        seconds = int(td_object.total_seconds())
        periods = [
            ("year", 60 * 60 * 24 * 365),
            ("month", 60 * 60 * 24 * 30),
            ("day", 60 * 60 * 24),
            ("hour", 60 * 60),
            ("minute", 60),
            ("second", 1),
        ]

        strings = []
        for period_name, period_seconds in periods:
            if seconds > period_seconds:
                period_value, seconds = divmod(seconds, period_seconds)
                has_s = "s" if period_value > 1 else ""
                strings.append(f"{period_value} {period_name}{has_s}")

        output = "0s"
        if len(strings) > 0:
            output = ", ".join(strings)

        return output
