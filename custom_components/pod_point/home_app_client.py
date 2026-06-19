"""Pod Point Home App API helpers."""

import logging
from typing import Any, Dict

import aiohttp
import async_timeout
from podpointclient.client import PodPointClient
from podpointclient.errors import APIError, ApiConnectionError
from podpointclient.helpers.functions import auth_headers

from .const import HOME_APP_API_URL

TIMEOUT = 10

_LOGGER: logging.Logger = logging.getLogger(__package__)


class PodPointHomeAppClient:
    """Client for Pod Point Home App endpoints."""

    def __init__(self, client: PodPointClient) -> None:
        """Initialize."""
        self._client = client

    async def async_get_delegated_control_vehicles(self):
        """Return all delegated control charger vehicle allocations."""
        return await self._get("/smart-charging/delegated-controls/vehicles")

    async def async_get_delegated_controls(self, ppid: str):
        """Return delegated controls for a charger."""
        return await self._get(f"/smart-charging/delegated-controls/{ppid}")

    async def async_get_charge_overrides(self, ppid: str):
        """Return Home App charge overrides for a charger."""
        return await self._get(f"/chargers/{ppid}/charge-overrides")

    async def async_put_charge_overrides(self, ppid: str, body: Dict[str, Any]):
        """Create or update Home App charge overrides for a charger."""
        return await self._put(f"/chargers/{ppid}/charge-overrides", body)

    async def async_delete_charge_overrides(self, ppid: str):
        """Delete Home App charge overrides for a charger."""
        return await self._delete(f"/chargers/{ppid}/charge-overrides")

    async def async_get_tariffs(self, ppid: str):
        """Return tariffs for a charger."""
        return await self._get(f"/chargers/{ppid}/tariffs")

    async def async_get_reward_wallet(self):
        """Return the reward wallet for the authenticated user."""
        return await self._get("/reward-wallet")

    async def async_get_remote_lock(self, ppid: str):
        """Return remote lock state for a charger."""
        return await self._get(f"/remote-lock/{ppid}")

    async def async_put_remote_lock(self, ppid: str, body: Dict[str, Any]):
        """Update remote lock state for a charger."""
        return await self._put(f"/remote-lock/{ppid}", body)

    async def async_get_delegated_control_preferences(self, ppid: str):
        """Return delegated control preferences for a charger."""
        return await self._get(
            f"/smart-charging/delegated-controls/{ppid}/preferences"
        )

    async def async_patch_delegated_control_preferences(
        self, ppid: str, body: Dict[str, Any]
    ):
        """Update delegated control preferences for a charger."""
        return await self._patch(
            f"/smart-charging/delegated-controls/{ppid}/preferences", body
        )

    async def _get(self, path: str):
        response = await self._client.api_wrapper.get(
            url=self._url(path),
            headers=await self._auth_headers(),
        )

        return await self._json_or_none(response)

    async def _put(self, path: str, body: Dict[str, Any]):
        response = await self._client.api_wrapper.put(
            url=self._url(path),
            body=body,
            headers=await self._auth_headers(),
        )

        return await self._json_or_none(response)

    async def _delete(self, path: str):
        response = await self._client.api_wrapper.delete(
            url=self._url(path),
            headers=await self._auth_headers(),
        )

        return await self._json_or_none(response)

    async def _patch(self, path: str, body: Dict[str, Any]):
        headers = await self._auth_headers()
        url = self._url(path)

        try:
            async with async_timeout.timeout(TIMEOUT):
                _LOGGER.debug("PATCH %s %s", url, body)
                response = await self._client._session.patch(
                    url,
                    headers=headers,
                    json=body,
                )
        except aiohttp.ClientError as exception:
            raise ApiConnectionError(
                f"Error connecting to Pod Point Home App ({url}) - {exception}"
            ) from exception

        if response.status < 200 or response.status > 204:
            text = await response.text()
            raise APIError(response.status, text)

        return await self._json_or_none(response)

    async def _auth_headers(self) -> Dict[str, str]:
        await self._client.auth.async_update_access_token()
        return auth_headers(access_token=self._client.auth.access_token)

    async def _json_or_none(self, response):
        if response.status == 204:
            return None

        return await self._client._handle_json_response(response=response)

    def _url(self, path: str) -> str:
        return f"{HOME_APP_API_URL}{path}"
