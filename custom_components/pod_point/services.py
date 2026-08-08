"""Services for the Pod Point integration."""

import logging
from datetime import UTC, datetime

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from podpointclient.client import PodPointClient
from podpointclient.errors import APIError, RequestValidationError
from podpointclient.pod import Pod

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DEVICE_ID,
    ATTR_HOURS,
    ATTR_MINUTES,
    ATTR_SECONDS,
    DOMAIN,
    SERVICE_CHARGE_NOW,
    SERVICE_STOP_CHARGE_NOW,
)
from .coordinator import PodPointDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


class PodPointServiceException(ServiceValidationError):
    """Exception for Pod Point services."""


async def async_register_services(hass: HomeAssistant) -> None:
    """Register services for the Pod Point integration, if not registered yet."""

    if not hass.services.has_service(DOMAIN, SERVICE_CHARGE_NOW):
        _LOGGER.info("Registering SERVICE_CHARGE_NOW for Pod Point integration")

        async def async_charge_now_service(call: ServiceCall):
            coordinator, pod = get_service_target(hass, call)
            try:
                await handle_charge_now(coordinator, call, pod)
            except RequestValidationError as err:
                raise PodPointServiceException(str(err)) from err
            except APIError as err:
                raise HomeAssistantError("Pod Point rejected the request") from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_CHARGE_NOW,
            async_charge_now_service,
            schema=vol.All(
                vol.Schema(
                    {
                        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                        vol.Optional(ATTR_DEVICE_ID): cv.string,
                        vol.Optional(ATTR_HOURS): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=24)
                        ),
                        vol.Optional(ATTR_MINUTES): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=59)
                        ),
                        vol.Optional(ATTR_SECONDS): vol.All(
                            vol.Coerce(int), vol.Range(min=0, max=59)
                        ),
                    }
                ),
                cv.has_at_least_one_key(ATTR_CONFIG_ENTRY_ID, ATTR_DEVICE_ID),
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_STOP_CHARGE_NOW):
        _LOGGER.info("Registering SERVICE_STOP_CHARGE_NOW for Pod Point integration")

        async def async_stop_charge_now_service(call: ServiceCall):
            coordinator, pod = get_service_target(hass, call)
            try:
                await handle_stop_charge_now(coordinator, pod)
            except RequestValidationError as err:
                raise PodPointServiceException(str(err)) from err
            except APIError as err:
                raise HomeAssistantError("Pod Point rejected the request") from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_STOP_CHARGE_NOW,
            async_stop_charge_now_service,
            schema=vol.All(
                vol.Schema(
                    {
                        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                        vol.Optional(ATTR_DEVICE_ID): cv.string,
                    }
                ),
                cv.has_at_least_one_key(ATTR_CONFIG_ENTRY_ID, ATTR_DEVICE_ID),
            ),
        )


def get_coordinator(
    hass: HomeAssistant, config_entry_id: str | None
) -> PodPointDataUpdateCoordinator:
    """Return the coordinator for a loaded Pod Point config entry."""
    if config_entry_id is None:
        raise PodPointServiceException(
            "A config_entry_id or device_id must be provided"
        )
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None:
        raise PodPointServiceException(
            f"Config entry with id {config_entry_id} was not found"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise PodPointServiceException(
            f"Config entry with id {config_entry_id} is not loaded"
        )
    return entry.runtime_data


def get_service_target(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[PodPointDataUpdateCoordinator, Pod]:
    """Resolve a service call to one specific Pod."""
    config_entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    device_id = call.data.get(ATTR_DEVICE_ID)

    if device_id is None:
        coordinator = get_coordinator(hass, config_entry_id)
        if len(coordinator.pods) != 1:
            raise PodPointServiceException(
                f"device_id is required for accounts with {len(coordinator.pods)} Pods"
            )
        return coordinator, coordinator.pods[0]

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise PodPointServiceException(f"Device with id {device_id} was not found")

    if config_entry_id is not None and config_entry_id not in device.config_entries:
        raise PodPointServiceException(
            f"Device with id {device_id} does not belong to config entry {config_entry_id}"
        )

    entry_ids = (
        [config_entry_id] if config_entry_id is not None else device.config_entries
    )
    identifiers = {
        identifier for domain, identifier in device.identifiers if domain == DOMAIN
    }
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            continue
        coordinator = entry.runtime_data
        for pod in coordinator.pods:
            serial_number = pod.ppid
            if pod.firmware is not None and pod.firmware.serial_number:
                serial_number = pod.firmware.serial_number
            if serial_number in identifiers:
                return coordinator, pod

    raise PodPointServiceException(
        f"Device with id {device_id} is not a loaded Pod Point charger"
    )


async def handle_charge_now(
    coordinator: PodPointDataUpdateCoordinator,
    call: ServiceCall,
    pod: Pod | None = None,
) -> None:
    """Handle the call for the add_product service."""
    if pod is None:
        pods: list[Pod] = coordinator.pods
        if len(pods) != 1:
            raise PodPointServiceException(
                f"Service requires a specific Pod, found {len(pods)} Pods"
            )
        pod = pods[0]

    hours = call.data.get(ATTR_HOURS, 0)
    minutes = call.data.get(ATTR_MINUTES, 0)
    seconds = call.data.get(ATTR_SECONDS, 0)

    hours_set = 0 < hours <= 24
    minutes_set = 0 < minutes <= 59
    seconds_set = 0 < seconds <= 59
    valid_time_passed = hours_set or minutes_set or seconds_set

    if valid_time_passed is False:
        raise PodPointServiceException(
            "Please pass an hours, minutes or seconds value. Cannot set 'charge now' with 0 values."
        )

    await async_start_charge_now(coordinator, pod, hours, minutes, seconds)


async def async_start_charge_now(
    coordinator: PodPointDataUpdateCoordinator,
    pod: Pod,
    hours: int,
    minutes: int,
    seconds: int = 0,
) -> None:
    """Start a timed charge override for one Pod."""
    api: PodPointClient = coordinator.api
    charger = coordinator.chargers.get(pod.ppid)
    if charger is not None:
        await api.async_create_charger_charge_override(
            charger=charger, hours=hours, minutes=minutes, seconds=seconds
        )
    else:
        await api.async_set_charge_override(
            pod=pod, hours=hours, minutes=minutes, seconds=seconds
        )

    coordinator.last_message_at = datetime.now(UTC)
    await coordinator.async_request_refresh()


async def handle_stop_charge_now(
    coordinator: PodPointDataUpdateCoordinator,
    pod: Pod | None = None,
) -> None:
    """Handle the call for the add_product service."""
    if pod is None:
        pods: list[Pod] = coordinator.pods
        if len(pods) != 1:
            raise PodPointServiceException(
                f"Service requires a specific Pod, found {len(pods)} Pods"
            )
        pod = pods[0]

    await async_stop_charge_now(coordinator, pod)


async def async_stop_charge_now(
    coordinator: PodPointDataUpdateCoordinator, pod: Pod
) -> None:
    """Stop a charge override for one Pod."""
    api: PodPointClient = coordinator.api
    charger = coordinator.chargers.get(pod.ppid)
    if charger is not None:
        await api.async_delete_charger_charge_overrides(charger=charger)
    else:
        await api.async_delete_charge_override(pod=pod)

    coordinator.last_message_at = datetime.now(UTC)
    await coordinator.async_request_refresh()
