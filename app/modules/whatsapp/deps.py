"""Dependency factory for the active WhatsApp provider (``WA_PROVIDER=fake|gateway``)."""

from functools import lru_cache

import httpx

from app.core.config import settings
from app.modules.whatsapp.fake import FakeProvider
from app.modules.whatsapp.gateway import TIMEOUT, GatewayProvider
from app.modules.whatsapp.provider import WhatsAppProvider


@lru_cache(maxsize=1)
def get_provider() -> WhatsAppProvider:
    """Return the process-wide provider; use as ``Depends(get_provider)``.

    The gateway's ``httpx.AsyncClient`` is created lazily here and lives as long as the
    cached provider. The sending worker owns shutdown: it must ``await provider.aclose()``
    (when the provider is a ``GatewayProvider``) and then call ``reset_provider_cache()``.
    Tests override this dependency with ``app.dependency_overrides``.
    """
    if settings.wa_provider == "fake":
        return FakeProvider()
    url = settings.wa_gateway_url.strip()
    key = settings.wa_gateway_api_key
    api_key = key.get_secret_value() if key else ""
    if not url or not api_key:
        raise RuntimeError(
            "WA_GATEWAY_URL and WA_GATEWAY_API_KEY must be set when WA_PROVIDER=gateway"
        )
    return GatewayProvider(base_url=url, api_key=api_key, client=httpx.AsyncClient(timeout=TIMEOUT))


def reset_provider_cache() -> None:
    """Forget the cached provider (tests, or after the worker closed it)."""
    get_provider.cache_clear()
