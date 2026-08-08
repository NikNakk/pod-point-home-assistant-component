"""Sensor platform for pod_point."""

import logging
from datetime import datetime, timedelta
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS, UnitOfEnergy, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from podpointclient.charge_mode import ChargeMode
from podpointclient.domain import (
    AccountCapability,
    CapabilitySupport,
)
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
    NAME,
)
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


def _boost_attributes(boost) -> dict[str, Any] | None:
    """Return stable attributes from a canonical boost state."""
    if boost is None or not boost.active:
        return None
    return {
        "active": boost.active,
        "timed": boost.timed,
        "requested_at": boost.requested_at,
        "started_at": boost.started_at,
        "ends_at": boost.ends_at,
        "source_id": boost.source_id,
    }


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_entities: set[tuple[str, str]] = set()

    def _add_new_entities() -> None:
        sensors = []
        for index, charger in enumerate(coordinator.data):
            candidates = [
                ("status", PodPointSensor),
                ("charge_time", PodPointChargeTimeSensor),
                ("total_energy", PodPointTotalEnergySensor),
                ("current_energy", PodPointCurrentEnergySensor),
                ("last_message", PodPointLastMessageReceivedSensor),
                ("total_cost", PodPointTotalCostSensor),
                ("last_charge_cost", PodPointLastCompleteChargeCostSensor),
                ("charge_mode", PodPointChargeModeEntity),
                ("charge_override", PodPointChargeOverrideEntity),
            ]
            charger_state = coordinator.charger_states.get(charger.ppid)
            if (
                charger_state is not None
                and charger_state.signal_strength_dbm is not None
            ):
                candidates.insert(4, ("signal_strength", PodPointSignalStrengthSensor))
            if (
                charger_state is not None
                and charger_state.connection_quality is not None
            ):
                candidates.append(
                    ("connection_quality", PodPointConnectionQualitySensor)
                )
            if coordinator.tariffs.get(charger.ppid):
                candidates.append(("cheapest_tariff", PodPointCheapestTariffSensor))
            if coordinator.smart_charging_preferences.get(charger.ppid) is not None:
                candidates.append(
                    ("smart_max_price", PodPointSmartChargingMaxPriceSensor)
                )
            delegated = coordinator.delegated_vehicles.get(charger.ppid)
            if delegated is not None and delegated.vehicles:
                candidates.append(("vehicle_battery", PodPointVehicleBatterySensor))

            for key, entity_type in candidates:
                entity_key = (charger.ppid, key)
                if entity_key not in known_entities:
                    known_entities.add(entity_key)
                    sensors.append(entity_type(coordinator, entry, index))

        account_candidates = [("balance", PodPointAccountBalanceEntity)]
        if coordinator.reward_wallet is not None:
            account_candidates.extend(
                [
                    ("reward_balance", PodPointRewardBalanceSensor),
                    ("allowance_balance", PodPointRewardBalanceSensor),
                    ("reward_points", PodPointRewardPointsSensor),
                ]
            )
        for key, entity_type in account_candidates:
            entity_key = (entry.entry_id, key)
            if entity_key in known_entities:
                continue
            known_entities.add(entity_key)
            if key in {"reward_balance", "allowance_balance"}:
                section = "rewards" if key == "reward_balance" else "allowance"
                sensors.append(entity_type(coordinator, entry, section))
            else:
                sensors.append(entity_type(coordinator, entry))

        if sensors:
            async_add_devices(sensors)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PodPointSensor(
    PodPointEntity,
    SensorEntity,
):
    """pod_point Sensor class."""

    _attr_options: ClassVar[list[str]] = [
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
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "raw": self.metrics.total_charge_seconds,
            "formatted": str(timedelta(seconds=self.metrics.total_charge_seconds)),
            "long": self._td_format(
                timedelta(seconds=self.metrics.total_charge_seconds)
            ),
        }

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["raw"]


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
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.extra_attrs

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["signal_strength"]

    @property
    def available(self) -> bool:
        """Return whether this wire API supplies RSSI diagnostics."""
        state = self.coordinator.charger_states.get(self.charger.ppid)
        return super().available and bool(
            state and state.signal_strength_dbm is not None
        )

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

    def __signal_strength(self) -> int:
        state = self.coordinator.charger_states.get(self.charger.ppid)
        return state.signal_strength_dbm if state and state.signal_strength_dbm else 0

    def __connection_quality(self) -> int:
        state = self.coordinator.charger_states.get(self.charger.ppid)
        diagnostic = state.connection_quality if state is not None else None
        return diagnostic.raw if diagnostic and diagnostic.raw is not None else 0


class PodPointConnectionQualitySensor(PodPointEntity, SensorEntity):
    """Pod Home connection quality level."""

    _attr_has_entity_name = True
    _attr_name = "Connection quality"
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self):
        return f"{super().unique_id}_connection_quality"

    @property
    def native_value(self):
        state = self.coordinator.charger_states.get(self.charger.ppid)
        diagnostic = state.connection_quality if state is not None else None
        return diagnostic.raw if diagnostic is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.charger_states.get(self.charger.ppid)
        diagnostic = state.connection_quality if state is not None else None
        return {"source": diagnostic.source.value} if diagnostic is not None else {}


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
            "last_message_received": self.last_message_at,
        }

        self.extra_attrs = attrs

    @property
    def unique_id(self):
        return f"{super().unique_id}_last_message_at"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.extra_attrs

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["last_message_received"]

    @property
    def icon(self):
        return "mdi:message-text-clock"


class PodPointTotalEnergySensor(PodPointEntity, SensorEntity):
    """pod_point total energy Sensor class."""

    _attr_has_entity_name = True
    _attr_name = "Total Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, config_entry: ConfigEntry, idx: int):
        super().__init__(coordinator, config_entry=config_entry, idx=idx)
        self.previous_total = self.metrics.total_kwh
        self.total_kwh_diff = self.previous_total

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    def __update_attrs(self):
        charger = self.charger
        new_total = self.metrics.total_kwh
        self.total_kwh_diff = new_total - self.previous_total
        self.previous_total = new_total

        attrs = {
            "attribution": ATTRIBUTION,
            "id": charger.unit_id,
            "integration": DOMAIN,
            "suggested_area": "Outside",
            "total_kwh": self.metrics.total_kwh,
            "total_kwh_difference": self.total_kwh_diff,
            "current_kwh": self.metrics.current_kwh,
        }

        self.extra_attrs = attrs

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return self.extra_attrs

    @property
    def unique_id(self):
        # Retain the historical ID created through PodPointSensor inheritance.
        return f"{super().unique_id}_status_total_energy"

    @property
    def native_value(self) -> float:
        return self.metrics.total_kwh

    @property
    def icon(self):
        icon = "mdi:lightning-bolt-outline"

        if self.connected:
            icon = "mdi:lightning-bolt"

        return icon


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
        return self.metrics.current_kwh

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.api.account_capability(
                AccountCapability.LEGACY_CHARGES
            )
            is not CapabilitySupport.UNSUPPORTED
        )

    @property
    def last_reset(self) -> datetime | None:
        return self.metrics.active_started_at

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

    _attr_options: ClassVar[list[ChargeMode]] = [
        ChargeMode.MANUAL,
        ChargeMode.SMART,
        ChargeMode.OVERRIDE,
    ]
    _attr_has_entity_name = True
    _attr_name = "Charge Mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:car-clock"

    @property
    def unique_id(self):
        return f"{super().unique_id}_charge_mode"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        boost = self.coordinator.boost_states.get(self.charger.ppid)
        return {"charge_override": _boost_attributes(boost)}

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.charge_mode


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
    def extra_state_attributes(self) -> dict[str, Any]:
        boost = self.coordinator.boost_states.get(self.charger.ppid)
        return {"charge_override": _boost_attributes(boost)}

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        boost = self.coordinator.boost_states.get(self.charger.ppid)
        return boost.ends_at if boost and boost.active and boost.timed else None


class PodPointCostSensor(PodPointEntity, SensorEntity):
    """Base class for charge-cost sensors measured in minor currency units."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_has_entity_name = True

    @property
    def currency(self) -> str:
        """Return the charge currency, falling back to the configured currency."""
        if currency := self.metrics.charge_currency:
            return currency
        return self.config_entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY)

    @property
    def cost_minor_units(self) -> float | None:
        """Return the cost in the API's minor currency units."""
        raise NotImplementedError

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self.cost_minor_units
        if raw is None:
            raw = 0
        amount = raw / 100
        currency = self.currency

        return {
            "raw": raw,
            "amount": amount,
            "currency": currency,
            "formatted": f"{amount} {currency}",
        }

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.extra_state_attributes["amount"]

    @property
    def native_unit_of_measurement(self):
        """Return the unit for this sensor."""
        return self.currency


class PodPointTotalCostSensor(PodPointCostSensor):
    """Total cost of completed charges."""

    _attr_name = "Total Cost"
    _attr_icon = "mdi:cash-multiple"

    @property
    def unique_id(self):
        return f"{super().unique_id}_total_cost"

    @property
    def cost_minor_units(self) -> float:
        return self.metrics.total_cost


class PodPointLastCompleteChargeCostSensor(PodPointCostSensor):
    """pod_point cost of last complete charge sensor class."""

    _attr_name = "Last Completed Charge Cost"
    _attr_icon = "mdi:cash"

    @property
    def unique_id(self):
        return f"{super().unique_id}_last_complete_charge_cost"

    @property
    def cost_minor_units(self) -> float | None:
        return self.metrics.last_charge_cost


class PodPointAccountBalanceEntity(CoordinatorEntity, SensorEntity):
    """Pod Point Balance Entity"""

    _attr_translation_key = "account_balance"
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_name = "Pod Point Balance"
    _attr_icon = "mdi:account-cash"
    _attr_available = False

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self.config_entry = config_entry

    @property
    def device_info(self):
        return _account_device_info(self.config_entry)

    @property
    def native_value(self):
        """Return the value of the balance sensor"""
        return self.balance

    @property
    def native_unit_of_measurement(self):
        """Return the unit for this sensor."""
        account = getattr(self.user, "account", None)
        return getattr(account, "currency", None)

    def __update_attrs(self):
        if self.available is False:
            return

        attrs = {"attribution": ATTRIBUTION, "integration": DOMAIN}
        self._attr_state = self.balance
        self._attr_extra_state_attributes = attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.__update_attrs()
        self.async_write_ha_state()

    @property
    def user(self) -> User:
        """Return the account user that drives this entity."""
        user: User = self.coordinator.user
        return user

    @property
    def uuid(self) -> str:
        """Return the user uuid"""
        account = getattr(self.user, "account", None)
        return getattr(account, "uid", None)

    @property
    def balance(self) -> float:
        """Return a balance float"""
        account = getattr(self.user, "account", None)
        raw_balance = getattr(account, "balance", None)

        if raw_balance is None or raw_balance <= 0:
            return 0.0

        return raw_balance / 100

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{DOMAIN}_{self.config_entry.entry_id}_account_balance"

    @property
    def available(self) -> bool:
        typed_coordinator: PodPointDataUpdateCoordinator = self.coordinator
        return (
            typed_coordinator.online is True
            and getattr(typed_coordinator.user, "account", None) is not None
        )


class PodPointRewardBalanceSensor(CoordinatorEntity, SensorEntity):
    """Reward wallet cash balance."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:wallet-giftcard"

    def __init__(self, coordinator, config_entry, section: str):
        super().__init__(coordinator)
        self.config_entry = config_entry
        self.section = section
        self._attr_name = (
            "Reward balance" if section == "rewards" else "Reward allowance"
        )

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self.config_entry.entry_id}_reward_{self.section}_balance"

    @property
    def device_info(self):
        return _account_device_info(self.config_entry)

    @property
    def native_value(self):
        data = getattr(self.coordinator.reward_wallet, self.section, {})
        return data.get("balanceGbp")

    @property
    def native_unit_of_measurement(self):
        return self.config_entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY)

    @property
    def extra_state_attributes(self):
        return getattr(self.coordinator.reward_wallet, self.section, {})

    @property
    def available(self) -> bool:
        return (
            self.coordinator.online is True
            and self.coordinator.reward_wallet is not None
        )


class PodPointRewardPointsSensor(CoordinatorEntity, SensorEntity):
    """Reward points balance."""

    _attr_has_entity_name = True
    _attr_name = "Reward points"
    _attr_icon = "mdi:star-circle"

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self.config_entry = config_entry

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self.config_entry.entry_id}_reward_points"

    @property
    def device_info(self):
        return _account_device_info(self.config_entry)

    @property
    def native_value(self):
        wallet = self.coordinator.reward_wallet
        return wallet.rewards.get("balancePoints") if wallet is not None else None

    @property
    def extra_state_attributes(self):
        wallet = self.coordinator.reward_wallet
        return wallet.payments if wallet is not None else {}

    @property
    def available(self) -> bool:
        return (
            self.coordinator.online is True
            and self.coordinator.reward_wallet is not None
        )


def _account_device_info(config_entry):
    """Return the shared virtual device for account-level Pod Point entities."""
    return {
        "identifiers": {(DOMAIN, f"account_{config_entry.entry_id}")},
        "name": "Pod Point Account",
        "manufacturer": NAME,
        "model": "Pod Home account",
    }


class PodPointCheapestTariffSensor(PodPointEntity, SensorEntity):
    """Cheapest configured Pod Home tariff rate."""

    _attr_has_entity_name = True
    _attr_name = "Cheapest tariff"
    _attr_icon = "mdi:cash-clock"

    @property
    def unique_id(self):
        return f"{super().unique_id}_cheapest_tariff"

    @property
    def native_value(self):
        prices = [
            tariff.cheapest_unit_price
            for tariff in self.coordinator.tariffs.get(self.charger.ppid, [])
            if tariff.cheapest_unit_price is not None
        ]
        return min(prices) if prices else None

    @property
    def native_unit_of_measurement(self):
        currency = self.config_entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY)
        return f"{currency}/kWh"


class PodPointSmartChargingMaxPriceSensor(PodPointEntity, SensorEntity):
    """Maximum price used by Pod Home smart charging."""

    _attr_has_entity_name = True
    _attr_name = "Smart charging maximum price"
    _attr_icon = "mdi:ev-station"

    @property
    def unique_id(self):
        return f"{super().unique_id}_smart_charging_max_price"

    @property
    def native_value(self):
        preferences = self.coordinator.smart_charging_preferences.get(self.charger.ppid)
        return preferences.max_price if preferences is not None else None

    @property
    def available(self) -> bool:
        return super().available and self.smart_charging_active

    @property
    def native_unit_of_measurement(self):
        currency = self.config_entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY)
        return f"{currency}/kWh"


class PodPointVehicleBatterySensor(PodPointEntity, SensorEntity):
    """Battery level supplied by delegated smart charging."""

    _attr_has_entity_name = True
    _attr_name = "Vehicle battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self):
        return f"{super().unique_id}_vehicle_battery"

    @property
    def vehicle_link(self):
        delegated = self.coordinator.delegated_vehicles.get(self.charger.ppid)
        if delegated is None or not delegated.vehicles:
            return None
        return next(
            (item for item in delegated.vehicles if item.is_primary),
            delegated.vehicles[0],
        )

    @property
    def native_value(self):
        link = self.vehicle_link
        return link.vehicle.charge_state.battery_level_percent if link else None

    @property
    def extra_state_attributes(self):
        link = self.vehicle_link
        return link.dict if link else {}
