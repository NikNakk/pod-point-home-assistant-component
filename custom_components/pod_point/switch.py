"""Switch platform for pod_point."""

from datetime import UTC, datetime
import logging

from homeassistant.components.switch import SwitchEntity
from podpointclient.charge_mode import ChargeMode
from podpointclient.client import PodPointClient

from .const import SWITCH_ICON
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_pods: set[str] = set()

    def _add_new_entities() -> None:
        switches = []
        for index, pod in enumerate(coordinator.data):
            if pod.ppid in known_pods:
                continue
            known_pods.add(pod.ppid)
            if pod.ppid in coordinator.chargers:
                # Preserve the legacy smart-mode entity unique ID while moving its
                # implementation to the charger-centric delegated-control endpoint.
                switches.append(PodPointChargeModeSwitch(coordinator, entry, index))
            else:
                switches.append(
                    PodPointChargingAllowedSwitch(coordinator, entry, index)
                )
        if switches:
            async_add_devices(switches)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PodPointChargingAllowedSwitch(PodPointEntity, SwitchEntity):
    """pod_point switch class."""

    _attr_has_entity_name = True
    _attr_name = "Charging Allowed"
    _attr_icon = SWITCH_ICON

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Allow charging (clear schedule)"""
        api: PodPointClient = self.coordinator.api
        await self.coordinator.async_api_call(
            api.async_set_schedule(enabled=False, pod=self.pod)
        )

        self.coordinator.last_message_at = datetime.now(UTC)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Block charging (turn on schedule). Unless an override or charge mode would prevent this functionality"""
        api: PodPointClient = self.coordinator.api

        # Exit early if the pod cannot be switched off due to charge mode or override
        if self._override_to_on():
            return False

        await self.coordinator.async_api_call(
            api.async_set_schedule(enabled=True, pod=self.pod)
        )

        self.coordinator.last_message_at = datetime.now(UTC)
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
        if await self.coordinator.async_api_call(
            api.async_set_charger_smart_charging(charger, True)
        ):
            self.coordinator.last_message_at = datetime.now(UTC)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Disable delegated smart charging."""
        api: PodPointClient = self.coordinator.api
        charger = self.coordinator.chargers[self.pod.ppid]
        if await self.coordinator.async_api_call(
            api.async_set_charger_smart_charging(charger, False)
        ):
            self.coordinator.last_message_at = datetime.now(UTC)
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
