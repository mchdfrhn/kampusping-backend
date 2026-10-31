"""The WhatsApp provider contract used by the sending worker and the status endpoints."""

from dataclasses import dataclass
from typing import Literal, Protocol

ProviderState = Literal["connected", "connecting", "qr", "disconnected"]


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str


@dataclass(frozen=True)
class ProviderStatus:
    state: ProviderState
    qr: str | None = None


class ProviderError(Exception):
    """Base class for every failure raised by a WhatsApp provider."""


class NotOnWhatsApp(ProviderError):
    """The recipient number is not registered on WhatsApp (permanent failure)."""


class ProviderUnavailable(ProviderError):
    """The provider could not deliver right now (transient, or misconfigured)."""


class WhatsAppProvider(Protocol):
    async def send(self, *, phone: str, text: str, client_ref: str) -> SendResult: ...

    async def status(self) -> ProviderStatus: ...

    async def logout(self) -> None: ...
