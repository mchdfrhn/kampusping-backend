"""In-memory provider for development and tests. Deterministic and never touches the network."""

from dataclasses import dataclass, field

from app.modules.whatsapp.provider import (
    NotOnWhatsApp,
    ProviderState,
    ProviderStatus,
    ProviderUnavailable,
    SendResult,
)


@dataclass(frozen=True)
class SentMessage:
    phone: str
    text: str
    client_ref: str
    provider_message_id: str


@dataclass
class FakeProvider:
    sent: list[SentMessage] = field(default_factory=list)
    unregistered: set[str] = field(default_factory=set)
    state: ProviderState = "connected"
    _failures: list[Exception] = field(default_factory=list, repr=False)
    _counter: int = field(default=0, repr=False)

    def fail_next(self, exc: Exception, times: int = 1) -> None:
        """Make the next ``times`` sends raise ``exc``."""
        self._failures.extend([exc] * times)

    async def send(self, *, phone: str, text: str, client_ref: str) -> SendResult:
        # No await in here, so concurrent tasks cannot interleave: ids stay unique and ordered.
        if self._failures:
            raise self._failures.pop(0)
        if self.state != "connected":
            raise ProviderUnavailable("WhatsApp belum terhubung.")
        if phone in self.unregistered:
            raise NotOnWhatsApp("Nomor tidak terdaftar di WhatsApp.")
        self._counter += 1
        message_id = f"fake-{self._counter}"
        self.sent.append(
            SentMessage(
                phone=phone, text=text, client_ref=client_ref, provider_message_id=message_id
            )
        )
        return SendResult(provider_message_id=message_id)

    async def status(self) -> ProviderStatus:
        return ProviderStatus(state=self.state, qr=None)

    async def logout(self) -> None:
        self.state = "disconnected"
