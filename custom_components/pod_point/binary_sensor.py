"""Binary sensor platform for pod_point."""

import logging
from typing import Any, Dict

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory

from .const import ATTR_CONNECTION_STATE_ONLINE, ATTR_STATE, ATTRIBUTION, DOMAIN
from .coordinator import PodPointDataUpdateCoordinator
from .entity import PodPointEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup binary_sensor platform."""
    coordinator: PodPointDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Handle coordinator offline on boot - no data will be populated
    if coordinator.online is False:
        return

    sensors = []
    for i in range(len(coordinator.data)):
        cable_sensor = PodPointCableConnectionSensor(coordinator, entry, i)
        cable_sensor.pod_id = i
        sensors.append(cable_sensor)

        cloud_sensor = PodPointCloudConnectionSensor(coordinator, entry, i)
        cloud_sensor.pod_id = i
        sensors.append(cloud_sensor)

        if coordinator.remote_locks.get(coordinator.data[i].ppid) is not None:
            sensors.append(PodPointOffModeSensor(coordinator, entry, i))
        charger = coordinator.chargers.get(coordinator.data[i].ppid)
        if charger is not None and charger.delegated_control_status is not None:
            sensors.append(PodPointSmartChargingSensor(coordinator, entry, i))

    async_add_devices(sensors)


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
        status_v2 = self.coordinator.connectivity_v2.get(self.pod.ppid)
        if status_v2 is not None:
            return (
                status_v2.connection_state is not None
                and status_v2.connection_state.casefold()
                == ATTR_CONNECTION_STATE_ONLINE.casefold()
            )

        if self.pod is None:
            return False

        if self.pod.connectivity_status is None:
            return False

        return (
            self.pod.connectivity_status.connectivity_status
            == ATTR_CONNECTION_STATE_ONLINE
        )

    @property
    def icon(self):
        """Return the icon of the sensor."""

        if self.is_on:
            return "mdi:cloud-check-variant"

        return "mdi:cloud-off"


class PodPointOffModeSensor(PodPointEntity, BinarySensorEntity):
    """Whether remote off mode is enabled in Pod Home."""

    _attr_has_entity_name = True
    _attr_name = "Off mode"
    _attr_icon = "mdi:power-plug-off"

    @property
    def unique_id(self):
        return f"{super().unique_id}_off_mode"

    @property
    def is_on(self):
        remote_lock = self.coordinator.remote_locks.get(self.pod.ppid)
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
        charger = self.coordinator.chargers.get(self.pod.ppid)
        if charger is None or charger.delegated_control_status is None:
            return False
        return charger.delegated_control_status.upper() in {"ACTIVE", "ENABLED"}
