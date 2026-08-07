"""Services for the Pod Point integration."""

from datetime import UTC, datetime
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv
from podpointclient.client import PodPointClient
from podpointclient.errors import APIError, RequestValidationError
from podpointclient.pod import Pod
import voluptuous as vol

from .const import (
    ATTR_CONFIG_ENTRY_ID,
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
            coordinator = get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
            try:
                await handle_charge_now(coordinator, call)
            except RequestValidationError as err:
                raise PodPointServiceException(str(err)) from err
            except APIError as err:
                raise HomeAssistantError("Pod Point rejected the request") from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_CHARGE_NOW,
            async_charge_now_service,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
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
        )

    if not hass.services.has_service(DOMAIN, SERVICE_STOP_CHARGE_NOW):
        _LOGGER.info("Registering SERVICE_STOP_CHARGE_NOW for Pod Point integration")

        async def async_stop_charge_now_service(call: ServiceCall):
            coordinator = get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
            try:
                await handle_stop_charge_now(coordinator)
            except RequestValidationError as err:
                raise PodPointServiceException(str(err)) from err
            except APIError as err:
                raise HomeAssistantError("Pod Point rejected the request") from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_STOP_CHARGE_NOW,
            async_stop_charge_now_service,
            schema=vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}),
        )


def get_coordinator(hass: HomeAssistant, config_entry_id: str) -> PodPointDataUpdateCoordinator:
    """Return the coordinator for a loaded Pod Point config entry."""
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


async def handle_charge_now(
    coordinator: PodPointDataUpdateCoordinator, call: ServiceCall
) -> None:
    """Handle the call for the add_product service."""
    api: PodPointClient = coordinator.api
    pods: list[Pod] = coordinator.pods
    pod: Pod
    if len(pods) == 1:
        pod = pods[0]
    else:
        raise PodPointServiceException(
            f"Service only supports accounts with 1 Pod attached, found {len(pods)} Pods!"
        )

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
) -> None:
    """Handle the call for the add_product service."""
    api: PodPointClient = coordinator.api
    pods: list[Pod] = coordinator.pods
    pod: Pod
    if len(pods) == 1:
        pod = pods[0]
    else:
        raise PodPointServiceException(
            f"Service only supports accounts with 1 Pod attached, found {len(pods)} Pods!"
        )

    charger = coordinator.chargers.get(pod.ppid)
    if charger is not None:
        await api.async_delete_charger_charge_overrides(charger=charger)
    else:
        await api.async_delete_charge_override(pod=pod)

    coordinator.last_message_at = datetime.now(UTC)
    await coordinator.async_request_refresh()
