"""Select controls for Pod Home smart-charging preferences."""

from math import isclose

from homeassistant.components.select import SelectEntity

from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

PRIORITISE_COMPLETE_CHARGE = "Prioritise a complete charge"
PRIORITISE_LOWEST_COST = "Prioritise lowest cost"
BASIC_MODE_SCHEDULED = "Scheduled"
BASIC_MODE_ALWAYS_ON = "Always on"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Pod Home smart-charging selectors."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_entities: set[tuple[str, str]] = set()

    def _add_new_entities() -> None:
        entities = []
        for index, pod in enumerate(coordinator.data):
            candidates = []
            if pod.ppid in coordinator.chargers:
                candidates.append(("basic_mode", PodPointBasicChargingModeSelect))
            if coordinator.smart_charging_preferences.get(
                pod.ppid
            ) is not None and _tariff_prices(coordinator, pod.ppid):
                candidates.append(
                    ("smart_priority", PodPointSmartChargingPrioritySelect)
                )

            for key, entity_type in candidates:
                entity_key = (pod.ppid, key)
                if entity_key not in known_entities:
                    known_entities.add(entity_key)
                    entities.append(entity_type(coordinator, entry, index))

        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


def _tariff_prices(
    coordinator: PodPointDataUpdateCoordinator, ppid: str
) -> list[float]:
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

    @property
    def available(self) -> bool:
        return super().available and self.smart_charging_active

    async def async_select_option(self, option: str) -> None:
        prices = _tariff_prices(self.coordinator, self.pod.ppid)
        price = min(prices) if option == PRIORITISE_LOWEST_COST else max(prices)
        charger = self.coordinator.chargers[self.pod.ppid]
        await self.coordinator.async_set_smart_charging_max_price(charger, price)


class PodPointBasicChargingModeSelect(PodPointEntity, SelectEntity):
    """Select the charger-centric basic charging mode."""

    _attr_has_entity_name = True
    _attr_name = "Basic charging mode"
    _attr_icon = "mdi:calendar-clock"
    _attr_options = [BASIC_MODE_SCHEDULED, BASIC_MODE_ALWAYS_ON]

    @property
    def unique_id(self):
        return f"{super().unique_id}_basic_charging_mode"

    @property
    def current_option(self):
        overrides = self.coordinator.charge_overrides.get(self.pod.ppid)
        if overrides is None:
            return None
        if any(override.end_at is not None for override in overrides):
            # A timed boost is neither of the persistent basic modes.
            return None
        if any(override.end_at is None for override in overrides):
            return BASIC_MODE_ALWAYS_ON
        return BASIC_MODE_SCHEDULED

    @property
    def available(self) -> bool:
        overrides = self.coordinator.charge_overrides.get(self.pod.ppid)
        return (
            super().available
            and getattr(
                self.coordinator.chargers.get(self.pod.ppid),
                "delegated_control_status",
                None,
            )
            is not None
            and not self.smart_charging_active
            and overrides is not None
            and not any(override.end_at is not None for override in overrides)
        )

    async def async_select_option(self, option: str) -> None:
        charger = self.coordinator.chargers[self.pod.ppid]
        if option == BASIC_MODE_ALWAYS_ON:
            result = await self.coordinator.async_api_call(
                self.coordinator.api.async_set_charger_charge_mode_always_on(charger)
            )
            succeeded = result is not None
        else:
            succeeded = await self.coordinator.async_api_call(
                self.coordinator.api.async_set_charger_charge_mode_scheduled(charger)
            )
        if succeeded:
            await self.coordinator.async_request_refresh()
