"""Provider that talks to the Node (Baileys) WhatsApp gateway over HTTP.

Contract (identical on the gateway side):
- ``POST /messages`` ``{"to", "text", "client_ref"}`` -> 200 ``{"provider_message_id"}``,
  404 ``{"error": "not_on_whatsapp"}``, 503 ``{"error": "disconnected"}``
- ``GET /status`` -> 200 ``{"state", "qr"}``
- ``POST /logout`` -> 200 or 204
All calls carry ``Authorization: Bearer <api key>``.

Logging never includes message text, the API key or raw response bodies; phone numbers are
only logged through ``mask_phone``.
"""

import logging
from typing import Any, cast, get_args

import httpx

from app.modules.whatsapp.provider import (
    NotOnWhatsApp,
    ProviderState,
    ProviderStatus,
    ProviderUnavailable,
    SendResult,
)
from app.shared.phone import mask_phone

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_VALID_STATES = frozenset(get_args(ProviderState))
_AUTH_REJECTED = "Kunci API gateway ditolak."
_UNAVAILABLE = "Gateway WhatsApp tidak tersedia."
_BAD_RESPONSE = "Gateway WhatsApp mengirim respons yang tidak valid."


class GatewayProvider:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client

    async def aclose(self) -> None:
        """Close the shared HTTP client. The sending worker owns calling this on shutdown."""
        await self._client.aclose()

    async def send(self, *, phone: str, text: str, client_ref: str) -> SendResult:
        masked = mask_phone(phone)
        payload = {"to": phone, "text": text, "client_ref": client_ref}
        response = await self._request("POST", "/messages", json=payload)

        if response.status_code == 404 and _json_error(response) == "not_on_whatsapp":
            logger.info("Gateway: %s is not on WhatsApp (ref %s)", masked, client_ref)
            raise NotOnWhatsApp("Nomor tidak terdaftar di WhatsApp.")
        _raise_for_failure(response, masked)

        body = _json_object(response)
        message_id = body.get("provider_message_id")
        if not isinstance(message_id, str) or not message_id:
            logger.warning("Gateway: malformed send response for %s (ref %s)", masked, client_ref)
            raise ProviderUnavailable(_BAD_RESPONSE)
        logger.debug("Gateway: sent to %s (ref %s)", masked, client_ref)
        return SendResult(provider_message_id=message_id)

    async def status(self) -> ProviderStatus:
        response = await self._request("GET", "/status")
        _raise_for_failure(response)
        body = _json_object(response)
        state = body.get("state")
        qr = body.get("qr")
        if state not in _VALID_STATES or not (qr is None or isinstance(qr, str)):
            logger.warning("Gateway: malformed status response")
            raise ProviderUnavailable(_BAD_RESPONSE)
        return ProviderStatus(state=cast(ProviderState, state), qr=qr)

    async def logout(self) -> None:
        response = await self._request("POST", "/logout")
        _raise_for_failure(response)

    async def _request(
        self, method: str, path: str, *, json: dict[str, str] | None = None
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=self._headers,
                timeout=TIMEOUT,
            )
        except httpx.TransportError as exc:
            # Timeouts are TransportErrors too; log only the exception type, never its text.
            logger.warning("Gateway %s %s failed: %s", method, path, type(exc).__name__)
            raise ProviderUnavailable(_UNAVAILABLE) from None


def _raise_for_failure(response: httpx.Response, masked_phone: str | None = None) -> None:
    status_code = response.status_code
    if status_code in (401, 403):
        logger.error("Gateway rejected the API key (HTTP %s)", status_code)
        raise ProviderUnavailable(_AUTH_REJECTED)
    if status_code >= 400:
        logger.warning("Gateway answered HTTP %s (phone %s)", status_code, masked_phone)
        raise ProviderUnavailable(_UNAVAILABLE)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body: object = response.json()
    except ValueError:
        raise ProviderUnavailable(_BAD_RESPONSE) from None
    if not isinstance(body, dict):
        raise ProviderUnavailable(_BAD_RESPONSE)
    return cast(dict[str, Any], body)


def _json_error(response: httpx.Response) -> str | None:
    try:
        body: object = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        error = cast(dict[str, Any], body).get("error")
        return error if isinstance(error, str) else None
    return None
