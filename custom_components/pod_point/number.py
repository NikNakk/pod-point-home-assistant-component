"""Number controls for Pod Home smart charging."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfEnergy

from .const import CONF_CURRENCY, DEFAULT_CURRENCY
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Pod Home number controls."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_pods: set[str] = set()

    def _add_new_entities() -> None:
        entities = []
        for index, pod in enumerate(coordinator.data):
            if (
                pod.ppid not in known_pods
                and coordinator.smart_charging_preferences.get(pod.ppid) is not None
            ):
                known_pods.add(pod.ppid)
                entities.append(
                    PodPointSmartChargingMaxPriceNumber(coordinator, entry, index)
                )
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PodPointSmartChargingMaxPriceNumber(PodPointEntity, NumberEntity):
    """Maximum unit price Pod Home may use when smart charging."""

    _attr_has_entity_name = True
    _attr_name = "Smart charging maximum price"
    _attr_icon = "mdi:cash-edit"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 5
    _attr_native_step = 0.01

    @property
    def unique_id(self):
        return f"{super().unique_id}_smart_charging_max_price_control"

    @property
    def native_unit_of_measurement(self):
        currency = self.config_entry.options.get(CONF_CURRENCY, DEFAULT_CURRENCY)
        return f"{currency}/{UnitOfEnergy.KILO_WATT_HOUR}"

    @property
    def native_value(self):
        preferences = self.coordinator.smart_charging_preferences.get(self.pod.ppid)
        return preferences.max_price if preferences is not None else None

    @property
    def available(self) -> bool:
        return super().available and self.smart_charging_active

    async def async_set_native_value(self, value: float) -> None:
        charger = self.coordinator.chargers[self.pod.ppid]
        await self.coordinator.async_api_call(
            self.coordinator.api.async_set_smart_charging_max_price(charger, value)
        )
        await self.coordinator.async_request_refresh()
