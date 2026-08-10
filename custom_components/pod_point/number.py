"""Number controls for Pod Point chargers."""

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import UnitOfEnergy, UnitOfTime

from .const import (
    CONF_CURRENCY,
    DEFAULT_CHARGE_NOW_DURATION,
    DEFAULT_CURRENCY,
)
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Pod Home number controls."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_entities: set[tuple[str, str]] = set()

    def _add_new_entities() -> None:
        entities = []
        for index, charger in enumerate(coordinator.data):
            candidates = [("charge_now_duration", PodPointChargeNowDurationNumber)]
            if coordinator.smart_charging_preferences.get(charger.ppid) is not None:
                candidates.append(
                    ("smart_charging_max_price", PodPointSmartChargingMaxPriceNumber)
                )

            for key, entity_type in candidates:
                entity_key = (charger.ppid, key)
                if entity_key not in known_entities:
                    known_entities.add(entity_key)
                    entities.append(entity_type(coordinator, entry, index))
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PodPointChargeNowDurationNumber(PodPointEntity, RestoreNumber):
    """Duration to use the next time Charge now is enabled."""

    _attr_has_entity_name = True
    _attr_name = "Charge now duration"
    _attr_translation_key = "charge_now_duration"
    _attr_icon = "mdi:timer-cog-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, config_entry, idx: int):
        super().__init__(coordinator, config_entry, idx)
        self._attr_native_value = DEFAULT_CHARGE_NOW_DURATION
        coordinator.charge_now_durations.setdefault(
            self.charger.ppid, DEFAULT_CHARGE_NOW_DURATION
        )

    @property
    def unique_id(self):
        return f"{super().unique_id}_charge_now_duration"

    @property
    def available(self) -> bool:
        return super().available and self.charge_now_available

    async def async_added_to_hass(self) -> None:
        """Restore the user's preferred duration."""
        await super().async_added_to_hass()
        if (
            last_data := await self.async_get_last_number_data()
        ) is not None and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        self.coordinator.charge_now_durations[self.charger.ppid] = int(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        """Store the duration without changing an active override."""
        self._attr_native_value = value
        self.coordinator.charge_now_durations[self.charger.ppid] = int(value)
        self.async_write_ha_state()


class PodPointSmartChargingMaxPriceNumber(PodPointEntity, NumberEntity):
    """Maximum unit price Pod Home may use when smart charging."""

    _attr_has_entity_name = True
    _attr_name = "Smart charging maximum price"
    _attr_icon = "mdi:cash-edit"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 5
    _attr_native_step = 0.0001

    @property
    def unique_id(self):
        return f"{super().unique_id}_smart_charging_max_price_control"

    @property
    def native_unit_of_measurement(self):
        currency = self.config_entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY)
        return f"{currency}/{UnitOfEnergy.KILO_WATT_HOUR}"

    @property
    def native_value(self):
        preferences = self.coordinator.smart_charging_preferences.get(self.charger.ppid)
        return preferences.max_price if preferences is not None else None

    @property
    def available(self) -> bool:
        return super().available and self.smart_charging_active

    async def async_set_native_value(self, value: float) -> None:
        charger = self.charger
        await self.coordinator.async_set_smart_charging_max_price(charger, value)
