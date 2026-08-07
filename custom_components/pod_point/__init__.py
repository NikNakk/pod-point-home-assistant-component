"""
Custom integration to integrate pod_point with Home Assistant.

For more details about this integration, please refer to
https://github.com/mattrayner/pod-point-home-assistant-component
"""

from datetime import timedelta
import logging
from pathlib import Path
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.core_config import Config
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
    PLATFORMS,
    STARTUP_MESSAGE,
)
from .coordinator import PodPointDataUpdateCoordinator
from .services import async_register_services

type PodPointConfigEntry = ConfigEntry[PodPointDataUpdateCoordinator]

_LOGGER: logging.Logger = logging.getLogger(__package__)

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
