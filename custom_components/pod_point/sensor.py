"""Sensor platform for pod_point."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfEnergy,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from podpointclient.charge_mode import ChargeMode
from podpointclient.charge_override import ChargeOverride
from podpointclient.connectivity_status import Evse
from podpointclient.pod import Pod
from podpointclient.user import User

from .const import (
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
    ATTRIBUTION,
    CONF_CURRENCY,
    DEFAULT_CURRENCY,
    DOMAIN,
    ICON,
    ICON_1C,
    ICON_2C,
)
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Handle coordinator offline on boot - no data will be populated
    if coordinator.online is False:
        return

    sensors = []
    known_vehicle_keys = set()

    def _vehicle_key(ppid: str, vehicle_idx: int, vehicle: Dict[str, Any]):
        vehicle_id = vehicle.get("id") or _get_nested(vehicle, "vehicle", "id")

        if vehicle_id is None:
            vehicle_id = vehicle_idx

        return (ppid, vehicle_id)

    def _new_vehicle_sensors():
        new_sensors = []

        for idx, pod in enumerate(coordinator.data):
            vehicles = coordinator.home_app_vehicles_by_ppid.get(pod.ppid, [])

            for vehicle_idx, vehicle in enumerate(vehicles):
                if not isinstance(vehicle, dict):
                    continue

                key = _vehicle_key(pod.ppid, vehicle_idx, vehicle)

                if key in known_vehicle_keys:
                    continue

                known_vehicle_keys.add(key)
                new_sensors.append(
                    PodPointHomeAppVehicleSensor(
                        coordinator, entry, idx, vehicle_idx
                    )
                )

        return new_sensors

    @callback
    def _async_add_new_home_app_vehicle_sensors() -> None:
        new_sensors = _new_vehicle_sensors()

        if new_sensors:
            async_add_devices(new_sensors)

    for i in range(len(coordinator.data)):
        pps = PodPointSensor(coordinator, entry, i)
        ppcts = PodPointChargeTimeSensor(coordinator, entry, i)
        pptes = PodPointTotalEnergySensor(coordinator, entry, i)
        ppces = PodPointCurrentEnergySensor(coordinator, entry, i)
        ppsss = PodPointSignalStrengthSensor(coordinator, entry, i)
        pplmrs = PodPointLastMessageReceivedSensor(coordinator, entry, i)
        pptcs = PodPointTotalCostSensor(coordinator, entry, i)
        pplcccs = PodPointLastCompleteChargeCostSensor(coordinator, entry, i)
        charge_mode = PodPointChargeModeEntity(coordinator, entry, i)
        charge_override = PodPointChargeOverrideEntity(coordinator, entry, i)

        sensors.append(pps)
        sensors.append(ppcts)
        sensors.append(pptes)
        sensors.append(ppces)
        sensors.append(ppsss)
        sensors.append(pplmrs)
        sensors.append(pptcs)
        sensors.append(pplcccs)
        sensors.append(charge_mode)
        sensors.append(charge_override)

    sensors.extend(_new_vehicle_sensors())
    sensors.append(PodPointAccountBalanceEntity(coordinator, entry))
    sensors.append(PodPointRewardWalletEntity(coordinator, entry))

    async_add_devices(sensors)
    entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_home_app_vehicle_sensors)
    )


def _get_nested(dictionary: Dict[str, Any], *keys):
    value = dictionary

    for key in keys:
        if not isinstance(value, dict):
            return None

        value = value.get(key)

    return value


class PodPointSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point Sensor class."""

    _attr_options = [
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
    ]
    _attr_translation_key = "status"
    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_device_class = SensorDeviceClass.ENUM

    @property
    def unique_id(self):
        return f"{super().unique_id}_status"

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes.get(ATTR_STATE, None)

    @property
    def icon(self):
        """Return the icon of the sensor."""
        model_slug = self.model.upper()[3:8].split("-")
        model_type = model_slug[0]

        if model_type == "1C":
            return ICON_1C

        if model_type == "2C":
            return ICON_2C

        if model_type == "UC":
            return ICON

        return ICON

    @property
    def entity_picture(self) -> str:
        return self.image


class PodPointChargeTimeSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point Sensor class."""

    _attr_has_entity_name = True
    _attr_name = "Completed Charge Time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def unique_id(self):
        return f"{super().unique_id}_charge_time"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            "raw": self.pod.total_charge_seconds,
            "formatted": str(timedelta(seconds=self.pod.total_charge_seconds)),
            "long": self._td_format(timedelta(seconds=self.pod.total_charge_seconds)),
        }

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["raw"]

    @property
    def entity_picture(self) -> str:
        return None


class PodPointSignalStrengthSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point Signal Strength sensor class."""

    _attr_translation_key = "signal_strength"
    _attr_has_entity_name = True
    _attr_name = "Signal Strength"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry: ConfigEntry, idx: int):
        super().__init__(coordinator, config_entry=config_entry, idx=idx)
        self.__update_attrs()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    def __update_attrs(self):
        signal_strength = self.__signal_strength()
        connection_quality = self.__connection_quality()

        attrs = {
            "attribution": ATTRIBUTION,
            "integration": DOMAIN,
            "signal_strength": signal_strength,
            "connection_quality": connection_quality,
        }

        self.extra_attrs = attrs

    @property
    def unique_id(self):
        return f"{super().unique_id}_signal_strength"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return self.extra_attrs

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["signal_strength"]

    @property
    def native_unit_of_measurement(self):
        return SIGNAL_STRENGTH_DECIBELS

    @property
    def icon(self):
        """Return the icon of the sensor."""
        icon = "mdi:wifi-strength-1"

        connection_quality = self.__connection_quality()

        if 0 < connection_quality <= 4:
            icon = f"mdi:wifi-strength-{connection_quality}"

        return icon

    @property
    def entity_picture(self) -> str:
        return None

    def __signal_strength(self) -> int:
        has_connectivity_status = self.pod.connectivity_status is not None
        has_evse = (
            has_connectivity_status
            and self.pod.connectivity_status.evses[0] is not None
        )
        has_connectivity_state = (
            has_evse
            and self.pod.connectivity_status.evses[0].connectivity_state is not None
        )
        has_signal_strength = (
            has_connectivity_state
            and self.pod.connectivity_status.evses[0].connectivity_state.signal_strength
            is not None
        )

        return (
            self.pod.connectivity_status.evses[0].connectivity_state.signal_strength
            if has_signal_strength
            else 0
        )

    def __connection_quality(self) -> int:
        has_connectivity_status = self.pod.connectivity_status is not None
        has_evse = (
            has_connectivity_status
            and self.pod.connectivity_status.evses[0] is not None
        )
        has_connectivity_state = (
            has_evse
            and self.pod.connectivity_status.evses[0].connectivity_state is not None
        )
        has_connection_quality = (
            has_connectivity_state
            and self.pod.connectivity_status.evses[
                0
            ].connectivity_state.connection_quality
            is not None
        )

        return (
            self.pod.connectivity_status.evses[0].connectivity_state.connection_quality
            if has_connection_quality
            else 0
        )


class PodPointLastMessageReceivedSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point Last Message Received sensor class."""

    _attr_translation_key = "last_message_received"
    _attr_has_entity_name = True
    _attr_name = "Last Message Received"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry: ConfigEntry, idx: int):
        super().__init__(coordinator, config_entry=config_entry, idx=idx)
        self.__update_attrs()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    def __update_attrs(self):
        attrs = {
            "attribution": ATTRIBUTION,
            "integration": DOMAIN,
            "last_message_received": self.pod.last_message_at,
        }

        self.extra_attrs = attrs

    @property
    def unique_id(self):
        return f"{super().unique_id}_last_message_at"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return self.extra_attrs

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["last_message_received"]

    @property
    def icon(self):
        return "mdi:message-text-clock"

    @property
    def entity_picture(self) -> str:
        return None


class PodPointTotalEnergySensor(PodPointSensor):
    """pod_point total energy Sensor class."""

    # Override the options from PodPointSensor, prevents an error as this sensor is an 'energy' type
    _attr_options = None
    _attr_translation_key = None
    _attr_has_entity_name = True
    _attr_name = "Total Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, config_entry: ConfigEntry, idx: int):
        super().__init__(coordinator, config_entry=config_entry, idx=idx)
        self.previous_total = self.pod.total_kwh
        self.total_kwh_diff = self.previous_total

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    def __update_attrs(self):
        pod: Pod = self.pod

        new_total = self.pod.total_kwh
        self.total_kwh_diff = new_total - self.previous_total
        self.previous_total = new_total

        attrs = {
            "attribution": ATTRIBUTION,
            "id": pod.id,
            "integration": DOMAIN,
            "suggested_area": "Outside",
            "total_kwh": pod.total_kwh,
            "total_kwh_difference": self.total_kwh_diff,
            "current_kwh": pod.current_kwh,
        }

        self.extra_attrs = attrs

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        return self.extra_attrs

    @property
    def unique_id(self):
        return f"{super().unique_id}_total_energy"

    @property
    def native_value(self) -> float:
        return self.pod.total_kwh

    @property
    def icon(self):
        icon = "mdi:lightning-bolt-outline"

        if self.connected:
            icon = "mdi:lightning-bolt"

        return icon

    @property
    def entity_picture(self) -> str:
        return None

    @property
    def is_on(self) -> bool:
        """This sensor is on when the given pod is connected to a vehicle"""
        return self.connected


class PodPointCurrentEnergySensor(PodPointTotalEnergySensor):
    """pod_point current charge energy Sensor class."""

    _attr_has_entity_name = True
    _attr_name = "Current Energy"
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def unique_id(self):
        return f"{super().unique_id}_current_charge_energy"

    @property
    def native_value(self) -> float:
        return self.pod.current_kwh

    @property
    def last_reset(self) -> datetime:
        if len(self.pod.charges) <= 0:
            return datetime.now(tz=timezone.utc)

        # Get the most recent charge
        charge = self.pod.charges[0]
        return charge.starts_at - timedelta(seconds=10)

    @property
    def icon(self):
        icon = "mdi:car"

        if self.connected:
            icon = "mdi:car-electric"

        return icon


class PodPointChargeModeEntity(
    PodPointEntity,
    SensorEntity,
):
    """pod_point charge mode sensor class."""

    _attr_options = [ChargeMode.MANUAL, ChargeMode.SMART, ChargeMode.OVERRIDE]
    _attr_has_entity_name = True
    _attr_name = "Charge Mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:car-clock"

    @property
    def unique_id(self):
        return f"{super().unique_id}_charge_mode"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        charge_override = None
        if self.pod.charge_override is not None:
            charge_override = self.pod.charge_override.dict

        return {"charge_override": charge_override}

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.pod.charge_mode

    @property
    def entity_picture(self) -> str:
        return None


class PodPointChargeOverrideEntity(
    PodPointEntity,
    SensorEntity,
):
    """pod_point charge mode sensor class."""

    _attr_has_entity_name = True
    _attr_name = "Charge Override End Time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:battery-clock"

    @property
    def unique_id(self):
        return f"{super().unique_id}_override_end_time"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        charge_override = None
        if self.pod.charge_override is not None:
            charge_override = self.pod.charge_override.dict

        return {"charge_override": charge_override}

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        value = None
        override: ChargeOverride = self.pod.charge_override

        if override is not None:
            value = override.ends_at

        return value

    @property
    def entity_picture(self) -> str:
        return None


class PodPointTotalCostSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point total cost sensor class."""

    _attr_has_entity_name = True
    _attr_name = "Total Cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-multiple"

    @property
    def unique_id(self):
        return f"{super().unique_id}_total_cost"

    @property
    def currency(self) -> str:
        """Which currency type are we returning?"""

        # TODO - Should use the default currency from HA here
        try:
            currency = self.config_entry.options[CONF_CURRENCY]
        except KeyError:
            currency = DEFAULT_CURRENCY

        return currency

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        cost_as_pounds = self.pod.total_cost / 100

        return {
            "raw": self.pod.total_cost,
            "amount": cost_as_pounds,
            "currency": self.currency,
            "formatted": f"{cost_as_pounds} {self.currency}",
        }

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["amount"]

    @property
    def native_unit_of_measurement(self):
        """Return the unit for this sensor."""
        return self.extra_state_attributes["currency"]

    @property
    def entity_picture(self) -> str:
        return None


class PodPointLastCompleteChargeCostSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point cost of last complete charge sensor class."""

    _attr_has_entity_name = True
    _attr_name = "Last Completed Charge Cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash"

    @property
    def unique_id(self):
        return f"{super().unique_id}_last_complete_charge_cost"

    @property
    def currency(self) -> str:
        """Which currency type are we returning?"""

        try:
            currency = self.config_entry.options[CONF_CURRENCY]
        except KeyError:
            currency = DEFAULT_CURRENCY

        return currency

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        raw = 0
        cost_as_pounds = 0.0

        if getattr(self.pod, "last_charge_cost", None) is not None:
            raw = getattr(self.pod, "last_charge_cost", None)
            cost_as_pounds = raw / 100

        return {
            "raw": raw,
            "amount": cost_as_pounds,
            "currency": self.currency,
            "formatted": f"{cost_as_pounds} {self.currency}",
        }

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["amount"]

    @property
    def native_unit_of_measurement(self):
        """Return the unit for this sensor."""
        return self.extra_state_attributes["currency"]

    @property
    def entity_picture(self) -> str:
        return None


class PodPointHomeAppVehicleSensor(PodPointEntity, SensorEntity):
    """Pod Point Home App vehicle battery sensor."""

    _attr_has_entity_name = True
    _attr_name = "Vehicle Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:car-electric"

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        idx: int,
        vehicle_idx: int,
    ):
        self.vehicle_idx = vehicle_idx
        super().__init__(coordinator, config_entry=config_entry, idx=idx)

    @property
    def unique_id(self):
        vehicle_id = self.vehicle_assignment.get("id") or _get_nested(
            self.vehicle_assignment, "vehicle", "id"
        )

        return f"{super().unique_id}_vehicle_{vehicle_id}_battery"

    @property
    def native_value(self):
        """Return the vehicle battery percentage."""
        return _get_nested(
            self.vehicle_assignment,
            "vehicle",
            "chargeState",
            "batteryLevelPercent",
        )

    @property
    def name(self):
        display_name = self.vehicle_display_name

        if display_name:
            return f"{display_name} Battery"

        return f"Vehicle {self.vehicle_idx + 1} Battery"

    @property
    def vehicle_assignment(self) -> Dict[str, Any]:
        vehicles = self.coordinator.home_app_vehicles_by_ppid.get(self.pod.ppid, [])

        if self.vehicle_idx >= len(vehicles):
            return {}

        vehicle = vehicles[self.vehicle_idx]

        if not isinstance(vehicle, dict):
            return {}

        return vehicle

    @property
    def vehicle_display_name(self) -> str:
        vehicle_information = _get_nested(
            self.vehicle_assignment, "vehicle", "vehicleInformation"
        )

        if not isinstance(vehicle_information, dict):
            return None

        display_name = vehicle_information.get("displayName")
        if display_name:
            return display_name

        return " ".join(
            filter(
                None,
                [
                    vehicle_information.get("brand"),
                    vehicle_information.get("model"),
                    vehicle_information.get("modelVariant"),
                ],
            )
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        vehicle = self.vehicle_assignment.get("vehicle", {})
        vehicle_information = vehicle.get("vehicleInformation", {})
        charge_state = vehicle.get("chargeState", {})

        return {
            "attribution": ATTRIBUTION,
            "integration": DOMAIN,
            "ppid": self.pod.ppid,
            "vehicle_assignment_id": self.vehicle_assignment.get("id"),
            "vehicle_id": vehicle.get("id"),
            "enode_user_id": vehicle.get("enodeUserId"),
            "enode_vehicle_id": vehicle.get("enodeVehicleId"),
            "is_primary": self.vehicle_assignment.get("isPrimary"),
            "is_plugged_in_to_this_charger": self.vehicle_assignment.get(
                "isPluggedInToThisCharger"
            ),
            "brand": vehicle_information.get("brand"),
            "model": vehicle_information.get("model"),
            "model_variant": vehicle_information.get("modelVariant"),
            "registration_plate": vehicle_information.get(
                "vehicleRegistrationPlate"
            ),
            "display_name": self.vehicle_display_name,
            "last_seen": vehicle.get("lastSeen"),
            "battery_capacity": charge_state.get("batteryCapacity"),
            "charge_limit_percent": charge_state.get("chargeLimitPercent"),
            "charge_limit_source": charge_state.get("chargeLimitSource"),
            "charge_rate": charge_state.get("chargeRate"),
            "charge_time_remaining": charge_state.get("chargeTimeRemaining"),
            "is_charging": charge_state.get("isCharging"),
            "is_fully_charged": charge_state.get("isFullyCharged"),
            "is_plugged_in": charge_state.get("isPluggedIn"),
            "range": charge_state.get("range"),
            "power_delivery_state": charge_state.get("powerDeliveryState"),
            "charge_state_last_updated": charge_state.get("lastUpdated"),
            "intents": self.vehicle_assignment.get("intents"),
            "current_intent": self.vehicle_assignment.get("currentIntent"),
            "raw": self.vehicle_assignment,
            "delegated_controls": self.coordinator.home_app_delegated_controls_by_ppid.get(
                self.pod.ppid
            ),
            "charge_overrides": self.coordinator.home_app_charge_overrides_by_ppid.get(
                self.pod.ppid
            ),
            "tariffs": self.coordinator.home_app_tariffs_by_ppid.get(self.pod.ppid),
            "remote_lock": self.coordinator.home_app_remote_locks_by_ppid.get(
                self.pod.ppid
            ),
            "preferences": self.coordinator.home_app_preferences_by_ppid.get(
                self.pod.ppid
            ),
        }

    @property
    def entity_picture(self) -> str:
        return None


class PodPointAccountBalanceEntity(CoordinatorEntity, SensorEntity):
    """Pod Point Balance Entity"""

    _attr_translation_key = "account_balance"
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_name = "Pod Point Balance"
    _attr_icon = "mdi:account-cash"
    _attr_available = False

    @property
    def native_value(self):
        """Return the value of the balance sensor"""
        return self.balance

    @property
    def native_unit_of_measurement(self):
        """Return the unit for this sensor."""
        return self.user.account.currency

    def __update_attrs(self):
        if self.available is False:
            return

        user: User = self.user

        attrs = {"attribution": ATTRIBUTION, "uuid": self.uuid, "integration": DOMAIN}

        attrs.update(user.dict)
        self._attr_state = self.balance
        self._attr_extra_state_attributes = attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    @property
    def user(self) -> User:
        """Return the underlying pod that drives this entity"""
        user: User = self.coordinator.user
        return user

    @property
    def uuid(self) -> str:
        """Return the user uuid"""
        return self.user.account.uid

    @property
    def balance(self) -> float:
        """Return a balance float"""
        raw_balance = self.user.account.balance

        if raw_balance is None or raw_balance <= 0:
            return 0.0

        return raw_balance / 100

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self.uuid

    @property
    def available(self) -> bool:
        typed_coordinator: PodPointDataUpdateCoordinator = self.coordinator
        return typed_coordinator.online is True


class PodPointRewardWalletEntity(CoordinatorEntity, SensorEntity):
    """Pod Point Home App reward wallet sensor."""

    _attr_has_entity_name = True
    _attr_name = "Reward Wallet"
    _attr_icon = "mdi:wallet-giftcard"

    @property
    def native_value(self):
        """Return the first useful scalar from the reward wallet payload."""
        wallet = self.wallet

        if not isinstance(wallet, dict):
            return None

        for path in (
            ("rewards", "balanceGbp"),
            ("allowance", "balanceGbp"),
            ("payments", "totalWithdrawnGbp"),
        ):
            value = _get_nested(wallet, *path)
            if isinstance(value, (int, float, str)):
                return value

        for key in (
            "balance",
            "availableBalance",
            "rewardBalance",
            "amount",
            "value",
            "points",
        ):
            value = wallet.get(key)
            if isinstance(value, (int, float, str)):
                return value

        return None

    @property
    def native_unit_of_measurement(self):
        """Return reward wallet units, if the API provides one."""
        wallet = self.wallet

        if not isinstance(wallet, dict):
            return None

        if _get_nested(wallet, "rewards", "balanceGbp") is not None:
            return DEFAULT_CURRENCY

        return wallet.get("currency") or wallet.get("unit")

    @property
    def wallet(self):
        """Return the cached reward wallet payload."""
        return self.coordinator.home_app_reward_wallet

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        user = self.coordinator.user

        if user is not None and user.account is not None:
            return f"{user.account.uid}_reward_wallet"

        return "pod_point_reward_wallet"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs = {"attribution": ATTRIBUTION, "integration": DOMAIN}

        if isinstance(self.wallet, dict):
            attrs.update(self.wallet)
        else:
            attrs["raw"] = self.wallet

        return attrs

    @property
    def available(self) -> bool:
        typed_coordinator: PodPointDataUpdateCoordinator = self.coordinator
        return (
            typed_coordinator.online is True
            and typed_coordinator.home_app_reward_wallet is not None
        )
