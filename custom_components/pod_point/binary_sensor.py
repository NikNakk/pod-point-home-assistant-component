"""Binary sensor platform for pod_point."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory
from podpointclient.domain import StateValue

from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup binary_sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = entry.runtime_data
    known_entities: set[tuple[str, str]] = set()

    def _async_add_new_entities() -> None:
        sensors = []
        for index, pod in enumerate(coordinator.data):
            candidates = [
                ("cable", PodPointCableConnectionSensor),
                ("cloud", PodPointCloudConnectionSensor),
            ]
            remote_lock = coordinator.remote_locks.get(pod.ppid)
            if remote_lock is not None and remote_lock.off_mode is not None:
                candidates.append(("off_mode", PodPointRemoteLockSensor))
            if coordinator.smart_charging_states.get(pod.ppid) is not None:
                candidates.append(("smart_charging", PodPointSmartChargingSensor))

            for key, entity_type in candidates:
                entity_key = (pod.ppid, key)
                if entity_key not in known_entities:
                    known_entities.add(entity_key)
                    sensors.append(entity_type(coordinator, entry, index))

        if sensors:
            async_add_devices(sensors)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class PodPointCableConnectionSensor(PodPointEntity, BinarySensorEntity):
    """pod_point cable connection class."""

    _attr_has_entity_name = True
    _attr_name = "Cable Status"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    @property
    def unique_id(self):
        return f"{super().unique_id}_cable_status"

    @property
    def is_on(self):
        """Return true if the binary_sensor is on."""
        return self.connected


class PodPointCloudConnectionSensor(PodPointEntity, BinarySensorEntity):
    """pod_point cloud connection class."""

    _attr_has_entity_name = True
    _attr_name = "Cloud Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self):
        return f"{super().unique_id}_cloud_connection"

    @property
    def is_on(self):
        """Return true if the binary_sensor is on."""
        state = self.coordinator.charger_states.get(self.charger.ppid)
        return bool(state and state.connection.value is StateValue.ONLINE)

    @property
    def icon(self):
        """Return the icon of the sensor."""

        if self.is_on:
            return "mdi:cloud-check-variant"

        return "mdi:cloud-off"


class PodPointRemoteLockSensor(PodPointEntity, BinarySensorEntity):
    """Whether the charger is secured using Pod Point Off mode."""

    _attr_has_entity_name = True
    _attr_name = "Remote lock"
    _attr_icon = "mdi:lock"

    @property
    def unique_id(self):
        return f"{super().unique_id}_off_mode"

    @property
    def is_on(self):
        remote_lock = self.coordinator.remote_locks.get(self.charger.ppid)
        return bool(remote_lock and remote_lock.off_mode)


class PodPointSmartChargingSensor(PodPointEntity, BinarySensorEntity):
    """Whether charger-level delegated smart charging is enabled."""

    _attr_has_entity_name = True
    _attr_name = "Pod Home smart charging"
    _attr_icon = "mdi:ev-station"

    @property
    def unique_id(self):
        return f"{super().unique_id}_pod_home_smart_charging"

    @property
    def is_on(self):
        smart_charging = self.coordinator.smart_charging_states.get(self.charger.ppid)
        status = getattr(smart_charging, "status", None)
        if status is None:
            return False
        return status.upper() in {"ACTIVE", "ENABLED"}
