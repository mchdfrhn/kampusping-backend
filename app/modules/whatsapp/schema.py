from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.messages.schema import ReceiptState

from .provider import ProviderState


class WhatsAppStatusRead(BaseModel):
    state: ProviderState
    qr: str | None
    paused_reason: str | None


class LogoutRead(BaseModel):
    status: Literal["logged_out"] = "logged_out"


class WebhookStatusEvent(BaseModel):
    """``{"type": "status", ...}``: a delivery receipt for a message we sent."""

    model_config = ConfigDict(extra="ignore")

    provider_message_id: str = Field(min_length=1, max_length=100)
    state: ReceiptState


class WebhookAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
