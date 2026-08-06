"""Select controls for Pod Home smart-charging preferences."""

from math import isclose

from homeassistant.components.select import SelectEntity

from .const import DOMAIN
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

PRIORITISE_COMPLETE_CHARGE = "Prioritise a complete charge"
PRIORITISE_LOWEST_COST = "Prioritise lowest cost"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Pod Home smart-charging selectors."""
    coordinator: PodPointDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for index, pod in enumerate(coordinator.data):
        if (
            coordinator.smart_charging_preferences.get(pod.ppid) is not None
            and _tariff_prices(coordinator, pod.ppid)
        ):
            entities.append(PodPointSmartChargingPrioritySelect(coordinator, entry, index))
    async_add_entities(entities)


def _tariff_prices(coordinator: PodPointDataUpdateCoordinator, ppid: str) -> list[float]:
    """Return every configured tariff-period price for a charger."""
    return [
        period.price
        for tariff in coordinator.tariffs.get(ppid, [])
        for period in tariff.tariff_info
        if period.price is not None
    ]


class PodPointSmartChargingPrioritySelect(PodPointEntity, SelectEntity):
    """Choose whether Pod Home prioritises completion or lowest cost."""

    _attr_has_entity_name = True
    _attr_name = "Smart charging priority"
    _attr_icon = "mdi:car-clock"
    _attr_options = [PRIORITISE_COMPLETE_CHARGE, PRIORITISE_LOWEST_COST]

    @property
    def unique_id(self):
        return f"{super().unique_id}_smart_charging_priority"

    @property
    def current_option(self):
        preferences = self.coordinator.smart_charging_preferences.get(self.pod.ppid)
        prices = _tariff_prices(self.coordinator, self.pod.ppid)
        if preferences is None or preferences.max_price is None or not prices:
            return None
        if isclose(preferences.max_price, min(prices), rel_tol=1e-6, abs_tol=1e-6):
            return PRIORITISE_LOWEST_COST
        if isclose(preferences.max_price, max(prices), rel_tol=1e-6, abs_tol=1e-6):
            return PRIORITISE_COMPLETE_CHARGE
        return None

    async def async_select_option(self, option: str) -> None:
        prices = _tariff_prices(self.coordinator, self.pod.ppid)
        price = min(prices) if option == PRIORITISE_LOWEST_COST else max(prices)
        charger = self.coordinator.chargers[self.pod.ppid]
        await self.coordinator.api.async_set_smart_charging_max_price(charger, price)
        await self.coordinator.async_request_refresh()
