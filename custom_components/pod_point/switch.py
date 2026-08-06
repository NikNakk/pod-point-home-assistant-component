"""Switch platform for pod_point."""

from datetime import datetime
import logging

from homeassistant.components.switch import SwitchEntity
from podpointclient.charge_mode import ChargeMode
from podpointclient.client import PodPointClient
import pytz

from .const import DOMAIN, SWITCH_ICON
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Handle coordinator offline on boot - no data will be populated
    if coordinator.online is False:
        return

    switches = []

    for i in range(len(coordinator.data)):
        if coordinator.data[i].ppid in coordinator.chargers:
            # Preserve the legacy smart-mode entity unique ID while moving its
            # implementation to the charger-centric delegated-control endpoint.
            switches.append(PodPointChargeModeSwitch(coordinator, entry, i))
            continue

        charging_allowed_switch = PodPointChargingAllowedSwitch(coordinator, entry, i)
        switches.append(charging_allowed_switch)
    async_add_devices(switches)


class PodPointChargingAllowedSwitch(PodPointEntity, SwitchEntity):
    """pod_point switch class."""

    _attr_has_entity_name = True
    _attr_name = "Charging Allowed"
    _attr_icon = SWITCH_ICON

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Allow charging (clear schedule)"""
        api: PodPointClient = self.coordinator.api
        await api.async_set_schedule(enabled=False, pod=self.pod)

        self.coordinator.last_message_at = datetime.now(pytz.UTC)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Block charging (turn on schedule). Unless an override or charge mode would prevent this functionality"""
        api: PodPointClient = self.coordinator.api

        # Exit early if the pod cannot be switched off due to charge mode or override
        if self._override_to_on():
            return False

        await api.async_set_schedule(enabled=True, pod=self.pod)

        self.coordinator.last_message_at = datetime.now(pytz.UTC)
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
        return self.pod.charge_mode == ChargeMode.MANUAL or (
            self.pod.charge_override is not None and self.pod.charge_override.active
        )


class PodPointChargeModeSwitch(PodPointEntity, SwitchEntity):
    """Charger-centric delegated smart-charging switch."""

    _attr_name = "Smart Charge Mode"
    _attr_icon = "mdi:cog"
    _attr_has_entity_name = True

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Enable delegated smart charging."""
        api: PodPointClient = self.coordinator.api
        charger = self.coordinator.chargers[self.pod.ppid]
        if await api.async_set_charger_smart_charging(charger, True):
            self.coordinator.last_message_at = datetime.now(pytz.UTC)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Disable delegated smart charging."""
        api: PodPointClient = self.coordinator.api
        charger = self.coordinator.chargers[self.pod.ppid]
        if await api.async_set_charger_smart_charging(charger, False):
            self.coordinator.last_message_at = datetime.now(pytz.UTC)
            await self.coordinator.async_request_refresh()

    @property
    def unique_id(self):
        return f"{super().unique_id}_smart_charge_mode"

    @property
    def is_on(self):
        control = self.coordinator.delegated_controls.get(self.pod.ppid)
        return control is not None and control.status == "ACTIVE"

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.delegated_controls.get(self.pod.ppid) is not None
        )
