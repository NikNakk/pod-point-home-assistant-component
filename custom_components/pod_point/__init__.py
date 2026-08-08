"""
Custom integration to integrate pod_point with Home Assistant.

For more details about this integration, please refer to
https://github.com/mattrayner/pod-point-home-assistant-component
"""

import logging
import re
from datetime import timedelta
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.core_config import Config
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from podpointclient.client import PodPointClient

from .const import (
    APP_IMAGE_URL_BASE,
    CONF_EMAIL,
    CONF_HTTP_DEBUG,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_HTTP_DEBUG,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)
from .coordinator import PodPointDataUpdateCoordinator
from .services import async_register_services

type PodPointConfigEntry = ConfigEntry[PodPointDataUpdateCoordinator]

_LOGGER: logging.Logger = logging.getLogger(__package__)

_LEGACY_CHARGER_UNIQUE_ID = re.compile(r"^pod_point_\d+_([^_]+)(.*)$")

# pylint: disable=unused-argument


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up shared Pod Point integration resources."""
    files_path = Path(__file__).parent / "static"
    if hass.http:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(APP_IMAGE_URL_BASE, str(files_path), False)]
        )
    await async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PodPointConfigEntry):
    """Set up this integration using UI."""
    _LOGGER.info(STARTUP_MESSAGE)

    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    if entry.unique_id is None and email:
        hass.config_entries.async_update_entry(entry, unique_id=email.casefold())

    _async_migrate_entity_unique_ids(hass, entry)

    session = async_get_clientsession(hass)

    # If http debug is set, use that, or default
    try:
        http_debug = entry.options[CONF_HTTP_DEBUG]
    except KeyError:
        http_debug = DEFAULT_HTTP_DEBUG

    client = PodPointClient(
        username=email, password=password, session=session, http_debug=http_debug
    )

    # If a scan interval is set, use that, or default
    try:
        scan_interval = timedelta(seconds=entry.options[CONF_SCAN_INTERVAL])
    except KeyError:
        scan_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

    # Setup our data coordinator with the desired scan interval
    coordinator = PodPointDataUpdateCoordinator(
        hass, config_entry=entry, client=client, scan_interval=scan_interval
    )

    # Check the credentials we have and ensure that we can perform a refresh
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # For every platform defined, check if the user has disabled it. If not, set it up
    for platform in PLATFORMS:
        if entry.options.get(platform.value, True):
            coordinator.platforms.append(platform)

    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


def _async_migrate_entity_unique_ids(
    hass: HomeAssistant, entry: PodPointConfigEntry
) -> None:
    """Move registry identities off legacy API IDs without changing entity IDs."""
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        new_unique_id = None
        match = _LEGACY_CHARGER_UNIQUE_ID.fullmatch(registry_entry.unique_id)
        if match:
            ppid, suffix = match.groups()
            new_unique_id = f"{DOMAIN}_{ppid}{suffix}"
            if registry_entry.device_id is not None:
                device = device_registry.async_get(registry_entry.device_id)
                if device is not None and (DOMAIN, ppid) not in device.identifiers:
                    device_registry.async_update_device(
                        device.id,
                        new_identifiers={*device.identifiers, (DOMAIN, ppid)},
                    )
        elif (
            getattr(registry_entry, "translation_key", None) == "account_balance"
            or registry_entry.original_name == "Pod Point Balance"
        ):
            new_unique_id = f"{DOMAIN}_{entry.entry_id}_account_balance"

        if new_unique_id is None or new_unique_id == registry_entry.unique_id:
            continue
        if registry.async_get_entity_id(
            registry_entry.domain, registry_entry.platform, new_unique_id
        ):
            _LOGGER.warning(
                "Unable to migrate %s because unique ID %s already exists",
                registry_entry.entity_id,
                new_unique_id,
            )
            continue
        registry.async_update_entity(
            registry_entry.entity_id, new_unique_id=new_unique_id
        )


async def async_unload_entry(hass: HomeAssistant, entry: PodPointConfigEntry) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(
        entry,
        [
            platform
            for platform in PLATFORMS
            if platform in entry.runtime_data.platforms
        ],
    )


async def async_reload_entry(hass: HomeAssistant, entry: PodPointConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
