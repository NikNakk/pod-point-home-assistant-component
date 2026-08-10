"""Adds config flow for Pod Point."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from podpointclient.client import PodPointClient
from podpointclient.errors import ApiConnectionError, AuthError, SessionError

from .const import (
    CONF_CURRENCY,
    CONF_EMAIL,
    CONF_HTTP_DEBUG,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USE_LEGACY_API,
    DEFAULT_CURRENCY,
    DEFAULT_HTTP_DEBUG,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    """Raised when Pod Point cannot be reached."""


class InvalidAuth(Exception):
    """Raised when Pod Point rejects the supplied credentials."""


class PodPointFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Pod Point."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize."""
        self._errors = {}

    # pylint: disable=unused-argument
    async def async_step_reauth(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            unique_id = user_input[CONF_EMAIL].casefold()
            if reauth_entry.unique_id is None:
                self.hass.config_entries.async_update_entry(
                    reauth_entry, unique_id=unique_id
                )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_mismatch()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        self._errors = {}

        if user_input is None:
            user_input = {}
            # Provide defaults for form
            user_input[CONF_EMAIL] = (
                self._get_reauth_entry().data[CONF_EMAIL]
                if self.source == config_entries.SOURCE_REAUTH
                else ""
            )
            user_input[CONF_PASSWORD] = ""
            user_input[CONF_USE_LEGACY_API] = (
                self._get_reauth_entry().data.get(CONF_USE_LEGACY_API, False)
                if self.source == config_entries.SOURCE_REAUTH
                else False
            )

            return await self._show_config_form(user_input)

        user_input[CONF_EMAIL] = user_input[CONF_EMAIL].strip()
        try:
            await self._test_credentials(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
        except InvalidAuth:
            self._errors["base"] = "auth"
            return await self._show_config_form(user_input)
        except CannotConnect:
            self._errors["base"] = "cannot_connect"
            return await self._show_config_form(user_input)
        except Exception:  # pragma: no cover - defensive flow fallback
            _LOGGER.exception("Unexpected error validating Pod Point credentials")
            self._errors["base"] = "unknown"
            return await self._show_config_form(user_input)

        if self.source == config_entries.SOURCE_REAUTH:
            reauth_entry = self._get_reauth_entry()
            if user_input[CONF_EMAIL].casefold() != reauth_entry.unique_id:
                self._errors["base"] = "wrong_account"
                return await self._show_config_form(user_input)
            return self.async_update_reload_and_abort(
                reauth_entry,
                data_updates=user_input,
            )

        await self.async_set_unique_id(user_input[CONF_EMAIL].casefold())
        self._abort_if_unique_id_configured(
            updates=user_input,
            error="reauth_successful",
        )

        return self.async_create_entry(title=user_input[CONF_EMAIL], data=user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, bool] | None = None
    ) -> ConfigFlowResult:
        """Allow an existing entry to select its wire API."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                data_updates=user_input,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USE_LEGACY_API,
                        default=entry.data.get(CONF_USE_LEGACY_API, False),
                    ): bool
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> config_entries.OptionsFlow:
        return PodPointOptionsFlowHandler()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        formatted_mac = format_mac(discovery_info.macaddress)
        _LOGGER.info("Found PodPoint device with mac %s", formatted_mac)

        await self.async_set_unique_id(formatted_mac)
        self._abort_if_unique_id_configured()

        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        return await self.async_step_user()

    async def _show_config_form(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:  # pylint: disable=unused-argument
        """Show the configuration form to edit location data."""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=user_input[CONF_EMAIL]): str,
                    vol.Required(CONF_PASSWORD, default=user_input[CONF_PASSWORD]): str,
                    vol.Required(
                        CONF_USE_LEGACY_API,
                        default=user_input[CONF_USE_LEGACY_API],
                    ): bool,
                }
            ),
            errors=self._errors,
        )

    async def _test_credentials(self, username: str, password: str) -> None:
        """Validate credentials or raise a flow-specific exception."""
        try:
            session = async_create_clientsession(self.hass)
            client = PodPointClient(
                username=username, password=password, session=session
            )
            if not await client.async_charger_credentials_verified():
                raise InvalidAuth
        except (AuthError, SessionError) as err:
            raise InvalidAuth from err
        except ApiConnectionError as err:
            raise CannotConnect from err


class PodPointOptionsFlowHandler(config_entries.OptionsFlow):
    """Pod Point config flow options handler."""

    async def async_step_init(
        self, _=None
    ) -> ConfigFlowResult:  # pylint: disable=unused-argument
        """Manage the options."""
        self.options = dict(self.config_entry.options)
        return await self.async_step_user()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        currency_schema = {
            vol.Required(
                CONF_CURRENCY,
                default=self.options.get(CONF_CURRENCY, DEFAULT_CURRENCY),
            ): vol.All(str, vol.Length(min=3, max=3), str.upper)
        }

        poll_schema = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=self.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400))
        }

        platforms_schema = {
            vol.Required(
                x.value,
                default=self.options.get(x.value, True),
            ): bool
            for x in sorted(PLATFORMS)
        }

        debug_schema = {
            vol.Required(
                CONF_HTTP_DEBUG,
                default=self.options.get(CONF_HTTP_DEBUG, DEFAULT_HTTP_DEBUG),
            ): bool
        }

        options_schema = vol.Schema(
            {**currency_schema, **platforms_schema, **debug_schema, **poll_schema}
        )

        return self.async_show_form(step_id="user", data_schema=options_schema)

    async def _update_options(self):
        """Update config entry options."""
        return self.async_create_entry(
            title=self.config_entry.data.get(CONF_EMAIL), data=self.options
        )
