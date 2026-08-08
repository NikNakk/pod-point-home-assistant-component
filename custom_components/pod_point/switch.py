"""Switch platform for pod_point."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import ServiceValidationError
from podpointclient.client import PodPointClient
from podpointclient.domain import CapabilitySupport, ChargerCapability
from podpointclient.errors import RequestValidationError

from .const import DEFAULT_CHARGE_NOW_DURATION, SWITCH_ICON
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity
from .services import async_start_charge_now, async_stop_charge_now

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_entities: set[tuple[str, str]] = set()

    def _add_new_entities() -> None:
        switches = []
        for index, pod in enumerate(coordinator.data):
            candidates = [("charge_now", PodPointChargeNowSwitch)]
            if (
                pod.capability(ChargerCapability.BASIC_CHARGING_MODE)
                is not CapabilitySupport.UNSUPPORTED
            ):
                # Preserve the legacy smart-mode entity unique ID while moving its
                # implementation to the charger-centric delegated-control endpoint.
                candidates.append(("charge_mode", PodPointChargeModeSwitch))
            else:
                candidates.append(("charging_allowed", PodPointChargingAllowedSwitch))

            for key, entity_type in candidates:
                entity_key = (pod.ppid, key)
                if entity_key not in known_entities:
                    known_entities.add(entity_key)
                    switches.append(entity_type(coordinator, entry, index))
        if switches:
            async_add_devices(switches)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PodPointChargeNowSwitch(PodPointEntity, SwitchEntity):
    """Start or stop a timed charge override."""

    _attr_has_entity_name = True
    _attr_name = "Charge now"
    _attr_translation_key = "charge_now"
    _attr_icon = "mdi:battery-charging"

    @property
    def unique_id(self):
        return f"{super().unique_id}_charge_now"

    @property
    def is_on(self):
        return self.timed_charge_override_active

    @property
    def available(self) -> bool:
        return super().available and self.charge_now_available

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Start a timed override using the saved duration."""
        if self.is_on:
            return
        duration = self.coordinator.charge_now_durations.get(
            self.charger.ppid, DEFAULT_CHARGE_NOW_DURATION
        )
        hours, minutes = divmod(duration, 60)
        try:
            await self.coordinator.async_api_call(
                async_start_charge_now(
                    self.coordinator, self.charger, hours=hours, minutes=minutes
                )
            )
        except RequestValidationError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Stop the active timed override."""
        if not self.is_on:
            return
        try:
            await self.coordinator.async_api_call(
                async_stop_charge_now(self.coordinator, self.charger)
            )
        except RequestValidationError as err:
            raise ServiceValidationError(str(err)) from err


class PodPointChargingAllowedSwitch(PodPointEntity, SwitchEntity):
    """pod_point switch class."""

    _attr_has_entity_name = True
    _attr_name = "Charging Allowed"
    _attr_icon = SWITCH_ICON

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Allow charging (clear schedule)"""
        api: PodPointClient = self.coordinator.api
        await self.coordinator.async_api_call(
            api.async_set_charger_legacy_schedule(self.charger, False)
        )

        self.coordinator.mark_charger_pending(self.charger)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Block charging (turn on schedule). Unless an override or charge mode would prevent this functionality"""
        api: PodPointClient = self.coordinator.api

        # Exit early if the pod cannot be switched off due to charge mode or override
        if self._override_to_on():
            return False

        await self.coordinator.async_api_call(
            api.async_set_charger_legacy_schedule(self.charger, True)
        )

        self.coordinator.mark_charger_pending(self.charger)
        await self.coordinator.async_request_refresh()

    @property
    def unique_id(self):
        return f"{super().unique_id}_charging_allowed"

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self.charging_allowed

    @property
    def available(self) -> bool:
        if self._override_to_on():
            return False

        return super().available

    def _override_to_on(self):
        boost = self.coordinator.boost_states.get(self.charger.ppid)
        return bool(boost and boost.active)


class PodPointChargeModeSwitch(PodPointEntity, SwitchEntity):
    """Charger-centric delegated smart-charging switch."""

    _attr_name = "Smart Charge Mode"
    _attr_icon = "mdi:cog"
    _attr_has_entity_name = True

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Enable delegated smart charging."""
        api: PodPointClient = self.coordinator.api
        charger = self.charger
        if await self.coordinator.async_api_call(
            api.async_set_domain_smart_charging(charger, True)
        ):
            self.coordinator.mark_charger_pending(charger)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Disable delegated smart charging."""
        api: PodPointClient = self.coordinator.api
        charger = self.charger
        if await self.coordinator.async_api_call(
            api.async_set_domain_smart_charging(charger, False)
        ):
            self.coordinator.mark_charger_pending(charger)
            await self.coordinator.async_request_refresh()

    @property
    def unique_id(self):
        return f"{super().unique_id}_smart_charge_mode"

    @property
    def is_on(self):
        return self.smart_charging_active

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.smart_charging_states.get(self.charger.ppid)
            is not None
        )
